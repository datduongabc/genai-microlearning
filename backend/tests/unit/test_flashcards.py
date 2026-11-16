import uuid
from types import SimpleNamespace

import pytest

from app.generation import fact_check, flashcards, media_common
from app.generation.claim_evaluation import SupportVerdict
from app.jobs.queue import LostJob
from app.llm import catalog
from app.models import MediaStatus


class FakeDb:
    expire_on_commit = True

    def __init__(self, *rows):
        self.rows = dict(zip(("Document", "FlashcardSet"), rows, strict=True))
        self.commits = 0

    def get(self, model, _id):
        return self.rows.get(model.__name__)

    def commit(self):
        self.commits += 1


def _chunks(n=3):
    return [SimpleNamespace(id=uuid.uuid4(), chunk_index=i, content=f"passage {i}", source_ref=None) for i in range(n)]


def _setup(monkeypatch, chunks, drafts, focus="heaps", verdict=lambda prompt: True, alias="gemini-flash"):
    doc = SimpleNamespace(id=uuid.uuid4(), course_id=uuid.uuid4(), raw_text="text", planner_concepts=None)
    card_set = SimpleNamespace(
        id=uuid.uuid4(), document_id=doc.id, course_id=doc.course_id, focus=focus, output_language="en",
        model_alias=alias, status=MediaStatus.generating, cards=[])
    db = FakeDb(doc, card_set)
    # Every alias counts as served: these tests are about generation, not about a dropped alias.
    monkeypatch.setattr(catalog, "is_available", lambda _alias: True)
    queries = []
    monkeypatch.setattr(media_common, "retrieve", lambda _db, _course, query, **kw: queries.append((query, kw)) or chunks)
    asked = []
    monkeypatch.setattr(flashcards, "complete_structured", lambda prompt, model, **kw: (
        asked.append((prompt, kw["system_instruction"])), model(cards=drafts))[1])
    card_set.asked = asked
    monkeypatch.setattr(fact_check, "_support", lambda claim, passages, options: SupportVerdict(
        supported=verdict(claim), reason="not in the passage"))
    return db, card_set, queries


def _draft(front, sources=("S1",)):
    return {"front": front, "back": f"answer to {front}", "sources": list(sources)}


def test_cards_are_capped_at_15(monkeypatch):
    chunks = _chunks()
    db, card_set, _ = _setup(monkeypatch, chunks, [_draft(f"q{i}") for i in range(20)])
    flashcards.generate_flashcards(db, card_set.id)
    assert len(card_set.cards) == flashcards.MAX_CARDS == 15
    assert card_set.status == MediaStatus.draft


def test_a_card_without_a_resolvable_citation_is_dropped(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(2), [
        _draft("kept"), _draft("unknown number", ["S9"]), _draft("uncited", []), _draft("not a code", ["see slide 3"])])
    flashcards.generate_flashcards(db, card_set.id)
    assert [c["front"] for c in card_set.cards] == ["kept"]


def test_cards_keep_chunk_ids_not_prompt_numbers(monkeypatch):
    chunks = _chunks(3)
    db, card_set, _ = _setup(monkeypatch, chunks, [_draft("q", ["S3", "[S1]", "S3"])])
    flashcards.generate_flashcards(db, card_set.id)
    card = card_set.cards[0]
    assert card["citations"] == [str(chunks[2].id), str(chunks[0].id)]
    assert card["author"] == "model" and card["flag"] is None


def test_fact_check_flags_the_card_the_judge_rejects(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("a"), _draft("b"), _draft("c")],
                             verdict=lambda claim: "answer to b" not in claim)
    flashcards.generate_flashcards(db, card_set.id)
    assert [c["flag"] is not None for c in card_set.cards] == [False, True, False]
    assert "not in the passage" in card_set.cards[1]["flag"]


def test_the_judge_is_given_the_cited_passages_of_each_card(monkeypatch):
    chunks = _chunks(3)
    db, card_set, _ = _setup(monkeypatch, chunks, [_draft("q", ["S2"])])
    asked = []
    monkeypatch.setattr(fact_check, "_support", lambda claim, passages, options: asked.append(passages) or SupportVerdict(
        supported=True, reason=""))
    flashcards.generate_flashcards(db, card_set.id)
    assert asked == [["passage 1"]]


def test_retrieval_uses_the_focus_adaptively_within_the_document(monkeypatch):
    db, card_set, queries = _setup(monkeypatch, _chunks(), [_draft("q")], focus="binary heaps")
    flashcards.generate_flashcards(db, card_set.id)
    query, kwargs = queries[0]
    assert query == "binary heaps"
    assert kwargs["top_k"] is None
    assert kwargs["scope"].document_id == card_set.document_id


def test_without_a_focus_the_planners_first_concept_is_the_query(monkeypatch):
    db, card_set, queries = _setup(monkeypatch, _chunks(), [_draft("q")], focus=None)
    concepts = [SimpleNamespace(name="Later", summary="s2", priority=2), SimpleNamespace(name="First", summary="s1", priority=1)]
    monkeypatch.setattr(media_common, "_get_or_cache_planner", lambda *a, **k: SimpleNamespace(concepts=concepts))
    flashcards.generate_flashcards(db, card_set.id)
    assert queries[0][0] == "First: s1"


def test_a_lost_job_writes_nothing(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])

    def lost():
        raise LostJob("gone")

    with pytest.raises(LostJob):
        flashcards.generate_flashcards(db, card_set.id, before_save=lost)
    assert card_set.status == MediaStatus.generating and card_set.cards == []


