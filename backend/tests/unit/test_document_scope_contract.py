"""All uploads and generation requests use the ordinary document scope."""
import pytest
from pydantic import ValidationError

from app.api.modules import GenerateRequest
from app.main import app


def test_upload_has_no_document_kind_and_sections_have_no_endpoint():
    schema = app.openapi()
    parameters = schema['paths']['/documents']['post']['parameters']
    assert 'kind' not in {p['name'] for p in parameters}
    assert '/documents/{document_id}/sections' not in schema['paths']


def test_generation_rejects_removed_section_scope():
    assert 'section_ids' not in GenerateRequest.model_fields
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate({'section_ids': ['00000000-0000-0000-0000-000000000001']})
