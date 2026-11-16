import json
import uuid
from types import SimpleNamespace

import pytest

from app import timing
from app.generation import claim_evaluation, media_common, video_script
from app.generation.video_script import (
    CHARS_PER_SECOND,
    MAX_ILLUSTRATIONS,
    MAX_SECONDS,
    MIN_SCENES,
    TARGET_SECONDS,
    Scene,
    estimated_seconds,
    normalize_narration,
    prepare_for_render,
)
from app.jobs.queue import LostJob
from app.models import MediaStatus


class FakeDb:
    expire_on_commit = True

    def __init__(self, doc, video):
        self.rows = {"Document": doc, "Video": video}
        self.commits = 0

    def get(self, model, _id):
        return self.rows.get(model.__name__)

    def commit(self):
        self.commits += 1


def _chunks(n=3):
    return [SimpleNamespace(id=uuid.uuid4(), chunk_index=i, content=f"passage {i}", source_ref=None) for i in range(n)]


# 520 characters: inside the 420 to 550 a scene is asked for, and 8 of them reach the 4,000 characters
# of a 4-minute video, so a script of these is not topped up.
LONG = "x" * 519 + "."


def _scene(kind="summary", title="t", bullets=("first point", "second point"), narration=(LONG,), sources=("S1",),
           **fields):
    return {"kind": kind, "title": title,
            "bullets": [{"text": b, "sources": list(sources)} for b in bullets],
            "narration": [{"text": n, "sources": list(sources)} for n in narration], **fields}


def _script(n=MIN_SCENES, **fields):
    return [_scene(title=f"scene {i}", **fields) for i in range(n)]


def _setup(monkeypatch, chunks, scenes, focus="FCFS", verdict=lambda claim: True, alias="gemini-fast", top_up=None):
    doc = SimpleNamespace(id=uuid.uuid4(), course_id=uuid.uuid4(), raw_text="text", planner_concepts=None)
    video = SimpleNamespace(
        id=uuid.uuid4(), document_id=doc.id, course_id=doc.course_id, focus=focus, output_language="vi",
        model_alias=alias, status=MediaStatus.generating, scenes=[], draft_version=1, cost={}, served_models=[])
    db = FakeDb(doc, video)
    calls = []

    def fake_complete(prompt, model, **kw):
        calls.append((prompt, model, kw))
        if model is video_script.ScriptDraft:
            return model(scenes=scenes)
        return model(scenes=top_up or [])

    monkeypatch.setattr(media_common, "retrieve", lambda _db, _course, query, **kw: chunks)
    monkeypatch.setattr(video_script, "complete_structured", fake_complete)
    monkeypatch.setattr(claim_evaluation, "complete_structured", _judge(verdict))
    return db, video, calls


def _judge(verdict, asked=None):
    """The item judge: one call per scene, a verdict for each numbered item."""
    def fake(prompt, response_model, **kwargs):
        payload = json.loads(prompt)
        if asked is not None:
            asked.append(payload)
        return response_model(verdicts=[
            {"number": c["number"], "supported": verdict(c["claim"]), "reason": "the source says one over n"}
            for c in payload["claims"]])
    return fake


def test_more_than_five_illustrations_become_text_scenes(monkeypatch):
    scenes = _script(8, kind="illustration", image_prompt="A Gantt chart with P_1 from 0 to 24")
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    kinds = [s["kind"] for s in video.scenes]
    assert kinds[:MAX_ILLUSTRATIONS] == ["illustration"] * 5
    assert kinds[5:] == ["summary"] * 3
    assert all(s["image_prompt"] is None for s in video.scenes[5:])
    assert all(s["image_prompt"] for s in video.scenes[:5])


def test_a_short_script_is_topped_up_once(monkeypatch):
    short = [_scene(title=f"s{i}", narration=("y" * (300 + i) + ".",)) for i in range(MIN_SCENES)]
    assert sum(len(s["narration"][0]["text"]) for s in short) < 0.8 * TARGET_SECONDS * CHARS_PER_SECOND
    # The model answers for scene 1, the shortest, with a narration that is longer and cited.
    longer = {"index": 1, "narration": [{"text": "z" * 500 + ".", "sources": ["S1"]}]}
    db, video, calls = _setup(monkeypatch, _chunks(), short, top_up=[longer])
    video_script.generate_video_script(db, video.id)
    assert [c[1] for c in calls] == [video_script.ScriptDraft, video_script.TopUpDraft]
    asked = calls[1][0]
    # The shortest scenes are the ones asked for, all of them shorter than a scene should be.
    assert "SCENE 1" in asked and f"SCENE {MIN_SCENES}" in asked
    assert video.scenes[0]["narration"] == "z" * 500 + "."
    # Still short after the one top-up: no second request.
    assert len(calls) == 2


