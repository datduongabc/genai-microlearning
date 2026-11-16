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
- A script under MIN_CHARS, or with a scene under NARRATION_MIN_CHARS, is topped up once: in the
  trial the model wrote about four minutes when asked for five, in the first live run 2:18, and in
  the second 4:10, just above the minimum. The top-up asks for a stated length
  per scene, and what each step wrote is recorded with the script's cost, so a short video can be
  traced to the draft, the top-up or the citations.
- A bullet that only repeats the slide's table or formula is removed, as long as two remain.
- What reaches the voice, the image model and the formula renderer is normalized: no LaTeX or
  underscores in narration, labels in image prompts written as they are shown, words in formulas
  set in \\text{}.
- A model of another family reads each scene against the passages it cites. What it doubts is a
  flag for the lecturer, never a reason to drop the scene or refuse the render.
"""

import logging
import math
import re
import unicodedata
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
    source_id,
)
from app.generation.media_common import (
    focus_passages,
    llm_cost,
    run_generation_step,
    term_rules,
    topics_line,
    values_kept_across_commits,
    without_doubled_words,
)
from app.llm.gateway import capture_models, complete_structured, using_model
from app.models import Chunk, Document, Job, MediaStatus, Video

logger = logging.getLogger(__name__)

TARGET_SECONDS = 300
MAX_SECONDS = 480
# Measured on the three voices, in Vietnamese and in English.
CHARS_PER_SECOND = 15
MAX_ILLUSTRATIONS = 5
MIN_SCENES = 8
MAX_SCENES = 10
# Asked for: the trial asked for 9 or 10 and reached about 4 minutes; asking for 8 to 10 got the
# minimum, 8, in the live run. MIN_SCENES stays what a script is flagged under.
ASKED_SCENES = (9, MAX_SCENES)
NARRATION_MIN_CHARS = 420
NARRATION_MAX_CHARS = 550
NARRATION_TARGET_CHARS = 480
# A model keeps a count of sentences far better than a count of characters: the live run's scenes
# were asked for 420 to 550 characters and came out at 2 or 3 sentences, 211 to 331 characters.
NARRATION_SENTENCES = (5, 7)
TARGET_CHARS = TARGET_SECONDS * CHARS_PER_SECOND
# The least the model is asked for, and below it the script is topped up. The 4-minute minimum at 15
# characters a second would be 3,600, but the voice read 15.9 a second in live run 2, which made
# 3,960 characters 4:10, ten seconds above the minimum; 4,000 is still 4:11 at that rate.
MIN_CHARS = TARGET_CHARS - 500
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
    # The voice model that read it: another voice for the video means every scene is read again.
    voice_alias_used: str | None = None


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


_LANGUAGE_NAMES = {"vi": "Vietnamese", "en": "English"}
_LOW, _HIGH = NARRATION_SENTENCES

_NARRATION_RULES = f"""Narration:
- Natural speech, as the lecturer would say it in class.
- Every scene's narration has {_LOW} to {_HIGH} sentences and at least {NARRATION_MIN_CHARS} characters
  ({NARRATION_MIN_CHARS} to {NARRATION_MAX_CHARS}, about {NARRATION_TARGET_CHARS}), whatever its kind, the title and the summary
  scenes included. A scene of 2 or 3 sentences is far too short.
- Say more than the slide: explain what each bullet means and why it matters; walk through a worked example
  step by step with its figures (each process, its burst time, when it starts and ends, how long it waits);
  link the scene to the one before. Explaining, restating and walking through what a chunk states is not
  outside knowledge: cite that chunk. Many sentences may cite the same chunk.
- One sentence per item of `narration`, each with its own `sources`.
- The voice reads the text as written, so read symbols and fractions as the words they mean, never as
  LaTeX or symbols: in Vietnamese "một phần n", "n trừ một nhân q"; in English "one over n",
  "n minus one times q". Write labels as they are said: P1, not P_1."""


# The example the model copies: live run 2's Vietnamese formulas kept the English one word for word.
_FORMULA_EXAMPLE = {
    "vi": "\\text{Thời gian chờ trung bình} = \\frac{0 + 24 + 27}{3} = 17, "
          "\\text{Thời gian chuyển ngữ cảnh} < 10\\,\\mu s \\ll q.",
    "en": "\\text{Average waiting time} = \\frac{0 + 24 + 27}{3} = 17.",
}


def _slide_language_rule(language: OutputLanguage) -> str:
    """The first live run's Vietnamese video showed the deck's English bullets as they were; the second
    still copied one table header ("Process | Burst Time") and the formulas' \\text{} words."""
    rule = (f"Titles, bullets, table cells, the table's header row and the words in \\text{{}} of a formula are written in "
            f"{_LANGUAGE_NAMES[language]}, like the narration; never copy the source's English bullets, table header or "
            "formula words as they are.")
    if language == "vi":
        rule += (' A technical term may follow in English in brackets, for example "trưng dụng (preemption)", '
                 '"hàng đợi sẵn sàng (ready queue)"; the names of algorithms stay as they are (FCFS, Round Robin). '
                 'A table header reads, for example, "Tiến trình | Thời gian chạy (ms)", not "Process | Burst Time".')
    return rule


