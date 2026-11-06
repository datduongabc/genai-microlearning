"""ECS task scale-in protection for a worker that is running jobs.

Scaling in stops a task ECS picks, and a worker in the middle of a module generation would lose
it: the job sits as running until the sweeper gives it back (STUCK_THRESHOLD_SECONDS), and the
tokens already spent are spent again. A task asks the ECS agent to protect it while it has a job
and lets the protection go when idle, so scaling in only removes idle workers.

Only on ECS, where the agent sets ECS_AGENT_URI. A failed call is logged and tried again on the
next tick: the protection expires on its own, so a worker cannot stay protected after it dies.
"""

import logging
import os
import threading

import httpx

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15
# Renewed every tick while busy, so this only matters when the worker stops renewing it.
PROTECTION_MINUTES = 30


class ScaleInProtection:
    def __init__(self, agent_uri: str, busy, client: httpx.Client | None = None):
        self.url = f"{agent_uri}/task-protection/v1/state"
        self.busy = busy
        self.client = client or httpx.Client(timeout=5)
        self.protected: bool | None = None

    def tick(self) -> None:
        want = self.busy()
        # Renewed on every busy tick, not only on a change: the protection expires.
        if not want and self.protected is False:
            return
        body = {"ProtectionEnabled": want}
        if want:
            body["ExpiresInMinutes"] = PROTECTION_MINUTES
        try:
            response = self.client.put(self.url, json=body)
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning("scale_in_protection_failed enabled=%s error=%s", want, error)
            return
        if want != self.protected:
            logger.info("scale_in_protection enabled=%s", want)
        self.protected = want


def start(stop: threading.Event, busy) -> threading.Thread | None:
    agent_uri = os.environ.get("ECS_AGENT_URI")
    if not agent_uri:
        return None
    protection = ScaleInProtection(agent_uri, busy)

    def loop():
        while True:
            protection.tick()
            if stop.wait(CHECK_INTERVAL_SECONDS):
                return

    thread = threading.Thread(target=loop, name="scale-in-protection", daemon=True)
    thread.start()
    return thread
