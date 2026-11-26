"""Chạy LLM chấm trên một thư mục gói nghiên cứu: mỗi gói ra hai dòng kết quả, và từ chối trước
khi tốn lượt gọi nào khi mô hình chấm cùng hãng với mô hình đã sinh gói, vì mô hình chấm ưu ái văn
của chính họ nhà mình.

Mô hình chấm và danh sách model của gateway đều là hàm giả, không gọi mạng."""

import json

import httpx
import litellm
import pytest
from litellm.litellm_core_utils.exception_mapping_utils import exception_type

from app.generation.study_packets import StudyFile
from app.llm.judge import eligible_judges, model_family
from app.study.judge import check_packet_budget, is_context_window_error, judge_pair
from scripts import run_judge
from tests.judge_helpers import GATEWAY, _evaluation, _packet


def _write_pairs(folder, pairs: int, generator: str = "gemini-flash") -> None:
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
    out = tmp_path / "ket-qua" / "claude-fast.jsonl"

    run_judge.main([str(tmp_path), "--out", str(out)])

    lines = _lines(out)
    assert len(lines) == 12 == len(judge)
    assert {(line["pair_id"], line["group"]) for line in lines} == {
        (f"cap-{n}", g) for n in range(3) for g in ("rag_llm", "llm_no_rag")
    }
    assert {line["judge"] for line in lines} == {"claude-fast", "openai-fast"}


def test_the_judge_sees_an_anonymous_code_that_does_not_name_the_group(tmp_path, judge):
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"

    run_judge.main([str(tmp_path), "--out", str(out)])

    codes = [call["packet"].anon_id for call in judge]
    assert len(set(codes)) == 4
    assert not any("rag" in code or "cap" in code for code in codes)
    assert {line["anon_id"] for line in _lines(out)} == set(codes)


def test_a_judge_of_the_same_family_as_the_generator_is_never_called(tmp_path, judge):
    """Gói do GPT-6 Luna sinh không được OpenAI chấm."""
    _write_pairs(tmp_path, pairs=1, generator="openai-fast")

    run_judge.main([str(tmp_path), "--out", str(tmp_path / "o.jsonl")])

    assert {call["alias"] for call in judge} == {"claude-fast", "gemini-flash"}


def test_a_rerun_only_judges_the_packets_still_missing(tmp_path, judge):
    """Một lượt chấm trả tiền bị ngắt giữa chừng (hết hạn mức, mất mạng) thì chạy lại không trả tiền
    lần nữa cho những gói đã chấm xong."""
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--out", str(out)])
    first = out.read_text(encoding="utf-8").splitlines()
    out.write_text("\n".join(first[:3]) + "\n", encoding="utf-8")
    judge.clear()

    run_judge.main([str(tmp_path), "--out", str(out)])

    assert len(judge) == 5
    assert sorted(line["anon_id"] for line in _lines(out)) == sorted(json.loads(x)["anon_id"] for x in first)


def test_completed_pairs_are_not_rejudged(tmp_path, judge):
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--out", str(out)])
    judge.clear()

    run_judge.main([str(tmp_path), "--out", str(out)])

    assert len(judge) == 0
    assert sorted(line["judge"] for line in _lines(out)) == ["claude-fast"] * 4 + ["openai-fast"] * 4


def test_a_line_cut_mid_character_by_an_interrupted_run_is_trimmed_and_judged_again(tmp_path, judge):
    """Dòng ghi dở cắt đôi một ký tự tiếng Việt nhiều byte: chạy lại không hỏng mà chấm lại gói đó."""
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "o.jsonl"
    run_judge.main([str(tmp_path), "--out", str(out)])
    complete = out.read_bytes().splitlines(keepends=True)
    cut = complete[3][: complete[3].index("ổn".encode()) + 1]
    out.write_bytes(b"".join(complete[:3]) + cut)
    judge.clear()

    run_judge.main([str(tmp_path), "--out", str(out)])

    assert len(judge) == 5
    assert sorted(line["anon_id"] for line in _lines(out)) == sorted(json.loads(x)["anon_id"] for x in complete)


