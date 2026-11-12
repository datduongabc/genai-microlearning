"""A scene by scene script for a narrated video, from one document and a focus.

Each scene is one slide shown while a voice reads its narration. The model writes the draft; the
checks below decide what is kept, before the lecturer reviews it:

- Every bullet and every narration sentence the model writes must cite a passage it was given. One
  that cites nothing resolvable is removed, as in a module's content: it cannot be traced or
  checked. A scene left with fewer than 2 bullets has no slide, and one left with no narration has
  nothing to say, so it is removed too. A script left shorter than MIN_SCENES is still saved, with
  a flag on its first scene: the lecturer can add scenes or generate again, and failing it would
  throw away the scenes that are good. Only a script with no scene left at all fails.
- At most MAX_ILLUSTRATIONS scenes are illustrations, since each costs an image model call.
- A script well under TARGET_SECONDS is topped up once: in the trial the model wrote about four
  minutes when asked for five.
- What reaches the voice, the image model and the formula renderer is normalized: no LaTeX or
  underscores in narration, labels in image prompts written as they are shown, words in formulas
  set in \\text{}.
- A model of another family reads each scene against the passages it cites. What it doubts is a
  flag for the lecturer, never a reason to drop the scene or refuse the render.
"""

import math
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import timing
from app.generation import fact_check
from app.generation.common import (
    IGNORE_EMBEDDED_INSTRUCTIONS,
    OutputLanguage,
    format_source_chunks,
    output_language_instruction,
    resolve_source_id,
)
from app.generation.media_common import focus_query, run_generation_step, values_kept_across_commits
from app.llm.gateway import capture_models, complete_structured, using_model
from app.models import Chunk, Document, Job, MediaStatus, Video
from app.retrieval.search import ChunkScope
from app.retrieval.search import retrieve_for_generation as retrieve

TARGET_SECONDS = 300
MAX_SECONDS = 480
# Measured on the three voices, in Vietnamese and in English.
CHARS_PER_SECOND = 15
MAX_ILLUSTRATIONS = 5
MIN_SCENES = 8
MAX_SCENES = 10
NARRATION_MIN_CHARS = 420
NARRATION_MAX_CHARS = 550
TARGET_CHARS = TARGET_SECONDS * CHARS_PER_SECOND
# Below this share of the target the script is topped up. A little under is fine: the voice and the
# pauses between slides add some length, and asking for more every time costs a call every time.
TOP_UP_BELOW = 0.8
MIN_BULLETS, MAX_BULLETS = 2, 4

SceneKind = Literal["title", "illustration", "formula", "code", "table", "summary"]


class Scene(BaseModel):
    """One stored scene. `Video.scenes` holds these as plain dicts (`model_dump`).

    `narration` holds one sentence per line, and `narration_citations[i]` the chunk ids line i is
    taken from, as `bullet_citations[i]` does for `bullets[i]`: the spec keeps sources per sentence
    and per bullet, so that an edited sentence without one can be shown as such. `citations` is
    every chunk the scene cites. The media keys start empty and are filled when the slide is drawn
    and the voice is made."""

    kind: SceneKind
    title: str
    bullets: list[str]
    formula: str | None = None
    code: str | None = None
    table: list[list[str]] | None = None
    image_prompt: str | None = None
    narration: str
    citations: list[str]
    bullet_citations: list[list[str]] = Field(default_factory=list)
    narration_citations: list[list[str]] = Field(default_factory=list)
    author: Literal["model", "lecturer"] = "model"
    flags: list[str] = Field(default_factory=list)
    image_key: str | None = None
    image_prompt_used: str | None = None
    voice_key: str | None = None
    voice_text_used: str | None = None


# What the model returns. Sources sit on each bullet and each sentence, so that one without a
# source can be removed alone, not the whole scene.
class CitedText(BaseModel):
    text: str
    sources: list[str] = Field(description="IDs of the source chunks this is taken from, e.g. S2")