def _script_instruction(language: OutputLanguage) -> str:
    return IGNORE_EMBEDDED_INSTRUCTIONS + f"""You write the script of a narrated lecture video that a student watches
at home before class. It must last about {TARGET_SECONDS // 60} minutes (4 to 6). The voice reads about {CHARS_PER_SECOND} characters a
second, so the narration of all scenes together is about {TARGET_CHARS} characters ({MIN_CHARS} to {TARGET_CHARS + 500}).
Too short is the usual failure: 2000 characters would be a video of only 2 minutes. The video is a list of scenes; each
scene is one slide shown while a voice reads its narration.

It is about the FOCUS given. When TOPICS lists several, cover every topic in TOPICS, each in scenes of its own and about
as much on each. A chunk about something else (another algorithm, an exercise, the chapter's summary) is only context:
write no scene about it.

Use ONLY what the source chunks say: no outside knowledge, nothing the chunks do not state.
Every bullet and every narration sentence lists in `sources` the IDs of the chunks it is taken from
(for example ["S2"]). A bullet or sentence without a source is removed.

Scenes:
- Write {ASKED_SCENES[0]} to {ASKED_SCENES[1]} scenes. The first is of kind title and introduces the topic; the last is of kind
  summary and recaps it. Use summary for the last scene only.
- Use 3 to {MAX_ILLUSTRATIONS} scenes of kind illustration, never more than {MAX_ILLUSTRATIONS}. These are the scenes that are pictures:
  - the schedule of a worked example: the Gantt chart of the order its processes run in, with its time marks
    (FCFS, SJF, Round Robin with its q). Draw every worked example the chunks give, each in its own scene;
  - a process timeline: when each process arrives, waits, runs and finishes;
  - a queue: the ready queue, or the order processes are taken from it and put back at its end;
  - how a parameter changes the result, such as the same processes run with a small and a large time quantum.
  A scene that walks through such a schedule is an illustration: its figures go in the picture and its narration (the
  burst times alone may be a table scene before it). A definition, a list of criteria or a comparison has no picture in it: when the chunks hold fewer than 3
  of the things above, write fewer illustrations rather than a picture that shows nothing.
- A formula goes in a scene of kind formula, as LaTeX in `formula`; a short code excerpt in kind code; a small table
  of figures in kind table, as rows with the header row first. Take them from the source chunks.
- bullets: {MIN_BULLETS} to {MAX_BULLETS} very short points shown on the slide. They add to what the slide's table, formula or code
  shows: never repeat the table's header or its rows, or the formula, as a bullet.
- {_slide_language_rule(language)}

image_prompt, for illustration scenes only, in English:
- Describe one concrete picture, faithful to the source. Use only the processes and figures of the chunks the scene cites:
  never add a process or a figure they do not give (no P4 or P5 when the example has P1, P2 and P3).
- Give every figure and label the picture shows, exactly as in the source: burst times, the time marks of a Gantt
  chart, the order of the processes. Never leave a number for the image model to make up.
- Write each label as it will appear in the picture: "P1", not "P_1" or "$P_1$".
- Flat vector style, white background, no text other than those labels and figures.

formula: words inside a formula go in \\text{{...}}, so that they are not set in italics like variables, and they are
written in {_LANGUAGE_NAMES[language]}: {_FORMULA_EXAMPLE[language]}

{_NARRATION_RULES}
{term_rules(language)}"""