def test_the_set_is_generated_with_its_own_model(monkeypatch):
    from app.llm.gateway import requested_model
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")], alias="openai-fast")
    used = []
    monkeypatch.setattr(flashcards, "complete_structured", lambda prompt, model, **kw: (
        used.append(requested_model()), model(cards=[_draft("q")]))[1])
    flashcards.generate_flashcards(db, card_set.id)
    assert used == ["openai-fast"]


def test_an_unknown_alias_fails_before_any_retrieval(monkeypatch):
    db, card_set, queries = _setup(monkeypatch, _chunks(), [_draft("q")], alias="mystery-1")
    with pytest.raises(ValueError, match="mystery-1"):
        flashcards.generate_flashcards(db, card_set.id)
    assert queries == []


def test_front_and_back_are_stripped_once_for_judge_and_storage(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [{"front": "  q \n", "back": " a  ", "sources": ["S1"]}])
    judged = []
    monkeypatch.setattr(fact_check, "_support", lambda claim, passages, options: judged.append(claim) or SupportVerdict(
        supported=True, reason=""))
    flashcards.generate_flashcards(db, card_set.id)
    assert judged == ["q\na"]
    assert (card_set.cards[0]["front"], card_set.cards[0]["back"]) == ("q", "a")


def test_a_focus_naming_two_topics_retrieves_and_asks_for_each(monkeypatch):
    # Live run: "FCFS và Round Robin" gave no card on FCFS and four on other scheduling.
    db, card_set, queries = _setup(monkeypatch, _chunks(), [_draft("q")], focus="FCFS và Round Robin")
    flashcards.generate_flashcards(db, card_set.id)
    assert [q for q, _ in queries] == ["FCFS", "Round Robin"]
    prompt, system = card_set.asked[0]
    assert "TOPICS: FCFS; Round Robin" in prompt
    assert "every topic in TOPICS" in system


def test_a_word_the_model_wrote_twice_is_kept_once(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [
        {"front": "Round Robin khác FCFS ở ở đâu?", "back": "ngoại trừ việc việc chia sẻ thời gian", "sources": ["S1"]}])
    flashcards.generate_flashcards(db, card_set.id)
    card = card_set.cards[0]
    assert (card["front"], card["back"]) == ("Round Robin khác FCFS ở đâu?", "ngoại trừ việc chia sẻ thời gian")


@pytest.mark.parametrize("language", ["vi", "en"])
def test_the_judge_answers_in_the_sets_language(monkeypatch, language):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    card_set.output_language = language
    seen = []
    monkeypatch.setattr(fact_check, "_support", lambda claim, passages, lang: seen.append(lang) or SupportVerdict(
        supported=True, reason=""))
    flashcards.generate_flashcards(db, card_set.id)
    assert seen == [language]


def test_the_instruction_asks_for_cards_in_the_sets_language(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    flashcards.generate_flashcards(db, card_set.id)
    assert "Write ALL output in English" in card_set.asked[0][1]


def test_the_set_records_what_its_model_calls_cost(monkeypatch):
    from app import timing
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    inner = flashcards.complete_structured

    def priced(prompt, model, **kw):
        timing.record("llm", 1.0, prompt_tokens=500, completion_tokens=80, cost_usd=0.002)
        return inner(prompt, model, **kw)

    def priced_judge(claim, passages, options):
        timing.record("llm", 1.0, prompt_tokens=100, completion_tokens=5, cost_usd=0.0005)
        return SupportVerdict(supported=True, reason="")

    monkeypatch.setattr(flashcards, "complete_structured", priced)
    monkeypatch.setattr(fact_check, "_support", priced_judge)
    flashcards.generate_flashcards(db, card_set.id)
    assert card_set.cost == {"usd": 0.0025, "calls": 2, "priced_calls": 2,
                             "prompt_tokens": 600, "completion_tokens": 85}
    assert card_set.served_models == []


# --- Live run 2 (2026-11-16): FCFS 2 cards and RR 5, no worked example, "trưng thu", a mismatched answer ---

def test_each_named_topic_is_asked_for_at_least_two_cards(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")], focus="FCFS và Round Robin")
    flashcards.generate_flashcards(db, card_set.id)
    assert "at least 2 cards on each topic in TOPICS" in card_set.asked[0][1]


def test_a_card_works_through_a_worked_example(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    flashcards.generate_flashcards(db, card_set.id)
    system = card_set.asked[0][1]
    assert "at least one card works through it" in system
    # Not forced: a chunk set without an example has none to work through.
    assert "When the chunks work through an example" in system


def test_a_cards_answer_must_answer_its_question(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    flashcards.generate_flashcards(db, card_set.id)
    system = card_set.asked[0][1]
    assert "The back answers exactly what the front asks" in system


def test_a_vietnamese_set_gets_the_shared_term_glossary(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    card_set.output_language = "vi"
    flashcards.generate_flashcards(db, card_set.id)
    system = card_set.asked[0][1]
    assert media_common.term_rules("vi") in system
    assert '"trưng dụng" (never "trưng thu")' in system


def test_an_english_set_has_no_vietnamese_glossary(monkeypatch):
    db, card_set, _ = _setup(monkeypatch, _chunks(), [_draft("q")])
    flashcards.generate_flashcards(db, card_set.id)
    assert "trưng" not in card_set.asked[0][1]