class SceneDraft(BaseModel):
    kind: SceneKind
    title: str
    bullets: list[CitedText] = Field(description="2 to 4 short points shown on the slide")
    formula: str | None = Field(default=None, description="LaTeX, only for kind formula")
    code: str | None = Field(default=None, description="a short code excerpt, only for kind code")
    table: list[list[str]] | None = Field(
        default=None, description="rows, the first row is the header, only for kind table")
    image_prompt: str | None = Field(
        default=None, description="English description of the illustration, only for kind illustration")
    narration: list[CitedText] = Field(description="the sentences the voice says over this slide, in order")


class ScriptDraft(BaseModel):
    # No length bound in the schema: a model that writes 11 scenes would fail the whole call.
    scenes: list[SceneDraft]


class TopUpScene(BaseModel):
    index: int = Field(description="the number of the scene, as given")
    narration: list[CitedText]


class TopUpDraft(BaseModel):
    scenes: list[TopUpScene]


_NARRATION_RULES = f"""Narration:
- Natural speech, as the lecturer would say it in class. Explain worked examples step by step, with their figures.
- {NARRATION_MIN_CHARS} to {NARRATION_MAX_CHARS} characters per scene.
- One sentence per item of `narration`, each with its own `sources`.
- The voice reads the text as written, so read symbols and fractions as the words they mean, never as
  LaTeX or symbols: in Vietnamese "một phần n", "n trừ một nhân q"; in English "one over n",
  "n minus one times q". Write labels as they are said: P1, not P_1.
Terms: in Vietnamese, preemption is "trưng dụng"; keep the English names of algorithms."""

_SCRIPT_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + f"""You write the script of a short narrated lecture video that a student
watches at home before class, about {TARGET_SECONDS // 60} minutes long: about {TARGET_CHARS} characters of narration in all
({TARGET_CHARS - 500} to {TARGET_CHARS + 500}). The video is a list of scenes; each scene is one slide shown while a voice
reads its narration. It is about the FOCUS given.

Use ONLY what the source chunks say: no outside knowledge, nothing the chunks do not state.
Every bullet and every narration sentence lists in `sources` the IDs of the chunks it is taken from
(for example ["S2"]). A bullet or sentence without a source is removed.

Scenes:
- Write {MIN_SCENES} to {MAX_SCENES} scenes. The first is of kind title and introduces the topic; the last is of kind
  summary and recaps it. Use summary for the last scene only.
- At most {MAX_ILLUSTRATIONS} scenes of kind illustration, only where a picture helps to understand a concept
  (for example a Gantt chart or a queue).
- A formula goes in a scene of kind formula, as LaTeX in `formula`; a short code excerpt in kind code; a small table
  of figures in kind table, as rows with the header row first. Take them from the source chunks.
- bullets: {MIN_BULLETS} to {MAX_BULLETS} very short points shown on the slide; keep the English term where the source uses it.

image_prompt, for illustration scenes only, in English:
- Describe one concrete picture, faithful to the source.
- Give every figure and label the picture shows, exactly as in the source: burst times, the time marks of a Gantt
  chart, the order of the processes. Never leave a number for the image model to make up.
- Write each label as it will appear in the picture: "P1", not "P_1" or "$P_1$".
- Flat vector style, white background, no text other than those labels and figures.

formula: words inside a formula go in \\text{{...}}, for example
\\text{{Average waiting time}} = \\frac{{0 + 24 + 27}}{{3}}, so that they are not set in italics like variables.

{_NARRATION_RULES}"""

_TOP_UP_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + f"""The narration of some scenes of a narrated lecture video came out
too short. For each scene given, write its narration again, longer: say more about what its slide shows, explain its
example and its figures, and why it matters. Use ONLY what the source chunks say; every sentence lists in `sources`
the IDs of the chunks it is taken from. Return each scene with the number it was given, as `index`.

{_NARRATION_RULES}"""


@dataclass
class _Draft:
    """A scene while it is checked: each bullet and sentence with the chunks it cites."""

    kind: str
    title: str
    bullets: list[tuple[str, list[Chunk]]]
    narration: list[tuple[str, list[Chunk]]]
    formula: str | None = None
    code: str | None = None
    table: list[list[str]] | None = None
    image_prompt: str | None = None
    flags: list[str] = field(default_factory=list)

    def narration_chars(self) -> int:
        return len(" ".join(text for text, _ in self.narration))

    def chunks(self) -> list[Chunk]:
        cited = [c for _, used in self.bullets + self.narration for c in used]
        return list({c.id: c for c in cited}.values())