def test_guard_uses_all_actual_served_models_even_if_requested_alias_is_different(tmp_path, judge):
    """Gói xin gemini-flash nhưng GPT-OSS thực sự sinh: không chấm bằng OpenAI, vẫn chấm bằng Gemini."""
    study = StudyFile(
        pair_id="pair", group="rag_llm", generator_model="gemini-flash", packet=_packet(),
        generation_models=[{"requested_alias": "gemini-flash", "served_model": "gpt-oss-120b"}],
    )
    (tmp_path / "fallback.json").write_text(study.model_dump_json(), encoding="utf-8")

    run_judge.main([str(tmp_path), "--out", str(tmp_path / "o.jsonl")])

    assert {call["alias"] for call in judge} == {"claude-fast", "gemini-flash"}


def test_runner_pins_requests_without_extra_judge_records(tmp_path, monkeypatch):
    from types import SimpleNamespace

    _write_pairs(tmp_path, pairs=1)
    sent = []

    def completion(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(
            model=next(m["litellm_params"]["model"] for m in GATEWAY if m["model_name"] == run_judge.gateway.active_model()),
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )

    def evaluate(packet, **kwargs):
        run_judge.gateway.complete("judge")
        return _evaluation(packet.anon_id)

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(run_judge, "judge_module", evaluate)
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    out = tmp_path / "result.jsonl"
    run_judge.main([str(tmp_path), "--out", str(out)])
    assert all(call["extra_body"]["disable_fallbacks"] for call in sent)
    assert all("judge_generation_models" not in row for row in _lines(out))


def test_quota_without_substitute_is_resumable(tmp_path, judge, monkeypatch):
    _write_pairs(tmp_path, pairs=1)
    out = tmp_path / "ratings.jsonl"
    real = run_judge.judge_module

    def quota(packet, **kwargs):
        if run_judge.gateway.active_model() != "claude-fast":
            raise litellm.RateLimitError("quota", model="gpt", llm_provider="openai")
        return real(packet, **kwargs)

    monkeypatch.setattr(run_judge, "judge_module", quota)
    with pytest.raises(SystemExit, match="missing 1/2 ratings"):
        run_judge.main([str(tmp_path), "--out", str(out)])
    assert len(_lines(out)) == 2
    monkeypatch.setattr(run_judge, "judge_module", real)
    run_judge.main([str(tmp_path), "--out", str(out)])
    assert len(_lines(out)) == 4
    assert len(judge) == 4


def test_quota_uses_a_substitute_and_records_actual_model(tmp_path, judge, monkeypatch):
    _write_pairs(tmp_path, pairs=1)
    out = tmp_path / "ratings.jsonl"
    real = run_judge.judge_module

    def quota(packet, **kwargs):
        if run_judge.gateway.active_model() == "openai-fast":
            raise litellm.RateLimitError("quota", model="gpt", llm_provider="openai")
        return real(packet, **kwargs)

    monkeypatch.setattr(run_judge, "judge_module", quota)
    run_judge.main([str(tmp_path), "--out", str(out)])
    assert {r["judge"] for r in _lines(out)} == {"claude-fast", "deepseek-flash"}
    assert {model_family(r["judge_model"]) for r in _lines(out)} == {"anthropic", "deepseek"}


def _quota():
    return litellm.RateLimitError("quota", model="gpt", llm_provider="openai")


def test_quota_on_the_second_member_moves_the_whole_pair_to_the_same_substitute(tmp_path, judge, monkeypatch):
    from scripts.analyze_study import load_judgements, pairs

    _write_pairs(tmp_path, pairs=1)
    out = tmp_path / "ratings.jsonl"
    real = run_judge.judge_module
    openai_calls = []

    def quota(packet, **kwargs):
        if run_judge.gateway.active_model() == "openai-fast":
            openai_calls.append(packet.anon_id)
            if len(openai_calls) > 1:
                raise _quota()
        return real(packet, **kwargs)

    monkeypatch.setattr(run_judge, "judge_module", quota)

    run_judge.main([str(tmp_path), "--out", str(out)])

    by_judge = load_judgements([str(out)])
    assert len(pairs(by_judge["claude-fast"])) == 1
    assert len(pairs(by_judge["deepseek-flash"])) == 1
    assert len(by_judge["openai-fast"]) == 1
    judge.clear()
    monkeypatch.setattr(run_judge, "judge_module", real)
    run_judge.main([str(tmp_path), "--out", str(out)])
    assert judge == []
    assert load_judgements([str(out)]) == by_judge


@pytest.mark.parametrize("status", [403, 500])
def test_http_denied_candidate_uses_substitute_but_server_errors_stay_pending(
    tmp_path, monkeypatch, status,
):
    from openai import OpenAI

    from app.config import settings

    _write_pairs(tmp_path, pairs=1, generator="claude-fast")
    out = tmp_path / "ratings.jsonl"
    sent = []

    def proxy(request):
        body = json.loads(request.content)
        alias = body["model"]
        sent.append(alias)
        code = status if alias == "gemini-flash" else 200
        if code != 200:
            return httpx.Response(code, json={"error": {"message": "candidate unavailable",
                                                       "type": "provider_error", "code": str(code)}})
        anon_id = body["messages"][-1]["content"].split("anon_id=")[1].split(")")[0]
        model = next(m["litellm_params"]["model"] for m in GATEWAY if m["model_name"] == alias)
        return httpx.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": _evaluation(anon_id).model_dump_json()}}],
        })

    real = run_judge.judge_module
    monkeypatch.setattr(settings, "llm_gateway_url", "http://gateway.test/v1")
    monkeypatch.setattr(settings, "llm_gateway_key", "test-key")
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    with OpenAI(api_key="test-key", base_url=settings.llm_gateway_url,
                http_client=httpx.Client(transport=httpx.MockTransport(proxy))) as client:
        monkeypatch.setattr(run_judge, "judge_module", lambda packet, **kw: real(packet, client=client, **kw))
        if status == 500:
            with pytest.raises(SystemExit, match="rating pending"):
                run_judge.main([str(tmp_path), "--out", str(out)])
        else:
            run_judge.main([str(tmp_path), "--out", str(out)])

    assert sent == ["openai-fast", "openai-fast", "gemini-flash"] + (
        ["deepseek-flash", "deepseek-flash"] if status == 403 else [])
    assert {r["judge"] for r in _lines(out)} == (
        {"openai-fast", "deepseek-flash"} if status == 403 else {"openai-fast"})


