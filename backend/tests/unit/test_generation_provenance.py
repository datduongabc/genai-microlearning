import uuid

import pytest

from app.generation.module_generation_pipeline import (
    control_payload,
    generation_payload,
    normalize_generation_payload,
)
from app.llm.gateway import active_model, requested_model, using_model


def test_default_selection_keeps_the_same_payload_as_legacy_jobs(monkeypatch):
    from app.llm.gateway import settings

    monkeypatch.setattr(settings, "llm_model", "default-model")
    with using_model(None):
        assert active_model() == "default-model"
        assert generation_payload(requested_model(), False) == generation_payload(None, False)
        paired_id = uuid.uuid4()
        assert control_payload(paired_id, requested_model(), False) == control_payload(paired_id, None, False)


def test_explicit_alias_is_preserved_and_does_not_match_other_selections():
    paired_id = uuid.uuid4()
    with using_model("explicit-model"):
        assert active_model() == requested_model() == "explicit-model"
        saved = control_payload(paired_id, requested_model(), False)
        assert saved == control_payload(paired_id, "explicit-model", False)
        assert saved != control_payload(paired_id, None, False)
        assert saved != control_payload(paired_id, "other-model", False)
        with using_model(None):
            assert requested_model() is None
        assert requested_model() == "explicit-model"



def test_missing_conditions_match_explicit_defaults_without_changing_alias():
    legacy = {"model_alias": "test-model", "use_retrieval": False, "paired_module_id": str(uuid.uuid4())}
    expected = {**legacy, "self_check": True, "output_language": "vi", "custom_instructions": ""}
    assert normalize_generation_payload(legacy) == normalize_generation_payload(expected)


@pytest.mark.parametrize("field,value", [
    ("self_check", False), ("output_language", "en"), ("custom_instructions", "Explain X"),
    ("use_retrieval", False), ("model_alias", "explicit-model"),
])
def test_explicit_condition_differences_remain_distinct(field, value):
    assert normalize_generation_payload({field: value}) != normalize_generation_payload({})
