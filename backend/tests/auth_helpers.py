"""Signing up and logging in through the real HTTP API, for tests that need a user."""

STRONG_PASSWORD = "Tt7!kQm2#vXz"


def register_and_login(client, email: str, password: str = STRONG_PASSWORD) -> dict:
    """Returns the login response body (access_token, refresh_token, user)."""
    client.post("/auth/register", json={"email": email, "name": "Test", "password": password})
    return client.post("/auth/login", json={"email": email, "password": password}).json()
