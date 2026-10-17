"""All uploads and generation requests use the ordinary document scope."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.modules import GenerateRequest
from app.db import get_db
from app.main import app


def test_upload_has_no_document_kind_and_sections_have_no_endpoint():
    schema = app.openapi()
    parameters = schema['paths']['/documents']['post']['parameters']
    assert 'kind' not in {p['name'] for p in parameters}
    assert '/documents/{document_id}/sections' not in schema['paths']


@pytest.fixture
def anonymous_client():
    app.dependency_overrides[get_current_user] = lambda: None
    app.dependency_overrides[get_db] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_generation_rejects_removed_section_scope(anonymous_client):
    response = anonymous_client.post(
        '/modules/generate', params={'document_id': str(uuid.uuid4())},
        json={'section_ids': [str(uuid.uuid4())]},
    )

    assert response.status_code == 422
    assert 'section_ids' in response.json()['detail'][0]['msg']
    assert 'gỡ bỏ' in response.json()['detail'][0]['msg']


def test_generation_request_ignores_other_unknown_fields():
    body = GenerateRequest.model_validate({'model_alias': 'claude-fast', 'unknown': 1})

    assert body.model_alias == 'claude-fast'
