import uuid


def test_shuffle_is_stable_per_rater_without_changing_input():
    from app.study.items import shuffle_for_rater
    study = uuid.UUID(int=1)
    items = list(range(12))
    first = shuffle_for_rater(items, study, uuid.UUID(int=2))
    assert first == shuffle_for_rater(items, study, uuid.UUID(int=2))
    assert first != shuffle_for_rater(items, study, uuid.UUID(int=3))
    assert sorted(first) == items == list(range(12))
    assert shuffle_for_rater([], study, uuid.UUID(int=2)) == []