_MARKER = re.compile(r"\[\s*S\d+\s*\]", re.IGNORECASE)


def _cited(items: list[CitedText], chunks: list[Chunk]) -> list[tuple[str, list[Chunk]]]:
    kept = []
    for item in items:
        # A model sometimes copies the marker into the text as well; it must not be shown or read.
        text = re.sub(r"\s{2,}", " ", _MARKER.sub("", item.text)).strip()
        resolved = [resolve_source_id(chunks, code) for code in item.sources]
        used = list({c.id: c for c in resolved if c is not None}.values())
        if text and used:
            kept.append((text, used))
    return kept


def _kept_scenes(drafts: list[SceneDraft], chunks: list[Chunk]) -> tuple[list[_Draft], int]:
    """The scenes kept, and how many were removed for want of sources."""
    scenes = []
    dropped = 0
    for d in drafts:
        bullets = _cited(d.bullets, chunks)[:MAX_BULLETS]
        narration = _cited(d.narration, chunks)
        if len(bullets) < MIN_BULLETS or not narration:
            dropped += 1
            continue
        scenes.append(_Draft(kind=d.kind, title=d.title.strip(), bullets=bullets, narration=narration,
                             formula=d.formula, code=d.code, table=d.table, image_prompt=d.image_prompt))
    if len(scenes) > MAX_SCENES:
        # The closing summary is what a cut from the end would lose, so the cut is made before it.
        scenes = scenes[:MAX_SCENES - 1] + scenes[-1:]
    return scenes, dropped


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _settle_kinds(scenes: list[_Draft]) -> None:
    """Each scene keeps only what its kind shows, so the slide renderer never has to choose, and a
    kind whose content is missing becomes a text scene. Only the first MAX_ILLUSTRATIONS
    illustrations keep their picture."""
    illustrations = 0
    for s in scenes:
        missing = {
            "illustration": _blank(s.image_prompt),
            "formula": _blank(s.formula),
            "code": _blank(s.code),
            # A header and at least one row.
            "table": not s.table or len(s.table) < 2,
        }.get(s.kind, False)
        if s.kind == "illustration" and not missing:
            illustrations += 1
            missing = illustrations > MAX_ILLUSTRATIONS
        if missing:
            s.kind = "summary"
        s.image_prompt = s.image_prompt.strip() if s.kind == "illustration" else None
        s.formula = s.formula.strip() if s.kind == "formula" else None
        s.code = s.code if s.kind == "code" else None
        s.table = s.table if s.kind == "table" else None


def _scenes_to_top_up(scenes: list[_Draft]) -> list[int]:
    """Indexes of the scenes to lengthen: the ones shorter than a scene is asked to be, or, when every
    scene reaches that and there are too few of them, the shortest third."""
    by_length = sorted(range(len(scenes)), key=lambda i: scenes[i].narration_chars())
    short = [i for i in by_length if scenes[i].narration_chars() < NARRATION_MIN_CHARS]
    return sorted(short or by_length[:math.ceil(len(scenes) / 3)])


def _top_up(scenes: list[_Draft], query: str, chunks: list[Chunk], language: OutputLanguage) -> None:
    chosen = _scenes_to_top_up(scenes)
    listed = []
    for i in chosen:
        s = scenes[i]
        bullets = "\n".join(f"- {text}" for text, _ in s.bullets)
        narration = " ".join(text for text, _ in s.narration)
        listed.append(f"SCENE {i + 1}: {s.title}\nSlide:\n{bullets}\nNarration now ({s.narration_chars()} characters):"
                      f" {narration}")
    reply = complete_structured(
        f"FOCUS: {query}\n\n" + "\n\n".join(listed) + f"\n\n{format_source_chunks(chunks)}", TopUpDraft,
        system_instruction=_TOP_UP_INSTRUCTION + output_language_instruction(language), temperature=0.3,
    )
    for item in reply.scenes:
        i = item.index - 1
        if i not in chosen:
            continue
        narration = _cited(item.narration, chunks)
        # Same rule as the first draft: what cites nothing is removed, so a reply that came back
        # uncited, or shorter, leaves the scene as it was.
        if len(" ".join(text for text, _ in narration)) > scenes[i].narration_chars():
            scenes[i].narration = narration


