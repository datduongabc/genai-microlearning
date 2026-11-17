import json


def events(response) -> list[tuple[str, dict]]:
    """The (name, data) pairs of a chat stream's server-sent events, heartbeats left out."""
    found = []
    for block in response.text.split("\n\n"):
        if block.startswith("event:"):
            name, data = block.split("\n", 1)
            found.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return found
