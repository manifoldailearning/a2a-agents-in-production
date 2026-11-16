"""An A2A client and the in-memory transport the examples use.

The transport serializes every request and reply to JSON, so
nothing passes between client and server that would not survive
a real network. It also plays the network's part in the failures
that matter: a reply that never arrives, and a blocking call that
outlives the client's patience.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable

from a2asys.model import rng
from a2asys.protocol import (
    INTERRUPTED,
    TERMINAL,
    VERSION,
    A2AError,
    request,
)
from a2asys.server import AgentServer, Clock, Pending


class Lost(Exception):
    """The request may have been processed; no reply came."""


class Timeout(Exception):
    """The client stopped waiting before the server answered."""


class InMemory:
    """Carries JSON-RPC to an AgentServer in the same process."""

    def __init__(
        self,
        server: AgentServer,
        clock: Clock | None = None,
        timeout: float = 30.0,
    ):
        self.server = server
        self.clock = clock or server.clock
        self.timeout = timeout
        self.ids = itertools.count(1)
        self.sent: list[dict] = []

    def rpc(
        self, method: str, params: dict, headers: dict
    ) -> dict:
        rid = next(self.ids)
        wire = json.loads(
            json.dumps(request(method, params, rid))
        )
        self.sent.append(wire)
        try:
            reply, _ = self.server.handle(wire, headers)
        except Pending as p:
            if p.wait > self.timeout:
                self.clock.advance(self.timeout)
                raise Timeout(
                    f"No reply within {self.timeout:.0f}s"
                ) from p
            self.clock.advance(p.wait)
            self.server.tick()
            got = request("GetTask", {"id": p.task_id}, rid)
            reply, _ = self.server.handle(got, headers)
            if "result" in reply:
                reply = {
                    **reply,
                    "result": {"task": reply["result"]},
                }
        reply = json.loads(json.dumps(reply))
        if "error" in reply:
            e = reply["error"]
            raise A2AError(e["code"], e["message"], e.get("data"))
        return reply["result"]

    def stream(self, method: str, params: dict, headers: dict):
        """Yield StreamResponse events until the task settles."""
        first = self.rpc(method, params, headers)
        yield from first["events"]
        if "taskId" not in first:
            return
        task_id, seen = first["taskId"], first["next"]
        while True:
            rec = self.server.tasks[task_id]
            state = rec.task["status"]["state"]
            if state in TERMINAL or state in INTERRUPTED:
                return
            due = self.server.next_due()
            if (
                due is None
                or due - self.clock.now() > self.timeout
            ):
                return  # the stream closes; resubscribe or poll
            self.clock.advance(max(0.0, due - self.clock.now()))
            self.server.tick()
            new = self.server.events_since(task_id, seen)
            seen += len(new)
            yield from (json.loads(json.dumps(e)) for e in new)


class A2AClient:
    """The operations a calling agent uses, over any transport."""

    def __init__(
        self,
        transport,
        extensions: tuple[str, ...] = (),
        token: Callable[[], str] | None = None,
        version: str = VERSION,
        extra_headers: dict | None = None,
    ):
        self.transport = transport
        self.extensions = extensions
        self.token = token
        self.version = version
        self.extra_headers = extra_headers or {}

    def headers(self) -> dict:
        h = {"A2A-Version": self.version, **self.extra_headers}
        if self.extensions:
            h["A2A-Extensions"] = ",".join(self.extensions)
        if self.token:
            h["Authorization"] = "Bearer " + self.token()
        return h

    def call(self, method: str, params: dict) -> dict:
        return self.transport.rpc(method, params, self.headers())

    def card(self) -> dict:
        """Fetch the public Agent Card (well-known path)."""
        return json.loads(json.dumps(self.transport.server.card))

    def send(
        self, message: dict, configuration: dict | None = None
    ):
        params = {"message": message}
        if configuration:
            params["configuration"] = configuration
        return self.call("SendMessage", params)

    def stream(
        self, message: dict, configuration: dict | None = None
    ):
        params = {"message": message}
        if configuration:
            params["configuration"] = configuration
        return self.transport.stream(
            "SendStreamingMessage", params, self.headers()
        )

    def get_task(self, task_id: str, history_length=None) -> dict:
        params = {"id": task_id}
        if history_length is not None:
            params["historyLength"] = history_length
        return self.call("GetTask", params)

    def list_tasks(self, **filters) -> dict:
        return self.call("ListTasks", filters)

    def cancel(self, task_id: str) -> dict:
        return self.call("CancelTask", {"id": task_id})

    def subscribe(self, task_id: str):
        return self.transport.stream(
            "SubscribeToTask", {"id": task_id}, self.headers()
        )

    def set_push(self, task_id: str, config: dict) -> dict:
        return self.call(
            "CreateTaskPushNotificationConfig",
            {**config, "taskId": task_id},
        )

    def extended_card(self) -> dict:
        return self.call("GetExtendedAgentCard", {})


class Network:
    """Loses some replies to slow writes, reproducibly.

    Refunds wait on a payment processor, and a share of their
    replies never make it back, although the refund happened.
    The concierge sets `key` before each send so the same request
    meets the same network in every configuration.
    """

    SLOW = ("refund", "price-adjustment")

    def __init__(self, drop: float):
        self.drop = drop
        self.key: tuple = ()
        self.lost = 0

    def wrap(self, transport) -> Flaky:
        return Flaky(transport, self)

    def slow(self, out: dict) -> bool:
        arts = (out.get("task") or {}).get("artifacts", [])
        return any(a["name"] in self.SLOW for a in arts)


class Flaky:
    def __init__(self, inner, net: Network):
        self.inner = inner
        self.net = net
        self.server = inner.server

    def rpc(
        self, method: str, params: dict, headers: dict
    ) -> dict:
        out = self.inner.rpc(method, params, headers)
        if (
            method == "SendMessage"
            and self.net.slow(out)
            and rng("drop", *self.net.key).random()
            < self.net.drop
        ):
            self.net.lost += 1
            raise Lost("reply lost after the work was done")
        return out

    def stream(self, method: str, params: dict, headers: dict):
        return self.inner.stream(method, params, headers)
