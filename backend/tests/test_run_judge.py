"""Chạy LLM chấm trên một thư mục gói nghiên cứu: mỗi gói ra đúng một dòng kết quả, và từ chối trước
khi tốn lượt gọi nào khi mô hình chấm cùng hãng với mô hình đã sinh gói, vì mô hình chấm ưu ái văn
của chính họ nhà mình.

Mô hình chấm và danh sách model của gateway đều là hàm giả, không gọi mạng."""

import json

import pytest

from app.generation.evaluation import (
    DimensionScore,
    ExampleInput,
    ModuleContentInput,
    ModuleEvaluation,
    QuizQuestionInput,
    SlideInput,
)
from app.generation.study_packets import StudyFile
from scripts import run_judge

GATEWAY = [
    {"model_name": "gemini-fast", "litellm_params": {"model": "gemini/gemini-flash-lite-latest"}},
    {"model_name": "claude-pro", "litellm_params": {"model": "anthropic/claude-sonnet-5"}},
    {"model_name": "groq-pro", "litellm_params": {"model": "groq/openai/gpt-oss-120b"}},
    {"model_name": "openai-fast", "litellm_params": {"model": "openai/gpt-4o-mini"}},
]


def _packet() -> ModuleContentInput:
    question = QuizQuestionInput(stem="Hỏi", choices=["A", "B", "C", "D"], correct_index=1, explanation="Vì B")
    return ModuleContentInput(
        anon_id="", topic="Chủ đề", bloom_level="understand", learning_objective="Giải thích X.",
        sub_objectives=["x"], content_markdown="## X", slides=[SlideInput(title="T", bullets=["b"])],
        examples=[ExampleInput(context="c", analysis="a")], quiz=[question], practice=question,
        source_chunks=["Nguồn."],
    )


def _write_pairs(folder, pairs: int, generator: str = "gemini-fast") -> None:
    for n in range(pairs):
        for group in ("rag_llm", "llm_no_rag"):
            study = StudyFile(pair_id=f"cap-{n}", group=group, generator_model=generator, packet=_packet())
            (folder / f"cap-{n}-{group}.json").write_text(study.model_dump_json(indent=2), encoding="utf-8")


def _evaluation(anon_id: str, score: int = 4) -> ModuleEvaluation:
    dim = DimensionScore(score=score, justification="ổn")
    return ModuleEvaluation(
        anon_id=anon_id, accuracy=dim, completeness=dim, consistency=dim, hallucination_score=dim,
        slide_quality=dim, quiz_quality=dim, bloom_fidelity=dim, query_alignment=dim, overall_notes="",
    )


@pytest.fixture
def judge(monkeypatch):
    """Mô hình chấm giả: điểm cố định, ghi lại gói nó được xem và model đang được dùng."""
    seen: list[dict] = []

    def fake(packet):
        seen.append({"packet": packet, "alias": run_judge.gateway.active_model()})
        return _evaluation(packet.anon_id)

    monkeypatch.setattr(run_judge, "judge_module", fake)
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    return seen


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_packet_gives_exactly_one_line(tmp_path, judge):
    _write_pairs(tmp_path, pairs=3)
    out = tmp_path / "ket-qua" / "claude-pro.jsonl"

    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])

    lines = _lines(out)
    assert len(lines) == 6 == len(judge)
    assert {(line["pair_id"], line["group"]) for line in lines} == {
        (f"cap-{n}", g) for n in range(3) for g in ("rag_llm", "llm_no_rag")
    }
    assert all(line["judge"] == "claude-pro" and line["evaluation"]["accuracy"]["score"] == 4 for line in lines)


def test_the_calls_go_to_the_judge_named_on_the_command_line(tmp_path, judge):
    _write_pairs(tmp_path, pairs=1)

    run_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(tmp_path / "o.jsonl")])

    assert {call["alias"] for call in judge} == {"groq-pro"}


def test_the_judge_sees_an_anonymous_code_that_does_not_name_the_group(tmp_path, judge):
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"

    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])

    codes = [call["packet"].anon_id for call in judge]
    assert len(set(codes)) == 4
    assert not any("rag" in code or "cap" in code for code in codes)
    assert {line["anon_id"] for line in _lines(out)} == set(codes)


def test_a_judge_of_the_same_family_as_the_generator_is_refused_before_any_call(tmp_path, judge):
    _write_pairs(tmp_path, pairs=1, generator="gemini-fast")
    out = tmp_path / "o.jsonl"

    with pytest.raises(SystemExit) as refused:
        run_judge.main([str(tmp_path), "--judge", "gemini-fast", "--out", str(out)])

    assert refused.value.code not in (0, None)
    assert "google" in str(refused.value.code)
    assert judge == [] and not out.exists()


def test_the_family_is_the_maker_not_the_host(tmp_path, judge):
    """gpt-oss chạy trên Groq vẫn là model của OpenAI: chấm gói do GPT-4o mini sinh là cùng họ."""
    _write_pairs(tmp_path, pairs=1, generator="openai-fast")

    with pytest.raises(SystemExit):
        run_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(tmp_path / "o.jsonl")])

    assert judge == []


@pytest.mark.parametrize(("model", "family"), [
    ("gemini/gemini-flash-lite-latest", "google"),
    ("anthropic/claude-sonnet-5", "anthropic"),
    ("groq/openai/gpt-oss-120b", "openai"),
    ("openai/gpt-4o", "openai"),
    ("groq/llama-3.3-70b-versatile", "meta"),
])
def test_model_family(model, family):
    assert run_judge.model_family(model) == family


def test_an_unknown_judge_alias_is_refused(tmp_path, judge):
    _write_pairs(tmp_path, pairs=1)

    with pytest.raises(SystemExit) as refused:
        run_judge.main([str(tmp_path), "--judge", "khong-co", "--out", str(tmp_path / "o.jsonl")])

    assert "khong-co" in str(refused.value.code)
    assert judge == []


def test_a_rerun_only_judges_the_packets_still_missing(tmp_path, judge):
    """Một lượt chấm trả tiền bị ngắt giữa chừng (hết hạn mức, mất mạng) thì chạy lại không trả tiền
    lần nữa cho những gói đã chấm xong."""
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])
    first = out.read_text(encoding="utf-8").splitlines()
    out.write_text("\n".join(first[:3]) + "\n", encoding="utf-8")
    judge.clear()

    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])

    assert len(judge) == 1
    assert sorted(line["anon_id"] for line in _lines(out)) == sorted(json.loads(x)["anon_id"] for x in first)


def test_a_second_judge_on_the_same_output_file_judges_every_packet(tmp_path, judge):
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])
    judge.clear()

    run_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(out)])

    assert len(judge) == 4
    assert sorted(line["judge"] for line in _lines(out)) == ["claude-pro"] * 4 + ["groq-pro"] * 4


def test_a_line_cut_mid_character_by_an_interrupted_run_is_trimmed_and_judged_again(tmp_path, judge):
    """Dòng ghi dở cắt đôi một ký tự tiếng Việt nhiều byte: chạy lại không hỏng mà chấm lại gói đó."""
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])
    complete = out.read_bytes().splitlines(keepends=True)
    cut = complete[3][: complete[3].index("ổn".encode()) + 1]
    out.write_bytes(b"".join(complete[:3]) + cut)
    judge.clear()

    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])

    assert len(judge) == 1
    assert sorted(line["anon_id"] for line in _lines(out)) == sorted(json.loads(x)["anon_id"] for x in complete)
