from app.generation.media_common import llm_cost


def _call(usd, prompt=100, completion=10):
    return {"kind": "llm", "prompt_tokens": prompt, "completion_tokens": completion, "cost_usd": usd}


def test_the_cost_adds_every_call():
    cost = llm_cost([_call(0.01), _call(0.02, 200, 20), {"kind": "stage"}])
    assert cost == {"usd": 0.03, "calls": 2, "priced_calls": 2, "prompt_tokens": 300, "completion_tokens": 30}


def test_a_call_without_a_price_is_counted_but_not_guessed():
    cost = llm_cost([_call(0.01), _call(None)])
    assert cost["usd"] == 0.01 and cost["calls"] == 2 and cost["priced_calls"] == 1


def test_no_priced_call_leaves_the_amount_empty():
    assert llm_cost([_call(None)])["usd"] is None
    assert llm_cost([])["usd"] is None
