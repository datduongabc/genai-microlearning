import httpx
import litellm
import pytest

from app.llm.judge import eligible_judges, model_family, refuse_same_family
from app.study.judge import judge_pair
from tests.integration.judge_helpers import GATEWAY, _packet


@pytest.mark.parametrize('generator,pair', [
    ('claude-pro', ['openai-pro', 'gemini-fast']),
    ('openai-fast', ['claude-pro', 'gemini-fast']),
    ('gemini-fast', ['claude-pro', 'openai-pro']),
])
def test_preferred_pair_excludes_generator(generator, pair):
    candidates = eligible_judges([('M1', generator, [])], GATEWAY)
    assert [c['alias'] for c in candidates[:2]] == pair
    assert len({model_family(c['model']) for c in candidates[:2]}) == 2


def test_substitute_excludes_all_generator_families():
    candidates = eligible_judges([('M1', 'gemini-fast', [
        {'served_model': 'openai/gpt-6.1-sol'}, {'served_model': 'gemini/gemini-test'},
    ])], GATEWAY)
    assert [c['alias'] for c in candidates] == ['claude-pro', 'groq-llama', 'groq-qwen']
    with pytest.raises(ValueError, match='cùng hãng'):
        refuse_same_family('openai-pro', [('M1', 'gemini-fast', [
            {'served_model': 'groq/openai/gpt-oss-120b'}])], GATEWAY)


def test_an_unavailable_candidate_after_a_quota_error_does_not_block_the_next_one():
    denied = litellm.PermissionDeniedError('no access', llm_provider='openai', model='gpt',
                                           response=httpx.Response(403, request=httpx.Request('POST', 'http://gw')))
    failures = {'claude-pro': litellm.RateLimitError('quota', llm_provider='anthropic', model='claude'),
                'openai-pro': denied}
    judged = []
    def rate(key, packet, candidate):
        if candidate['alias'] in failures:
            raise failures[candidate['alias']]
        judged.append(candidate['alias'])

    missing, errors = judge_pair({'M1': _packet()}, [('M1', 'gemini-fast', [])], GATEWAY, {'M1': set()}, rate)

    assert judged == ['groq-llama', 'groq-qwen'] and missing == 0
    assert [e.split(':')[0] for e in errors] == ['claude-pro', 'openai-pro']
