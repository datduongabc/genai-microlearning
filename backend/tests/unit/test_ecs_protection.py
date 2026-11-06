"""Scale-in may only take a worker that has no job: a module generation stopped half way is
spent again from the start."""

import json

import httpx

from app.jobs.ecs_protection import PROTECTION_MINUTES, ScaleInProtection


def _agent(calls, status=200):
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json={})
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_a_busy_worker_is_protected_and_renewed_and_released_once_idle():
    calls, busy = [], [True]
    protection = ScaleInProtection("http://agent", lambda: busy[0], client=_agent(calls))

    protection.tick()
    protection.tick()
    busy[0] = False
    protection.tick()
    protection.tick()

    on = {"ProtectionEnabled": True, "ExpiresInMinutes": PROTECTION_MINUTES}
    # Renewed while busy, because the protection expires; one release, then quiet.
    assert calls == [on, on, {"ProtectionEnabled": False}]


def test_an_idle_worker_at_start_tells_the_agent_once():
    calls = []
    protection = ScaleInProtection("http://agent", lambda: False, client=_agent(calls))
    protection.tick()
    protection.tick()
    assert calls == [{"ProtectionEnabled": False}]


def test_a_refused_call_is_tried_again_on_the_next_tick():
    calls = []
    protection = ScaleInProtection("http://agent", lambda: False, client=_agent(calls, status=500))
    protection.tick()
    protection.tick()
    assert len(calls) == 2
    assert protection.protected is None
