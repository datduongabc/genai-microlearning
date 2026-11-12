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
    return [SimpleNamespace(id=uuid.uuid4(), content=f"passage {i}", source_ref=None) for i in range(n)]


# 450 characters: inside the 420 to 550 a scene is asked for, so a script of these is not topped up.
LONG = "x" * 449 + "."


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

    monkeypatch.setattr(video_script, "retrieve", lambda _db, _course, query, **kw: chunks)
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
        timing.record("llm", 1.0, prompt_tokens=1000, completion_tokens=200)
        return inner(prompt, model, **kw)

    monkeypatch.setattr(video_script, "complete_structured", counted)
    video_script.generate_video_script(db, video.id)
    script_cost = video.cost["script"]
    assert script_cost["prompt_tokens"] == 1000 and script_cost["completion_tokens"] == 200
    assert script_cost["llm_calls"] == 1
    # The gateway does not pass the provider's price back, so no amount is made up.
    assert script_cost["usd"] is None


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
    monkeypatch.setattr(video_script, "retrieve", lambda _db, _course, query, **kw: queries.append((query, kw))
                        or _chunks())
    video_script.generate_video_script(db, video.id)
    query, kwargs = queries[0]
    assert query == "Round Robin" and kwargs["top_k"] is None
    assert kwargs["scope"].document_id == video.document_id


def test_without_a_focus_the_planners_first_concept_is_the_query(monkeypatch):
    db, video, _ = _setup(monkeypatch, _chunks(), _script(), focus=None)
    queries = []
    monkeypatch.setattr(video_script, "retrieve", lambda _db, _course, query, **kw: queries.append(query) or _chunks())
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
