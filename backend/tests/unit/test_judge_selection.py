import pytest

from app.llm.judge import eligible_judges, model_family, refuse_same_family
from tests.integration.judge_helpers import GATEWAY


@pytest.mark.parametrize('generator,pair', [
    ('claude-pro', ['openai-pro', 'gemini-fast']),
    ('openai-fast', ['claude-pro', 'gemini-fast']),
    ('gemini-fast', ['claude-pro', 'openai-pro']),
])
def test_preferred_pair_excludes_generator(generator, pair):
    candidates = eligible_judges([('M1', generator, [])], GATEWAY)
    assert [c['alias'] for c in candidates[:2]] == pair
    assert len({model_family(c['model']) for c in candidates[:2]}) == 2


def test_substitute_excludes_all_generator_and_completed_families():
    candidates = eligible_judges([('M1', 'gemini-fast', [
        {'served_model': 'openai/gpt-6.1-sol'}, {'served_model': 'gemini/gemini-test'},
    ])], GATEWAY, ['anthropic/claude-sonnet-5'])
    assert [c['alias'] for c in candidates] == ['groq-llama', 'groq-qwen']
    with pytest.raises(ValueError, match='cùng hãng'):
        refuse_same_family('openai-pro', [('M1', 'gemini-fast', [
            {'served_model': 'groq/openai/gpt-oss-120b'}])], GATEWAY)