# --- What reaches the voice, the image model and the formula renderer ---

_SAID = {
    "vi": {"frac": "{} phần {}", "sqrt": "căn {}", "power": " mũ {}", "le": "nhỏ hơn hoặc bằng",
           "ge": "lớn hơn hoặc bằng", "ne": "khác", "times": "nhân", "approx": "xấp xỉ", "to": "đến",
           "sum": "tổng", "infty": "vô cùng", "lt": "nhỏ hơn", "gt": "lớn hơn"},
    "en": {"frac": "{} over {}", "sqrt": "square root of {}", "power": " to the power of {}",
           "le": "less than or equal to", "ge": "greater than or equal to", "ne": "not equal to", "times": "times",
           "approx": "approximately", "to": "to", "sum": "sum of", "infty": "infinity", "lt": "less than",
           "gt": "greater than"},
}
_SYMBOL_COMMANDS = {"le": "le", "leq": "le", "ge": "ge", "geq": "ge", "ne": "ne", "neq": "ne", "times": "times",
                    "cdot": "times", "approx": "approx", "to": "to", "rightarrow": "to", "sum": "sum", "infty": "infty",
                    "lt": "lt", "gt": "gt"}
# Commands whose argument is said as it is: they only change how it looks.
_TEXT_COMMANDS = "text|textbf|textit|mathrm|mathbf|mathit|mathbb|mathcal|operatorname"
# Any other command is said by its name, which is how Greek letters (\alpha) and functions (\log)
# are read; these are layout or ellipses instead.
_LAYOUT_COMMANDS = {"left": " ", "right": " ", "quad": " ", "qquad": " ", "dots": "...", "ldots": "...",
                    "cdots": "...", "vdots": "...", "ddots": "..."}
_LETTER = r"[^\W_]"


def _subscripts_joined(text: str) -> str:
    """P_1 and P_{1} are shown as P1 on the slide, so that is how they are said and drawn. A longer
    subscript is a word of its own: T_{wait} is "T wait", time_quantum is "time quantum"."""
    def join(m: re.Match) -> str:
        sub = m[1] if m[1] is not None else m[2]
        return sub if re.fullmatch(r"\d+|[^\W_]", sub.strip()) else f" {sub.strip()}"
    return re.sub(rf"(?<={_LETTER})_(?:\{{([^{{}}]*)\}}|({_LETTER}+))", join, text)


