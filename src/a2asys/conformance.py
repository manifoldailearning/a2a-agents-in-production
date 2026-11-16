"""Protocol checks any A2A server in this repository must pass.

Each check sends real JSON-RPC through a client and looks at what
comes back: the card, version negotiation, error codes, the task
state rules, and that one caller cannot see another's tasks.
"""

from __future__ import annotations

from collections.abc import Callable

from a2asys.client import A2AClient, InMemory
from a2asys.protocol import (
    METHOD_NOT_FOUND,
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    TERMINAL,
    UNSUPPORTED_OPERATION,
    USER,
    VERSION_NOT_SUPPORTED,
    A2AError,
    message,
)
from a2asys.registry import validate_card
from a2asys.server import AgentServer


def code_of(fn: Callable) -> int | None:
    try:
        fn()
    except A2AError as err:
        return err.code
    return None


def check(server: AgentServer, sample: dict) -> list:
    """Run every check. `sample` is a message that completes a
    task; the server's authenticate hook, if any, should read the
    caller from an X-Caller header so the isolation check runs."""
    results = []

    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    client = A2AClient(InMemory(server))
    record(
        "card has required fields",
        not validate_card(client.card()),
    )

    old = A2AClient(InMemory(server), version="0.3")
    record(
        "unsupported version is refused",
        code_of(lambda: old.send(sample))
        == VERSION_NOT_SUPPORTED,
    )
    record(
        "unknown method is refused",
        code_of(lambda: client.call("NoSuchMethod", {}))
        == METHOD_NOT_FOUND,
    )
    record(
        "unknown task is not found",
        code_of(lambda: client.get_task("task-9999"))
        == TASK_NOT_FOUND,
    )
    task = client.send(sample)["task"]
    state = task["status"]["state"]
    record("sample completes", state in TERMINAL, state)
    record(
        "resending the same messageId does nothing new",
        client.send(sample)["task"]["id"] == task["id"],
    )
    record(
        "terminal task cannot be canceled",
        code_of(lambda: client.cancel(task["id"]))
        == TASK_NOT_CANCELABLE,
    )
    record(
        "terminal task cannot be subscribed to",
        code_of(lambda: list(client.subscribe(task["id"])))
        == UNSUPPORTED_OPERATION,
    )
    follow = message(
        USER,
        [{"text": "one more thing"}],
        "m-follow",
        task_id=task["id"],
    )
    record(
        "terminal task accepts no more messages",
        code_of(lambda: client.send(follow))
        == UNSUPPORTED_OPERATION,
    )
    record(
        "history can be trimmed",
        client.get_task(task["id"], history_length=0)["history"]
        == [],
    )
    stranger = A2AClient(
        InMemory(server),
        extra_headers={"X-Caller": "someone-else"},
    )
    if server.authenticate is not None:
        record(
            "another caller cannot read the task",
            code_of(lambda: stranger.get_task(task["id"]))
            == TASK_NOT_FOUND,
        )
    return results