def test_a_long_enough_script_is_not_topped_up(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    assert len(calls) == 1


def test_a_script_under_the_four_minute_floor_is_topped_up_though_every_scene_is_long_enough(monkeypatch):
    # 8 x 450 = 3,600 characters: every scene reaches 420, yet at the voice's measured 15.9 characters a
    # second this is 3:46, under the 4 minutes asked. The old 0.8 x target rule let it through.
    scenes = [_scene(title=f"s{i}", narration=("y" * (449 + i) + ".",)) for i in range(MIN_SCENES)]
    db, video, calls = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert [c[1] for c in calls] == [video_script.ScriptDraft, video_script.TopUpDraft]
    asked = calls[1][0]
    # No scene is short, so the shortest third is lengthened.
    assert "SCENE 1:" in asked and "SCENE 3:" in asked and "SCENE 4:" not in asked


def test_a_scene_under_its_minimum_is_topped_up_though_the_total_is_long_enough(monkeypatch):
    # Run 2: 3,960 characters in all, but two scenes under 420; a slightly shorter draft lands under 4 minutes.
    scenes = _script(9)
    scenes[4] = _scene(title="short", narration=("y" * 403 + ".",))
    db, video, calls = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert [c[1] for c in calls] == [video_script.ScriptDraft, video_script.TopUpDraft]
    asked = calls[1][0]
    assert "SCENE 5: short" in asked and asked.count("SCENE ") == 1


def test_a_top_up_without_sources_keeps_the_old_narration(monkeypatch):
    short = [_scene(title=f"s{i}", narration=("y" * 300 + ".",)) for i in range(MIN_SCENES)]
    uncited = {"index": 1, "narration": [{"text": "z" * 500 + ".", "sources": []}]}
    db, video, _ = _setup(monkeypatch, _chunks(), short, top_up=[uncited])
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["narration"] == "y" * 300 + "."


def test_a_script_over_eight_minutes_is_refused_before_render():
    # 9 scenes of 900 characters read for 9 minutes at 15 characters a second.
    scenes = [Scene(kind="summary", title="t", bullets=["a", "b"], narration="x" * 900, citations=[]).model_dump()
              for _ in range(9)]
    assert estimated_seconds(scenes) == 9 * 900 / CHARS_PER_SECOND > MAX_SECONDS
    problems = prepare_for_render(scenes)
    assert len(problems) == 1
    assert "9,0 phút" in problems[0] and "8 phút" in problems[0]


def test_a_script_within_eight_minutes_has_no_problems():
    scenes = [Scene(kind="summary", title="t", bullets=["a", "b"], narration="x" * 450, citations=[]) for _ in range(9)]
    assert prepare_for_render(scenes) == []


def test_render_is_refused_for_an_empty_narration_or_a_missing_image():
    scenes = [
        Scene(kind="summary", title="t", bullets=["a", "b"], narration="  ", citations=[]),
        Scene(kind="illustration", title="t", bullets=["a", "b"], narration="n", citations=[], image_prompt="a queue"),
        Scene(kind="illustration", title="t", bullets=["a", "b"], narration="n", citations=[], image_prompt="a queue",
              image_key="videos/x/2.png"),
        # The lecturer removed the picture: the scene is rendered with text only.
        Scene(kind="illustration", title="t", bullets=["a", "b"], narration="n", citations=[]),
    ]
    problems = prepare_for_render([s.model_dump() for s in scenes])
    assert len(problems) == 2
    assert problems[0].startswith("Cảnh 1") and problems[1].startswith("Cảnh 2")


def test_latex_and_underscores_do_not_reach_the_voice():
    spoken = normalize_narration("P_1 chạy $\\frac{a}{b}$")
    assert not any(ch in spoken for ch in "_$\\")
    assert spoken == "P1 chạy a phần b"


def test_narration_reads_common_symbols_as_words():
    assert normalize_narration("$T_{wait} \\le 3$ ms [S2]") == "T wait nhỏ hơn hoặc bằng 3 ms"
    assert normalize_narration("$\\frac{1}{n}$", language="en") == "1 over n"
    assert normalize_narration("$O(n\\log n)$, $\\left( n-1 \\right) \\times q$") == "O(n log n), (n-1) nhân q"
    assert normalize_narration("time_quantum $q$") == "time quantum q"


def test_fractions_read_their_parts_cleanly():
    assert normalize_narration("$\\frac{P_{1}}{2}$") == "P1 phần 2"
    assert normalize_narration("$\\frac{x^{2}}{2}$") == "x mũ 2 phần 2"
    assert normalize_narration("$\\frac{W_{1}+W_{2}}{2}$") == "W1+W2 phần 2"
    assert normalize_narration("$\\frac{\\text{tổng}}{n}$") == "tổng phần n"
    assert "frac" not in normalize_narration("$\\frac{\\frac{a_{1}}{b}}{c^{2}}$")


def test_formatting_commands_are_not_read_out():
    assert normalize_narration("$x \\in \\mathbb{R}$, $\\mathcal{O}(n)$") == "x in R, O(n)"
    assert normalize_narration("$a \\quad b \\qquad c\\,d\\;e$") == "a b c d e"
    assert normalize_narration("$P_1, \\dots, P_n$ và $1 \\ldots n$, $\\cdots$") == "P1,..., Pn và 1... n,..."
    assert normalize_narration("$a \\vdots b \\ddots c$") == "a... b... c"


def test_image_prompt_labels_are_written_as_shown(monkeypatch):
    scenes = _script(kind="illustration", image_prompt="Gantt chart: P_1 0-24, P_{2} 24-27, $P_3$ 27-30")
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["image_prompt"] == "Gantt chart: P1 0-24, P2 24-27, P3 27-30"


def test_formula_words_use_text(monkeypatch):
    formula = "Average\\ waiting\\ time = \\frac{0 + 24 + 27}{3}"
    db, video, _ = _setup(monkeypatch, _chunks(), _script(kind="formula", formula=formula))
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["formula"] == "\\text{Average waiting time} = \\frac{0 + 24 + 27}{3}"


def test_formula_words_keep_commands_and_existing_text():
    fix = video_script.text_words_in_formula
    assert fix("\\text{Thời gian chờ} = \\frac{a}{b}") == "\\text{Thời gian chờ} = \\frac{a}{b}"
    assert fix("\\begin{aligned} W &= 0 \\end{aligned}") == "\\begin{aligned} W &= 0 \\end{aligned}"
    assert fix("Waiting = q \\times (n-1)") == "\\text{Waiting} = q \\times (n-1)"
    # The column spec of an array is not a word.
    assert fix("\\begin{array}{llll} a & b \\end{array}") == "\\begin{array}{llll} a & b \\end{array}"


def test_fact_check_flags_a_wrong_bullet(monkeypatch):
    scenes = _script()
    scenes[2]["bullets"][1]["text"] = "each process gets one ninth of the CPU"
    db, video, _ = _setup(monkeypatch, _chunks(), scenes, verdict=lambda claim: "one ninth" not in claim)
    video_script.generate_video_script(db, video.id)
    assert [bool(s["flags"]) for s in video.scenes] == [False, False, True] + [False] * (MIN_SCENES - 3)
    # The flag names the item the judge doubts, not just the scene.
    assert video.scenes[2]["flags"] == ["Ý 2: the source says one over n"]
    # A flag is for the lecturer to read, not a reason to refuse the render.
    assert prepare_for_render(video.scenes) == []


def test_fact_check_flags_a_wrong_narration_sentence_by_its_number(monkeypatch):
    scenes = _script(narration=(LONG, "Each process gets one ninth of the CPU."))
    db, video, _ = _setup(monkeypatch, _chunks(), scenes, verdict=lambda claim: "one ninth" not in claim)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["flags"] == ["Câu 2: the source says one over n"]


def test_sentence_numbers_match_the_stored_narration(monkeypatch):
    # The middle sentence is only markup, so it is not stored; the judge must not count it either.
    scenes = _script(narration=(LONG, "$\\quad$", "Each process gets one ninth of the CPU."))
    db, video, _ = _setup(monkeypatch, _chunks(), scenes, verdict=lambda claim: "one ninth" not in claim)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["narration"].split("\n")[1] == "Each process gets one ninth of the CPU."
    assert video.scenes[0]["flags"] == ["Câu 2: the source says one over n"]


def test_the_judge_reads_each_item_of_a_scene_against_its_passages_in_one_call(monkeypatch):
    chunks = _chunks(3)
    scenes = _script(sources=("S2",))
    scenes[1].update(kind="formula", formula="W = q")
    db, video, _ = _setup(monkeypatch, chunks, scenes)
    asked = []
    monkeypatch.setattr(claim_evaluation, "complete_structured", _judge(lambda claim: True, asked))
    video_script.generate_video_script(db, video.id)
    assert len(asked) == len(video.scenes)
    assert [c["claim"] for c in asked[0]["claims"]] == ["first point", "second point", LONG]
    assert asked[0]["evidence"] == ["passage 1"]
    assert "W = q" in [c["claim"] for c in asked[1]["claims"]]


def test_uncited_model_sentences_are_dropped(monkeypatch):
    chunks = _chunks(3)
    scenes = _script()
    scenes[0]["bullets"].append({"text": "uncited bullet", "sources": []})
    scenes[0]["narration"].append({"text": "An uncited sentence.", "sources": ["S9"]})
    scenes[0]["narration"].append({"text": "A cited one.", "sources": ["[S3]"]})
    # Left with one bullet: too little for a slide.
    scenes[1]["bullets"][1]["sources"] = ["see slide 3"]
    # Left with no narration: nothing to say over the slide.
    scenes[2]["narration"][0]["sources"] = []
    db, video, _ = _setup(monkeypatch, chunks, scenes + [_scene(title="extra"), _scene(title="extra 2")])
    video_script.generate_video_script(db, video.id)
    first = video.scenes[0]
    assert first["bullets"] == ["first point", "second point"]
    assert first["narration"] == f"{LONG}\nA cited one."
    assert first["citations"] == [str(chunks[0].id), str(chunks[2].id)]
    # Sources are kept per bullet and per narration line, which holds one sentence.
    assert first["bullet_citations"] == [[str(chunks[0].id)]] * 2
    assert first["narration_citations"] == [[str(chunks[0].id)], [str(chunks[2].id)]]
    titles = [s["title"] for s in video.scenes]
    assert "scene 1" not in titles and "scene 2" not in titles
    assert len(video.scenes) == MIN_SCENES
    assert all(s["author"] == "model" for s in video.scenes)


def test_a_script_the_model_wrote_too_short_is_saved_with_a_flag(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script(5))
    video_script.generate_video_script(db, video.id)
    assert video.status == MediaStatus.draft and len(video.scenes) == 5
    flag = video.scenes[0]["flags"][-1]
    assert "5 cảnh" in flag and "model chỉ viết" in flag and "bị bỏ" not in flag


def test_a_script_left_short_by_dropped_scenes_says_so(monkeypatch):
    scenes = _script(7)
    scenes[3]["narration"][0]["sources"] = []
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    flag = video.scenes[0]["flags"][-1]
    assert "6 cảnh" in flag and "1 cảnh" in flag and "bị bỏ" in flag


def test_a_script_with_no_cited_scene_fails(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script(sources=()))
    with pytest.raises(ValueError, match="trích nguồn"):
        video_script.generate_video_script(db, video.id)
    assert video.status == MediaStatus.generating


def test_too_many_scenes_keep_the_closing_summary(monkeypatch):
    scenes = _script(12)
    scenes[-1]["title"] = "closing"
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert len(video.scenes) == video_script.MAX_SCENES
    assert video.scenes[-1]["title"] == "closing"


def test_a_kind_without_its_content_becomes_a_text_scene(monkeypatch):
    scenes = _script()
    scenes[1].update(kind="formula", formula=None)
    scenes[2].update(kind="illustration", image_prompt="  ")
    scenes[3].update(kind="summary", formula="x = 1", image_prompt="a queue")
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert [s["kind"] for s in video.scenes[1:4]] == ["summary"] * 3
    assert video.scenes[3]["formula"] is None and video.scenes[3]["image_prompt"] is None


def test_the_saved_scenes_are_valid_scenes_waiting_for_media(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script(kind="illustration", image_prompt="a queue"))
    video_script.generate_video_script(db, video.id)
    for stored in video.scenes:
        scene = Scene.model_validate(stored)
        assert (scene.image_key, scene.voice_key, scene.image_prompt_used, scene.voice_text_used) == (None,) * 4
    assert video.status == MediaStatus.draft and video.draft_version == 1


def test_narration_is_stored_as_the_voice_will_read_it(monkeypatch):
    scenes = _script(narration=(LONG, "Tiến trình P_1 chờ $\\frac{0+24+27}{3}$ ms."))
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["narration"].endswith("Tiến trình P1 chờ 0+24+27 phần 3 ms.")


def test_the_script_cost_is_recorded(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script())
    inner = video_script.complete_structured

    def counted(prompt, model, **kw):
        timing.record("llm", 1.0, prompt_tokens=1000, completion_tokens=200, cost_usd=0.004)
        return inner(prompt, model, **kw)

    monkeypatch.setattr(video_script, "complete_structured", counted)
    video_script.generate_video_script(db, video.id)
    script_cost = video.cost["script"]
    assert script_cost["prompt_tokens"] == 1000 and script_cost["completion_tokens"] == 200
    assert script_cost["llm_calls"] == 1
    assert script_cost["usd"] == 0.004 and script_cost["priced_calls"] == 1


def test_the_prompt_carries_the_rules_of_the_trial(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    prompt, _, kwargs = calls[0]
    system = kwargs["system_instruction"]
    assert "FCFS" in prompt and "SOURCE CHUNK [S1]" in prompt
    assert "trưng dụng" in system and "420" in system and "550" in system
    assert '"P1"' in system and "\\text{" in system
    assert "one over n" in system or "một phần n" in system


def test_retrieval_uses_the_focus_adaptively_within_the_document(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script(), focus="Round Robin")
    queries = []
    monkeypatch.setattr(media_common, "retrieve", lambda _db, _course, query, **kw: queries.append((query, kw))
                        or _chunks())
    video_script.generate_video_script(db, video.id)
    query, kwargs = queries[0]
    assert query == "Round Robin" and kwargs["top_k"] is None
    assert kwargs["scope"].document_id == video.document_id


def test_without_a_focus_the_planners_first_concept_is_the_query(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script(), focus=None)
    queries = []
    monkeypatch.setattr(media_common, "retrieve", lambda _db, _course, query, **kw: queries.append(query) or _chunks())
    concepts = [SimpleNamespace(name="Later", summary="s2", priority=2), SimpleNamespace(name="First", summary="s1",
                                                                                         priority=1)]
    monkeypatch.setattr(media_common, "_get_or_cache_planner", lambda *a, **k: SimpleNamespace(concepts=concepts))
    video_script.generate_video_script(db, video.id)
    assert queries == ["First: s1"]


def test_a_lost_job_writes_nothing(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script())

    def lost():
        raise LostJob("gone")

    with pytest.raises(LostJob):
        video_script.generate_video_script(db, video.id, before_save=lost)
    assert video.status == MediaStatus.generating and video.scenes == []


def test_an_unknown_alias_fails_before_any_model_call(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script(), alias="mystery-1")
    with pytest.raises(ValueError, match="mystery-1"):
        video_script.generate_video_script(db, video.id)
    assert calls == []


def test_the_script_is_written_with_the_videos_model(monkeypatch):
    from app.llm.gateway import requested_model
    db, video, _ = _setup(monkeypatch, _chunks(), _script(), alias="openai-fast")
    used = []
    monkeypatch.setattr(video_script, "complete_structured", lambda prompt, model, **kw: (
        used.append(requested_model()), model(scenes=_script()))[1])
    video_script.generate_video_script(db, video.id)
    assert used == ["openai-fast"]


# --- Length, illustrations and the language of the slide (live run of 2026-11-13: 2:18 of 5 minutes) ---

def test_the_prompt_asks_each_scene_for_its_length_in_sentences_and_characters(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    low, high = video_script.NARRATION_SENTENCES
    assert f"{low} to {high} sentences" in system
    assert f"at least {video_script.NARRATION_MIN_CHARS} characters" in system
    # The whole and what too short means, in minutes the model can picture.
    assert f"{video_script.TARGET_CHARS}" in system and "2 minutes" in system


def test_the_prompt_asks_for_illustrations_wherever_a_picture_helps(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    assert f"3 to {MAX_ILLUSTRATIONS} scenes of kind illustration" in system
    assert f"never more than {MAX_ILLUSTRATIONS}" in system


@pytest.mark.parametrize(("language", "named"), [("vi", "Vietnamese"), ("en", "English")])
def test_slide_text_is_asked_for_in_the_videos_language(monkeypatch, language, named):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video.output_language = language
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    assert f"of a formula are written in {named}" in system
    # Copying the deck's English bullets into a Vietnamese video is what this replaces.
    assert "keep the English term where the source uses it" not in system
    if language == "vi":
        assert "trưng dụng (preemption)" in system


def test_a_focus_naming_two_topics_asks_for_both(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script(), focus="FCFS và Round Robin")
    video_script.generate_video_script(db, video.id)
    prompt, _, kwargs = calls[0]
    assert "TOPICS: FCFS; Round Robin" in prompt
    assert "every topic in TOPICS" in kwargs["system_instruction"]


def test_the_top_up_names_the_length_wanted_and_each_scenes_sources(monkeypatch):
    chunks = _chunks(3)
    short = [_scene(title=f"s{i}", narration=("y" * 300 + ".",), sources=("S2",)) for i in range(MIN_SCENES)]
    db, video, calls = _setup(monkeypatch, chunks, short)
    video_script.generate_video_script(db, video.id)
    asked, _, kwargs = calls[1]
    assert "SCENE 1: s0" in asked and "cites S2" in asked
    system = kwargs["system_instruction"]
    low, high = video_script.NARRATION_SENTENCES
    assert f"at least {video_script.NARRATION_MIN_CHARS} characters" in system
    assert f"{low} to {high} sentences" in system
    assert f"about {video_script.NARRATION_TARGET_CHARS} characters" in system


def test_the_length_at_each_step_is_recorded(monkeypatch):
    short = [_scene(title=f"s{i}", narration=("y" * 300 + ".",)) for i in range(MIN_SCENES)]
    short[1]["narration"].append({"text": "An uncited sentence.", "sources": []})
    longer = {"index": 1, "narration": [{"text": "z" * 500 + ".", "sources": ["S1"]},
                                        {"text": "Uncited too.", "sources": ["S9"]}]}
    db, video, _ = _setup(monkeypatch, _chunks(), short, top_up=[longer])
    video_script.generate_video_script(db, video.id)
    length = video.cost["script"]["length"]
    assert length == {
        "draft_chars": 8 * 301, "after_top_up_chars": 7 * 301 + 501,
        "uncited_sentences_dropped": 1, "top_up_uncited_sentences_dropped": 1,
        "scenes_topped_up": 8, "scenes_lengthened": 1, "chunks": 3,
    }


def test_a_bullet_repeating_the_tables_header_or_rows_is_dropped(monkeypatch):
    scenes = _script()
    scenes[1].update(kind="table", table=[["Process", "Burst Time (ms)"], ["P_1", "24"], ["P_2", "3"]],
                     bullets=[{"text": t, "sources": ["S1"]} for t in
                              ("Process Burst Time (ms)", "P_1 24, P_2 3", "P1 chạy trước", "P2 chờ 24 ms")])
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[1]["bullets"] == ["P1 chạy trước", "P2 chờ 24 ms"]
    assert len(video.scenes[1]["bullet_citations"]) == 2


def test_a_bullet_repeating_the_formula_is_dropped(monkeypatch):
    scenes = _script()
    scenes[1].update(kind="formula", formula="\\text{Average waiting time} = \\frac{0 + 24 + 27}{3} = 17",
                     bullets=[{"text": t, "sources": ["S1"]} for t in
                              ("Thời gian chờ: P1 = 0, P2 = 24, P3 = 27", "Average waiting time = (0 + 24 + 27)/3 = 17",
                               "Trung bình là 17 ms")])
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[1]["bullets"] == ["Thời gian chờ: P1 = 0, P2 = 24, P3 = 27", "Trung bình là 17 ms"]


def test_a_repeating_bullet_stays_when_the_slide_would_be_left_too_bare(monkeypatch):
    scenes = _script()
    scenes[1].update(kind="table", table=[["Process", "Burst"], ["P1", "24"]],
                     bullets=[{"text": t, "sources": ["S1"]} for t in ("Process Burst", "P1 chạy 24 ms")])
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    # Dropping it would cost the scene its slide, and the scene with it.
    assert video.scenes[1]["bullets"] == ["Process Burst", "P1 chạy 24 ms"]


def test_a_word_the_model_wrote_twice_is_said_and_shown_once(monkeypatch):
    scenes = _script(narration=(LONG, "Ngoại trừ việc việc chia sẻ thời gian."),
                     bullets=("chia sẻ sẻ thời gian", "second point"))
    db, video, _ = _setup(monkeypatch, _chunks(), scenes)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[0]["narration"].endswith("Ngoại trừ việc chia sẻ thời gian.")
    assert video.scenes[0]["bullets"][0] == "chia sẻ thời gian"


def test_the_judge_is_asked_to_answer_in_the_videos_language(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script())
    seen = []
    inner = _judge(lambda claim: True)
    monkeypatch.setattr(claim_evaluation, "complete_structured",
                        lambda prompt, model, **kw: seen.append(kw["system_instruction"]) or inner(prompt, model, **kw))
    video_script.generate_video_script(db, video.id)
    assert seen and all("Write `reason` in Vietnamese" in s for s in seen)


# --- Live run 2 (2026-11-16): 2 pictures of the 3 to 5 asked, P4 and P5 invented, English left on slides ---

def test_the_prompt_says_which_scenes_are_pictures_without_forcing_them(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    for kind in ("Gantt chart", "timeline", "ready queue"):
        assert kind in system
    assert "every worked example" in system
    # Not forced: a definition or a list has no picture in it, and fewer is right then.
    assert "write fewer illustrations" in system


def test_the_image_prompt_may_use_only_what_its_chunk_gives(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    assert "only the processes and figures of the chunks the scene cites" in system
    assert "never add a process" in system


def test_an_image_prompt_naming_processes_or_figures_its_sources_lack_is_flagged(monkeypatch):
    chunks = _chunks(2)
    chunks[0].content = "Example of RR with Time Quantum q = 4\nProcess Burst Time\nP_1 24\nP2 3\nP 3 3\n0 4 7 10 30"
    scenes = _script()
    scenes[3].update(kind="illustration",
                     image_prompt="A Gantt chart of P4, P5, P1, P2, P3 with q = 4, marks 0, 4, 7, 10, 12 and 30")
    db, video, _ = _setup(monkeypatch, chunks, scenes)
    video_script.generate_video_script(db, video.id)
    flags = video.scenes[3]["flags"]
    assert flags == ["Mô tả hình có P4, P5, 12, không có trong đoạn nguồn của cảnh: hãy sửa mô tả trước khi vẽ"]


def test_an_image_prompt_true_to_its_sources_is_not_flagged(monkeypatch):
    chunks = _chunks(2)
    chunks[0].content = "Suppose P_1 , P_2 , P_3 burst 24, 3, 3\n0 24 27 30"
    scenes = _script()
    scenes[3].update(kind="illustration", image_prompt="A Gantt chart: P1 from 0 to 24, P2 to 27, P3 to 30")
    db, video, _ = _setup(monkeypatch, chunks, scenes)
    video_script.generate_video_script(db, video.id)
    assert video.scenes[3]["flags"] == []


def test_table_headers_and_formula_words_are_asked_for_in_vietnamese(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    assert "the table's header row and the words in \\text{} of a formula" in system
    assert '"Tiến trình | Thời gian chạy (ms)", not "Process | Burst Time"' in system
    assert "\\text{Thời gian chờ trung bình}" in system
    # The English example was what the model copied into a Vietnamese slide.
    assert "\\text{Average waiting time}" not in system


def test_an_english_video_keeps_english_formula_words(monkeypatch):
    db, video, calls = _setup(monkeypatch, _chunks(), _script())
    video.output_language = "en"
    video_script.generate_video_script(db, video.id)
    system = calls[0][2]["system_instruction"]
    assert "\\text{Average waiting time}" in system
    assert "Thời gian" not in system
