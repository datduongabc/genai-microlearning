import httpx
import litellm
import pytest

from app.llm.judge import eligible_judges, model_family, refuse_same_family
from app.study.judge import judge_pair
from tests.integration.judge_helpers import GATEWAY, _packet


@pytest.mark.parametrize('generator,pair', [
    ('claude-fast', ['openai-fast', 'gemini-flash']),
    ('openai-fast', ['claude-fast', 'gemini-flash']),
    ('gemini-flash', ['claude-fast', 'openai-fast']),
    ('deepseek-flash', ['claude-fast', 'openai-fast']),
])
def test_preferred_pair_excludes_generator(generator, pair):
    candidates = eligible_judges([('M1', generator, [])], GATEWAY)
    assert [c['alias'] for c in candidates[:2]] == pair
    assert len({model_family(c['model']) for c in candidates[:2]}) == 2


def test_substitute_excludes_all_generator_families():
    candidates = eligible_judges([('M1', 'gemini-flash', [
        {'served_model': 'openai/gpt-6-luna'}, {'served_model': 'gemini/gemini-test'},
    ])], GATEWAY)
    assert [c['alias'] for c in candidates] == ['claude-fast', 'deepseek-flash']
    with pytest.raises(ValueError, match='cùng hãng'):
        refuse_same_family('openai-fast', [('M1', 'gemini-flash', [
            {'served_model': 'groq/openai/gpt-oss-120b'}])], GATEWAY)


def test_an_unavailable_candidate_after_a_quota_error_does_not_block_the_next_one():
    denied = litellm.PermissionDeniedError('no access', llm_provider='openai', model='gpt',
                                           response=httpx.Response(403, request=httpx.Request('POST', 'http://gw')))
    failures = {'claude-fast': litellm.RateLimitError('quota', llm_provider='anthropic', model='claude'),
                'openai-fast': denied}
    judged = []
    def rate(key, packet, candidate):
        if candidate['alias'] in failures:
            raise failures[candidate['alias']]
        judged.append(candidate['alias'])

    missing, errors = judge_pair({'M1': _packet()}, [('M1', 'gemini-flash', [])], GATEWAY, {'M1': set()}, rate)

    assert judged == ['deepseek-flash'] and missing == 1
    assert [e.split(':')[0] for e in errors] == ['claude-fast', 'openai-fast']