def test_a_packet_over_a_candidate_budget_uses_the_next_candidate(tmp_path, judge, monkeypatch):
    small = [{**m, "model_info": {"max_input_tokens": 10}} if m["model_name"] == "openai-fast" else m
             for m in GATEWAY]
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: small)
    _write_pairs(tmp_path, pairs=1)
    out = tmp_path / "ratings.jsonl"

    run_judge.main([str(tmp_path), "--out", str(out)])

    assert {r["judge"] for r in _lines(out)} == {"claude-fast", "deepseek-flash"}
    assert "openai-fast" not in {call["alias"] for call in judge}


def test_a_failing_pair_is_reported_and_later_pairs_are_still_judged(tmp_path, judge, monkeypatch):
    _write_pairs(tmp_path, pairs=2)
    out = tmp_path / "ratings.jsonl"
    real = run_judge.judge_module
    broken = run_judge.anon_code(tmp_path / "cap-0-rag_llm.json")

    def invalid(packet, **kwargs):
        if packet.anon_id == broken:
            raise ValueError("invalid rubric response")
        return real(packet, **kwargs)

    monkeypatch.setattr(run_judge, "judge_module", invalid)

    with pytest.raises(SystemExit, match="invalid rubric response"):
        run_judge.main([str(tmp_path), "--out", str(out)])

    assert [r["pair_id"] for r in _lines(out)].count("cap-1") == 4
    assert len(_lines(out)) == 5
    monkeypatch.setattr(run_judge, "judge_module", real)
    run_judge.main([str(tmp_path), "--out", str(out)])
    assert len(_lines(out)) == 8