_TOP_UP_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + f"""The narration of some scenes of a narrated lecture video came out
too short: the video must last about {TARGET_SECONDS // 60} minutes and at least 4. For each scene given, write its narration
again so that it has {_LOW} to {_HIGH} sentences and at least {NARRATION_MIN_CHARS} characters, about {NARRATION_TARGET_CHARS} characters. Keep what it
says, and add to it: explain what its slide shows and why it matters, walk through its example and its figures step by
step. Use ONLY what the source chunks say. Every sentence lists in `sources` the IDs of the chunks it is taken from,
such as the chunks the scene already cites: a sentence without a source is removed, and a new narration that ends up no
longer than the old one is thrown away. Return each scene with the number it was given, as `index`.

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


def _cited(items: list[CitedText], chunks: list[Chunk]) -> tuple[list[tuple[str, list[Chunk]]], int]:
    """The items that cite a passage given, and how many were removed for citing none."""
    kept = []
    dropped = 0
    for item in items:
        # A model sometimes copies the marker into the text as well; it must not be shown or read.
        text = without_doubled_words(re.sub(r"\s{2,}", " ", _MARKER.sub("", item.text)).strip())
        resolved = [resolve_source_id(chunks, code) for code in item.sources]
        used = list({c.id: c for c in resolved if c is not None}.values())
        if text and used:
            kept.append((text, used))
        elif text:
            dropped += 1
    return kept, dropped


def _kept_scenes(drafts: list[SceneDraft], chunks: list[Chunk]) -> tuple[list[_Draft], int, int]:
    """The scenes kept, how many were removed for want of sources, and how many narration
    sentences were removed for citing nothing."""
    scenes = []
    dropped = 0
    uncited_sentences = 0
    for d in drafts:
        bullets, _ = _cited(d.bullets, chunks)
        narration, uncited = _cited(d.narration, chunks)
        uncited_sentences += uncited
        if len(bullets) < MIN_BULLETS or not narration:
            dropped += 1
            continue
        scenes.append(_Draft(kind=d.kind, title=d.title.strip(), bullets=bullets, narration=narration,
                             formula=d.formula, code=d.code, table=d.table, image_prompt=d.image_prompt))
    if len(scenes) > MAX_SCENES:
        # The closing summary is what a cut from the end would lose, so the cut is made before it.
        scenes = scenes[:MAX_SCENES - 1] + scenes[-1:]
    return scenes, dropped, uncited_sentences


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
        s.bullets = _without_repeats(s)[:MAX_BULLETS]


def _words(text: str) -> set[str]:
    """The words and figures of a text, however the model wrote them: P_1, P₁ and P1 are one."""
    flat = _subscripts_joined(unicodedata.normalize("NFKC", text))
    return set(re.findall(r"[^\W_]+", re.sub(r"\\[A-Za-z]+", " ", flat).casefold()))


def _without_repeats(s: _Draft) -> list[tuple[str, list[Chunk]]]:
    """The bullets without those that only repeat the slide's table or formula, which the slide
    already shows: the live run turned a table's header into a bullet above the same table. They
    stay when removing them would leave too few bullets, which would cost the scene its slide."""
    shown = set()
    if s.table:
        shown = _words(" ".join(cell for row in s.table for cell in row))
    elif s.formula:
        shown = _words(s.formula)
    if not shown:
        return s.bullets
    fresh = [(text, used) for text, used in s.bullets if not _words(text) <= shown]
    return fresh if len(fresh) >= MIN_BULLETS else s.bullets


def _needs_top_up(scenes: list[_Draft]) -> bool:
    """Under the floor in all, or any scene under what a scene is asked for. Live run 2 passed the old
    rule (0.8 x target) at 3,960 characters with two scenes under 420, and came out 10 s above 4
    minutes: a slightly shorter draft would have skipped the top-up and been under."""
    return _total_chars(scenes) < MIN_CHARS or any(s.narration_chars() < NARRATION_MIN_CHARS for s in scenes)


# A process label as a slide or its PDF text writes it: P1, P 1, P_1, P_{1}.
_PROCESS = re.compile(r"\bP\s?_?\{?(\d+)\}?")
_FIGURE = re.compile(r"\d+(?:[.,]\d+)?")


def _invented_in_picture(s: _Draft) -> list[str]:
    """The processes and figures the image prompt names that no chunk the scene cites has. Live run 2's
    Round Robin picture showed P4 and P5, which exist nowhere in the source: the script model wrote
    them into the prompt, and the image model drew what it was told. Flagged, not removed: the
    lecturer fixes the description before paying for the picture."""
    if not s.image_prompt:
        return []
    source = " ".join(c.content for c in s.chunks())
    labels = {f"P{n}" for n in _PROCESS.findall(source)}
    figures = set(_FIGURE.findall(source))
    asked = labels_as_shown(s.image_prompt)
    named = [f"P{n}" for n in _PROCESS.findall(asked)]
    shown = _FIGURE.findall(_PROCESS.sub(" ", asked))
    missing = [label for label in named if label not in labels] + [f for f in shown if f not in figures]
    return list(dict.fromkeys(missing))


def _scenes_to_top_up(scenes: list[_Draft]) -> list[int]:
    """Indexes of the scenes to lengthen: the ones shorter than a scene is asked to be, or, when every
    scene reaches that and there are too few of them, the shortest third."""
    by_length = sorted(range(len(scenes)), key=lambda i: scenes[i].narration_chars())
    short = [i for i in by_length if scenes[i].narration_chars() < NARRATION_MIN_CHARS]
    return sorted(short or by_length[:math.ceil(len(scenes) / 3)])


@dataclass
class _TopUpResult:
    chosen: int = 0
    lengthened: int = 0
    uncited_sentences: int = 0


def _top_up(scenes: list[_Draft], query: str, chunks: list[Chunk], language: OutputLanguage) -> _TopUpResult:
    chosen = _scenes_to_top_up(scenes)
    ids = {c.id: source_id(n) for n, c in enumerate(chunks)}
    listed = []
    for i in chosen:
        s = scenes[i]
        bullets = "\n".join(f"- {text}" for text, _ in s.bullets)
        narration = " ".join(text for text, _ in s.narration)
        # The IDs it already cites, so the longer narration has a source at hand for every sentence.
        cites = ", ".join(ids[c.id] for c in s.chunks() if c.id in ids)
        listed.append(f"SCENE {i + 1}: {s.title}\nThis scene cites {cites}.\nSlide:\n{bullets}\n"
                      f"Narration now ({s.narration_chars()} characters, {len(s.narration)} sentences; wanted: at least "
                      f"{NARRATION_MIN_CHARS} characters, {_LOW} to {_HIGH} sentences): {narration}")
    reply = complete_structured(
        f"FOCUS: {query}\n\n" + "\n\n".join(listed) + f"\n\n{format_source_chunks(chunks)}", TopUpDraft,
        system_instruction=_TOP_UP_INSTRUCTION + f"\n{term_rules(language)}" + output_language_instruction(language),
        temperature=0.3,
    )
    result = _TopUpResult(chosen=len(chosen))
    for item in reply.scenes:
        i = item.index - 1
        if i not in chosen:
            continue
        narration, uncited = _cited(item.narration, chunks)
        result.uncited_sentences += uncited
        # Same rule as the first draft: what cites nothing is removed, so a reply that came back
        # uncited, or shorter, leaves the scene as it was.
        if len(" ".join(text for text, _ in narration)) > scenes[i].narration_chars():
            scenes[i].narration = narration
            result.lengthened += 1
    return result


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


def _script_cost(spans: list[dict], length: dict) -> dict:
    """The script, top-up and fact-check calls, priced by the gateway (see llm_cost); the judge's
    tokens are in as well. `length` is what each step wrote: the live run's short video could not be
    traced to the draft, the top-up or the citations, since none of it was kept."""
    cost = llm_cost(spans)
    cost["llm_calls"] = cost.pop("calls")
    return {**cost, "length": length}


def _total_chars(scenes: list[_Draft]) -> int:
    return sum(s.narration_chars() for s in scenes)


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
            found = focus_passages(db, doc, video)
            query, chunks = found.query, found.chunks
            draft = complete_structured(
                f"FOCUS: {query}\n{topics_line(found)}\n{format_source_chunks(chunks)}", ScriptDraft,
                system_instruction=_script_instruction(language) + output_language_instruction(language),
                temperature=0.3,
            )
            scenes, dropped, uncited = _kept_scenes(draft.scenes, chunks)
            if not scenes:
                raise ValueError("Model không viết được cảnh nào có trích nguồn từ tài liệu; hãy thử lại "
                                 "hoặc đổi trọng tâm")
            _settle_kinds(scenes)
            draft_chars = _total_chars(scenes)
            topped = _TopUpResult()
            top_up_failed = False
            if _needs_top_up(scenes):
                # Optional, after the paid draft: a malformed reply would fail the video, and an outage
                # would requeue the job to pay for the draft again. A short draft is kept, as the
                # module docstring says, and the lecturer can lengthen it in the editor.
                try:
                    topped = _top_up(scenes, query, chunks, language)
                except Exception:
                    logger.warning(f"[video_script] video {video_id}: the top-up failed, the draft is kept as it is",
                                   exc_info=True)
                    top_up_failed = True
        length = {"draft_chars": draft_chars, "after_top_up_chars": _total_chars(scenes),
                  "uncited_sentences_dropped": uncited, "top_up_uncited_sentences_dropped": topped.uncited_sentences,
                  "scenes_topped_up": topped.chosen, "scenes_lengthened": topped.lengthened, "chunks": len(chunks),
                  "top_up_failed": top_up_failed}

        for s in scenes:
            invented = _invented_in_picture(s)
            if invented:
                s.flags.append(f"Mô tả hình có {', '.join(invented)}, không có trong đoạn nguồn của cảnh: "
                               "hãy sửa mô tả trước khi vẽ")
        verdicts = fact_check.check_items_against_sources(
            [(_items(s, language), [c.content for c in s.chunks()]) for s in scenes], video.model_alias, language)
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
        video.cost = {**(video.cost or {}), "script": _script_cost(spans, length)}
        video.served_models = list(served)
        db.commit()


def run_video_script_step(job: Job) -> None:
    run_generation_step(job, "video_id", generate_video_script)
