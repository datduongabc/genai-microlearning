import random

from app.models import StudyItem
from app.study.items import assign_codes


def test_codes_shuffle_all_groups_and_replace_supplied_anonymous_ids(monkeypatch):
    monkeypatch.setattr(random.SystemRandom, "shuffle", lambda self, items: items.reverse())
    items = [StudyItem(group=group, packet={"anon_id": "revealing-id", "topic": "Topic"})
             for group in ("rag_llm", "llm_no_rag", "human")]
    assign_codes(items)
    assert [item.anon_code for item in items] == ["M03", "M02", "M01"]
    assert [item.packet["anon_id"] for item in items] == ["M03", "M02", "M01"]
    assert all(item.packet["topic"] == "Topic" for item in items)


def test_loading_more_items_reblinds_existing_codes(monkeypatch):
    monkeypatch.setattr(random.SystemRandom, "shuffle", lambda self, items: items.reverse())
    old = StudyItem(group="human", anon_code="M01", packet={"anon_id": "M01"})
    new = StudyItem(group="rag_llm", packet={"anon_id": ""})
    assign_codes([old, new])
    assert old.anon_code == "M02"
    assert new.anon_code == "M01"


def test_codes_remain_unique_beyond_two_digits():
    items = [StudyItem(group="human", packet={}) for _ in range(101)]
    assign_codes(items)
    assert {item.anon_code for item in items} == {f"M{i:02d}" for i in range(1, 102)}