def normalize_narration(text: str, language: str = "vi") -> str:
    """The narration as the voice must receive it: no LaTeX, `$`, `\\` or `_`, which a voice reads
    out as symbols or skips. The prompt asks for symbols in words; this catches what slips through.
    Lines are kept, since each holds one sentence."""
    said = _SAID.get(language, _SAID["en"])
    lines = []
    for line in text.split("\n"):
        line = _MARKER.sub("", line)
        # Innermost group first, so the parts of a fraction are read before the fraction itself:
        # \frac{P_{1}}{2} needs its subscript joined before the fraction pattern can match it.
        while True:
            replaced = re.sub(rf"\\(?:{_TEXT_COMMANDS})\s*\{{([^{{}}]*)\}}", r"\1", line)
            replaced = re.sub(r"\^\s*\{([^{}]*)\}|\^\s*(\w)",
                              lambda m: said["power"].format((m[1] if m[1] is not None else m[2]).strip()), replaced)
            replaced = _subscripts_joined(replaced)
            replaced = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
                              lambda m: said["frac"].format(m[1].strip(), m[2].strip()), replaced)
            replaced = re.sub(r"\\sqrt\s*\{([^{}]*)\}", lambda m: said["sqrt"].format(m[1].strip()), replaced)
            if replaced == line:
                break
            line = replaced
        # Spacing commands (\, \; \quad) are layout, not words.
        line = re.sub(r"\\[,;:! ]", " ", line)
        line = re.sub(r"\\([A-Za-z]+)", lambda m: f" {said[_SYMBOL_COMMANDS[m[1]]]} " if m[1] in _SYMBOL_COMMANDS
                      else _LAYOUT_COMMANDS.get(m[1], f" {m[1]} "), line)
        line = re.sub(r"[_$\\{}]", " ", line)
        line = re.sub(r"[ \t]+", " ", line)
        line = re.sub(r" +([.,;:!?)])", r"\1", line)
        line = re.sub(r"\( +", "(", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def labels_as_shown(image_prompt: str) -> str:
    """The image model draws a label as it is written, so "P_1" would be drawn with its underscore."""
    return re.sub(r"[ \t]+", " ", _subscripts_joined(image_prompt.replace("$", ""))).strip()


_FORMULA_TOKEN = re.compile(
    # The group after \begin{array} is a column spec such as {llll}, not a word.
    rf"(?P<keep>\\begin\s*\{{[^{{}}]*\}}(?:\s*\{{[^{{}}]*\}})?|\\(?:{_TEXT_COMMANDS}|end)\s*\{{[^{{}}]*\}}|\\[A-Za-z]+)"
    r"|(?P<words>[^\W\d_]+(?:\\ [^\W\d_]+)+|[^\W\d_]{4,})"
)


def text_words_in_formula(formula: str) -> str:
    """Words in a formula are set in \\text{}, or LaTeX prints each letter in italics as a variable.
    Words a model joined with "\\ " become one \\text{}; a bare word of four letters or more is a
    word, not a product of variables. Commands and what is already in \\text{} are left alone."""
    def fix(m: re.Match) -> str:
        if m["keep"]:
            return m["keep"]
        return "\\text{" + m["words"].replace("\\ ", " ") + "}"
    return _FORMULA_TOKEN.sub(fix, formula)


# --- Length and readiness ---

def _as_scene(scene: Scene | dict) -> Scene:
    return scene if isinstance(scene, Scene) else Scene.model_validate(scene)


def estimated_seconds(scenes: Sequence[Scene | dict]) -> float:
    return sum(len(_as_scene(s).narration) for s in scenes) / CHARS_PER_SECOND


def prepare_for_render(scenes: Sequence[Scene | dict]) -> list[str]:
    """What stops the script from being rendered, for the lecturer to fix. Fact check flags are not
    among them: they are for the lecturer to read, and the lecturer decides."""
    parsed = [_as_scene(s) for s in scenes]
    if not parsed:
        return ["Video chưa có cảnh nào"]
    problems = []
    seconds = estimated_seconds(parsed)
    if seconds > MAX_SECONDS:
        minutes = f"{seconds / 60:.1f}".replace(".", ",")
        problems.append(f"Video ước tính {minutes} phút, quá giới hạn {MAX_SECONDS // 60} phút: hãy rút ngắn lời thoại")
    for number, s in enumerate(parsed, 1):
        if not s.narration.strip():
            problems.append(f"Cảnh {number}: chưa có lời thoại")
        if s.kind == "illustration" and not _blank(s.image_prompt) and s.image_key is None:
            problems.append(f"Cảnh {number}: chưa vẽ hình minh họa")
    return problems


# --- The job ---

def _spoken(s: _Draft, language: str) -> list[tuple[str, list[Chunk]]]:
    """The narration sentences as they are stored and read, without those that were only markup.
    Both the stored lines and the judge's "Câu n" come from this list, so the numbers agree."""
    spoken = [(normalize_narration(text, language).replace("\n", " "), used) for text, used in s.narration]
    return [(text, used) for text, used in spoken if text]


def _items(s: _Draft, language: str) -> list[tuple[str, str]]:
    """What the judge checks in a scene, each named as the lecturer will find it: bullets as "Ý n",
    narration sentences as "Câu n", and the formula, code or table the slide shows."""
    items = [(f"Ý {n}", text) for n, (text, _) in enumerate(s.bullets, 1)]
    if s.formula:
        items.append(("Công thức", s.formula))
    if s.code:
        items.append(("Code", s.code))
    if s.table:
        items.append(("Bảng", "\n".join(" | ".join(row) for row in s.table)))
    items.extend((f"Câu {n}", text) for n, (text, _) in enumerate(_spoken(s, language), 1))
    return items


def _stored(s: _Draft, language: str) -> dict:
    spoken = _spoken(s, language)
    narration = [text for text, _ in spoken]
    narration_citations = [[str(c.id) for c in used] for _, used in spoken]
    return Scene(
        kind=s.kind, title=s.title, bullets=[text for text, _ in s.bullets],
        formula=text_words_in_formula(s.formula) if s.formula else None, code=s.code, table=s.table,
        image_prompt=labels_as_shown(s.image_prompt) if s.image_prompt else None,
        narration="\n".join(narration), citations=[str(c.id) for c in s.chunks()],
        bullet_citations=[[str(c.id) for c in used] for _, used in s.bullets],
        narration_citations=narration_citations, author="model", flags=s.flags,
    ).model_dump()


def _script_cost(spans: list[dict]) -> dict:
    """The gateway does not pass the provider's price back through complete_structured, so the
    amount is left empty rather than estimated; the tokens of every call, the judge's included, are
    what is known."""
    calls = [s for s in spans if s["kind"] == "llm"]
    return {"usd": None, "llm_calls": len(calls),
            "prompt_tokens": sum(s.get("prompt_tokens") or 0 for s in calls),
            "completion_tokens": sum(s.get("completion_tokens") or 0 for s in calls)}


def generate_video_script(db: Session, video_id: uuid.UUID, before_save: Callable[[], None] | None = None) -> None:
    """`before_save` runs right before the script is written, in the transaction that writes it:
    the job queue checks there that its worker still holds the job (app/jobs/queue.py, hold)."""
    with values_kept_across_commits(db), timing.capture_timings() as spans, capture_models() as served:
        video = db.get(Video, video_id)
        if video is None:
            raise ValueError(f"Video {video_id} not found")
        doc = db.get(Document, video.document_id)
        if doc is None or not doc.raw_text:
            raise ValueError("Document chưa được parse (raw_text rỗng)")
        language: OutputLanguage = video.output_language  # type: ignore[assignment]

        # Fail on an alias that has no judge of another family before any paid call.
        fact_check.judge_alias(video.model_alias)

        with using_model(video.model_alias):
            query = focus_query(db, doc, video)
            db.commit()
            chunks = retrieve(db, doc.course_id, query, top_k=None, min_k=8, scope=ChunkScope(doc.id))
            db.commit()
            draft = complete_structured(
                f"FOCUS: {query}\n\n{format_source_chunks(chunks)}", ScriptDraft,
                system_instruction=_SCRIPT_INSTRUCTION + output_language_instruction(language), temperature=0.3,
            )
            scenes, dropped = _kept_scenes(draft.scenes, chunks)
            if not scenes:
                raise ValueError("Model không viết được cảnh nào có trích nguồn từ tài liệu; hãy thử lại "
                                 "hoặc đổi trọng tâm")
            _settle_kinds(scenes)
            if sum(s.narration_chars() for s in scenes) < TOP_UP_BELOW * TARGET_CHARS:
                _top_up(scenes, query, chunks, language)

        verdicts = fact_check.check_items_against_sources(
            [(_items(s, language), [c.content for c in s.chunks()]) for s in scenes], video.model_alias)
        for s, item_flags in zip(scenes, verdicts, strict=True):
            s.flags.extend(flag for flag in item_flags if flag)
        if len(scenes) < MIN_SCENES:
            # Why it is short tells the lecturer what to do: sources missing suggests another focus.
            why = (f"{dropped} cảnh không trích được nguồn đã bị bỏ" if dropped
                   else "model chỉ viết từng ấy cảnh")
            scenes[0].flags.append(
                f"Lời thoại chỉ có {len(scenes)} cảnh (nên có {MIN_SCENES} đến {MAX_SCENES}) vì {why}: "
                "hãy thêm cảnh hoặc sinh lại")
        stored = [_stored(s, language) for s in scenes]

        if before_save is not None:
            before_save()
        video.scenes = stored
        video.status = MediaStatus.draft
        video.draft_version = 1
        video.error = None
        video.cost = {**(video.cost or {}), "script": _script_cost(spans)}
        video.served_models = list(served)
        db.commit()


def run_video_script_step(job: Job) -> None:
    run_generation_step(job, "video_id", generate_video_script)
