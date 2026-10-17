"""Chạy LLM chấm trên một thư mục gói nghiên cứu: mỗi gói ra hai dòng kết quả, và từ chối trước
khi tốn lượt gọi nào khi mô hình chấm cùng hãng với mô hình đã sinh gói, vì mô hình chấm ưu ái văn
của chính họ nhà mình.

Mô hình chấm và danh sách model của gateway đều là hàm giả, không gọi mạng."""

import json

import pytest

from app.generation.study_packets import StudyFile
from app.llm.judge import model_family
from scripts import run_judge
from tests.integration.judge_helpers import GATEWAY, _evaluation, _packet


def _write_pairs(folder, pairs: int, generator: str = "gemini-fast") -> None:
    for n in range(pairs):
        for group in ("rag_llm", "llm_no_rag"):
            study = StudyFile(pair_id=f"cap-{n}", group=group, generator_model=generator, packet=_packet())
            (folder / f"cap-{n}-{group}.json").write_text(study.model_dump_json(indent=2), encoding="utf-8")


@pytest.fixture
def judge(monkeypatch):
    """Mô hình chấm giả: điểm cố định, ghi lại gói nó được xem và model đang được dùng."""
    seen: list[dict] = []

    def fake(packet, **kwargs):
        run_judge.gateway.complete("judge")
        seen.append({"packet": packet, "alias": run_judge.gateway.active_model()})
        return _evaluation(packet.anon_id)

    from types import SimpleNamespace
    def completion(**kwargs):
        alias = run_judge.gateway.active_model()
        model = next(m["litellm_params"]["model"] for m in GATEWAY if m["model_name"] == alias)
        return SimpleNamespace(model=model, choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
    monkeypatch.setattr("litellm.completion", completion)
    monkeypatch.setattr(run_judge, "judge_module", fake)
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    return seen


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_packet_gives_exactly_two_cross_family_lines(tmp_path, judge):
    _write_pairs(tmp_path, pairs=3)
    out = tmp_path / "ket-qua" / "claude-pro.jsonl"

    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])

    lines = _lines(out)
    assert len(lines) == 12 == len(judge)
    assert {(line["pair_id"], line["group"]) for line in lines} == {
        (f"cap-{n}", g) for n in range(3) for g in ("rag_llm", "llm_no_rag")
    }
    assert {line["judge"] for line in lines} == {"claude-pro", "openai-pro"}


def test_the_calls_go_to_the_judge_named_on_the_command_line(tmp_path, judge):
    _write_pairs(tmp_path, pairs=1)

    run_judge.main([str(tmp_path), "--judge", "openai-pro", "--out", str(tmp_path / "o.jsonl")])

    assert {call["alias"] for call in judge} == {"claude-pro", "openai-pro"}


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
        run_judge.main([str(tmp_path), "--judge", "openai-pro", "--out", str(tmp_path / "o.jsonl")])

    assert judge == []


@pytest.mark.parametrize(("model", "family"), [
    ("gemini/gemini-flash-lite-latest", "google"),
    ("anthropic/claude-sonnet-5", "anthropic"),
    ("groq/openai/gpt-oss-120b", "openai"),
    ("openai/gpt-4o", "openai"),
    ("groq/llama-3.3-70b-versatile", "meta"),
])
def test_model_family(model, family):
    assert model_family(model) == family


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

    assert len(judge) == 5
    assert sorted(line["anon_id"] for line in _lines(out)) == sorted(json.loads(x)["anon_id"] for x in first)


def test_completed_pairs_are_not_rejudged_when_preference_changes(tmp_path, judge):
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])
    judge.clear()

    run_judge.main([str(tmp_path), "--judge", "openai-pro", "--out", str(out)])

    assert len(judge) == 0
    assert sorted(line["judge"] for line in _lines(out)) == ["claude-pro"] * 4 + ["openai-pro"] * 4


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

    assert len(judge) == 5
    assert sorted(line["anon_id"] for line in _lines(out)) == sorted(json.loads(x)["anon_id"] for x in complete)


def test_guard_uses_all_actual_served_models_even_if_requested_alias_is_different(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    study = StudyFile(pair_id="pair", group="rag_llm", generator_model="gemini-fast", packet=_packet(),
                      generation_models=[{"requested_alias": "gemini-fast", "routed_alias": "gemini-fast",
                                          "served_model": "gpt-oss-120b"}])
    with pytest.raises(SystemExit, match="thực sự"):
        run_judge._refuse_same_family("openai-fast", [(Path("fallback.json"), study)])
    assert run_judge._refuse_same_family("claude-pro", [(Path("fallback.json"), study)]) == "anthropic/claude-sonnet-5"


def test_runner_pins_requests_without_extra_judge_records(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import litellm

    _write_pairs(tmp_path, pairs=1)
    sent = []
    def completion(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(model=next(m["litellm_params"]["model"] for m in GATEWAY if m["model_name"] == run_judge.gateway.active_model()), choices=[
            SimpleNamespace(message=SimpleNamespace(content="ok")),
        ])
    def evaluate(packet, **kwargs):
        run_judge.gateway.complete("judge")
        return _evaluation(packet.anon_id)
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(run_judge, "judge_module", evaluate)
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    out = tmp_path / "result.jsonl"
    run_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])
    assert all(call["extra_body"]["disable_fallbacks"] for call in sent)
    assert all("judge_generation_models" not in row for row in _lines(out))


def test_quota_without_substitute_is_resumable(tmp_path, judge, monkeypatch):
    import litellm
    _write_pairs(tmp_path, pairs=1)
    out = tmp_path / 'ratings.jsonl'
    real = run_judge.judge_module
    def quota(packet, **kwargs):
        if run_judge.gateway.active_model() != 'claude-pro':
            raise litellm.RateLimitError('quota', model='gpt', llm_provider='openai')
        return real(packet, **kwargs)
    monkeypatch.setattr(run_judge, 'judge_module', quota)
    with pytest.raises(SystemExit, match='missing 1/2 ratings'):
        run_judge.main([str(tmp_path), '--out', str(out)])
    assert len(_lines(out)) == 2
    monkeypatch.setattr(run_judge, 'judge_module', real)
    run_judge.main([str(tmp_path), '--out', str(out)])
    assert len(_lines(out)) == 4
    assert len(judge) == 4


def test_quota_uses_groq_substitute_and_records_actual_model(tmp_path, judge, monkeypatch):
    import litellm
    _write_pairs(tmp_path, pairs=1)
    out = tmp_path / 'ratings.jsonl'
    real = run_judge.judge_module
    def quota(packet, **kwargs):
        if run_judge.gateway.active_model() == 'openai-pro':
            raise litellm.RateLimitError('quota', model='gpt', llm_provider='openai')
        return real(packet, **kwargs)
    monkeypatch.setattr(run_judge, 'judge_module', quota)
    run_judge.main([str(tmp_path), '--out', str(out)])
    assert {r['judge'] for r in _lines(out)} == {'claude-pro', 'groq-llama'}
    assert {model_family(r['judge_model']) for r in _lines(out)} == {'anthropic', 'meta'}