# --- Judge packet budget --------------------------------------------------------------------------


def test_packet_over_the_gateway_input_cap_is_refused_without_truncation(monkeypatch):
    seen = []

    def tokens(**kwargs):
        seen.append(kwargs)
        return 3953

    monkeypatch.setattr("app.study.judge.litellm.token_counter", tokens)
    packet = _packet()
    before = packet.model_dump()
    with pytest.raises(ValueError, match="Packet too large.*3953.*3952.*not truncated"):
        check_packet_budget(packet, 3952)
    assert packet.model_dump() == before
    assert set(seen[0]) == {"messages"}
    check_packet_budget(packet, 3953)
    check_packet_budget(packet, None)
    assert len(seen) == 2


def test_packet_above_the_old_free_tier_cap_fits_the_paid_groq_cap():
    packet = _packet()
    packet.content_markdown = "Whole module content " * 4000
    check_packet_budget(packet, 128000)


def test_vietnamese_packet_is_counted_like_the_gateway_not_like_gpt_4o():
    packet = _packet()
    packet.content_markdown = "Cookie là một mẩu dữ liệu nhỏ mà máy chủ gửi cho trình duyệt. " * 110
    with pytest.raises(ValueError, match="Packet too large"):
        check_packet_budget(packet, 3952)


def test_gateway_context_window_rejection_is_recognised_but_other_errors_are_not():
    message = (
        "litellm.ContextWindowExceededError: litellm._pre_call_checks: Context Window exceeded "
        "for given call. No models have context window large enough for this call."
    )
    response = httpx.Response(400, request=httpx.Request("POST", "http://gateway/chat/completions"))
    with pytest.raises(litellm.BadRequestError) as rejected:
        exception_type(
            model="openai/deepseek-flash", custom_llm_provider="openai",
            original_exception=litellm.llms.openai.common_utils.OpenAIError(
                status_code=400, message=message, request=response.request, response=response),
        )
    assert is_context_window_error(rejected.value)
    assert is_context_window_error(
        litellm.ContextWindowExceededError("too long", model="gpt-oss-120b", llm_provider="groq"))
    assert not is_context_window_error(
        litellm.BadRequestError("bad schema", model="gpt-oss-120b", llm_provider="groq"))
    assert not is_context_window_error(
        litellm.RateLimitError("quota", llm_provider="groq", model="gpt-oss-120b"))


# --- Judge selection ------------------------------------------------------------------------------


@pytest.mark.parametrize("generator,pair", [
    ("claude-fast", ["openai-fast", "gemini-flash"]),
    ("openai-fast", ["claude-fast", "gemini-flash"]),
    ("gemini-flash", ["claude-fast", "openai-fast"]),
    ("deepseek-flash", ["claude-fast", "openai-fast"]),
])
def test_preferred_pair_excludes_generator(generator, pair):
    candidates = eligible_judges([("M1", generator, [])], GATEWAY)
    assert [c["alias"] for c in candidates[:2]] == pair
    assert len({model_family(c["model"]) for c in candidates[:2]}) == 2


def test_an_unavailable_candidate_after_a_quota_error_does_not_block_the_next_one():
    denied = litellm.PermissionDeniedError(
        "no access", llm_provider="openai", model="gpt",
        response=httpx.Response(403, request=httpx.Request("POST", "http://gw")),
    )
    failures = {
        "claude-fast": litellm.RateLimitError("quota", llm_provider="anthropic", model="claude"),
        "openai-fast": denied,
    }
    judged = []

    def rate(key, packet, candidate):
        if candidate["alias"] in failures:
            raise failures[candidate["alias"]]
        judged.append(candidate["alias"])

    missing, errors = judge_pair({"M1": _packet()}, [("M1", "gemini-flash", [])], GATEWAY, {"M1": set()}, rate)

    assert judged == ["deepseek-flash"] and missing == 1
    assert [e.split(":")[0] for e in errors] == ["claude-fast", "openai-fast"]
