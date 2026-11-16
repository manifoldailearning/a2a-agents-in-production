"""The HTTP bindings: JSON-RPC, HTTP+JSON (REST) and the card.

HttpAgent serves one AgentServer the way a web framework would:
the Agent Card at its well-known path, JSON-RPC at the interface
URL, the same operations as REST routes, and Server-Sent Events
for streams. Requests and responses are (status, headers, body)
triples, so the examples can show the exact bytes on the wire.
"""

from __future__ import annotations

import json
import re

from a2asys.protocol import (
    CARD_PATH,
    CONTENT_TYPE_NOT_SUPPORTED,
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    UNSUPPORTED_OPERATION,
    VERSION_NOT_SUPPORTED,
    A2AError,
    error,
    request,
)
from a2asys.server import AgentServer, AuthError

A2A_JSON = "application/a2a+json"

# A2A error codes as HTTP statuses on the REST binding.
HTTP_STATUS = {
    TASK_NOT_FOUND: 404,
    TASK_NOT_CANCELABLE: 409,
    UNSUPPORTED_OPERATION: 400,
    CONTENT_TYPE_NOT_SUPPORTED: 415,
    VERSION_NOT_SUPPORTED: 400,
    INVALID_PARAMS: 400,
}

ROUTES = [
    ("POST", r"/message:send", "SendMessage"),
    ("POST", r"/message:stream", "SendStreamingMessage"),
    ("GET", r"/tasks/(?P<id>[^/:]+)", "GetTask"),
    ("GET", r"/tasks", "ListTasks"),
    ("POST", r"/tasks/(?P<id>[^/:]+):cancel", "CancelTask"),
    (
        "POST",
        r"/tasks/(?P<id>[^/:]+):subscribe",
        "SubscribeToTask",
    ),
    ("GET", r"/extendedAgentCard", "GetExtendedAgentCard"),
]


def sse(events: list[dict], rid=None) -> str:
    """Server-Sent Events: one data line per event."""
    lines = []
    for e in events:
        payload = (
            e
            if rid is None
            else {"jsonrpc": "2.0", "id": rid, "result": e}
        )
        lines.append("data: " + json.dumps(payload) + "\n\n")
    return "".join(lines)


class HttpAgent:
    def __init__(self, server: AgentServer, base: str = ""):
        self.server = server
        self.base = base  # path prefix of the JSON-RPC interface

    def __call__(
        self,
        method: str,
        path: str,
        headers: dict,
        body: str = "",
    ):
        try:
            if method == "GET" and path == CARD_PATH:
                return (
                    200,
                    self.card_headers(),
                    json.dumps(self.server.card),
                )
            if path.startswith(self.base + "/rest"):
                return self.rest(
                    method,
                    path[len(self.base) + 5 :],
                    headers,
                    body,
                )
            if method == "POST" and path == self.base:
                return self.jsonrpc(headers, body)
            return 404, {}, ""
        except AuthError as err:
            return 401, {"WWW-Authenticate": err.challenge}, ""

    def card_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=300",
        }

    def jsonrpc(self, headers: dict, body: str):
        try:
            rpc = json.loads(body)
        except json.JSONDecodeError:
            err = A2AError(PARSE_ERROR, "Body is not JSON")
            return 200, {}, json.dumps(error(None, err))
        if rpc.get("jsonrpc") != "2.0" or "method" not in rpc:
            err = A2AError(
                INVALID_REQUEST, "Not a JSON-RPC 2.0 request"
            )
            return 200, {}, json.dumps(error(rpc.get("id"), err))
        reply, extra = self.server.handle(rpc, headers)
        out = {"Content-Type": "application/json"}
        if extra.get("a2a-extensions"):
            out["A2A-Extensions"] = extra["a2a-extensions"]
        result = reply.get("result")
        if isinstance(result, dict) and "events" in result:
            out["Content-Type"] = "text/event-stream"
            return 200, out, sse(result["events"], reply["id"])
        return 200, out, json.dumps(reply)

    def rest(
        self, method: str, path: str, headers: dict, body: str
    ):
        route, rest_path = path.split("?")[0], path
        for verb, pattern, op in ROUTES:
            m = re.fullmatch(pattern, route)
            if verb == method and m:
                params = json.loads(body) if body else {}
                params.update(m.groupdict())
                params.update(query(rest_path))
                reply, _ = self.server.handle(
                    request(op, params, 0), headers
                )
                if "error" in reply:
                    code = reply["error"]["code"]
                    status = HTTP_STATUS.get(code, 500)
                    return (
                        status,
                        {"Content-Type": A2A_JSON},
                        json.dumps({"error": reply["error"]}),
                    )
                result = reply["result"]
                if (
                    isinstance(result, dict)
                    and "events" in result
                ):
                    return (
                        200,
                        {"Content-Type": "text/event-stream"},
                        sse(result["events"]),
                    )
                return (
                    200,
                    {"Content-Type": A2A_JSON},
                    json.dumps(result),
                )
        return 404, {}, ""


def query(path: str) -> dict:
    if "?" not in path:
        return {}
    out = {}
    for pair in path.split("?", 1)[1].split("&"):
        k, _, v = pair.partition("=")
        out[k] = int(v) if v.isdigit() else v
    return out


class HttpTransport:
    """A client transport that speaks JSON-RPC over HttpAgent."""

    def __init__(self, agent: HttpAgent, path: str = ""):
        self.agent = agent
        self.server = agent.server
        self.path = path or agent.base
        self.n = 0
        self.wire: list[tuple] = []

    def rpc(
        self, method: str, params: dict, headers: dict
    ) -> dict:
        self.n += 1
        body = json.dumps(request(method, params, self.n))
        h = {"Content-Type": "application/json", **headers}
        status, rh, out = self.agent("POST", self.path, h, body)
        self.wire.append((method, status, rh))
        if status == 401:
            raise AuthError(rh.get("WWW-Authenticate", ""))
        if rh.get("Content-Type") == "text/event-stream":
            events = [
                json.loads(line[6:])["result"]
                for line in out.split("\n\n")
                if line.startswith("data: ")
            ]
            return {"events": events}
        reply = json.loads(out)
        if "error" in reply:
            e = reply["error"]
            raise A2AError(e["code"], e["message"], e.get("data"))
        return reply["result"]

    def stream(self, method: str, params: dict, headers: dict):
        yield from self.rpc(method, params, headers)["events"]


class MultiTenant:
    """One endpoint hosting several agents, chosen by `tenant`."""

    def __init__(self, servers: dict[str, AgentServer]):
        self.servers = servers

    def handle(self, rpc: dict, headers: dict):
        tenant = (rpc.get("params") or {}).get("tenant", "")
        server = self.servers.get(tenant)
        if server is None:
            err = A2AError(
                INVALID_PARAMS, f"Unknown tenant: {tenant!r}"
            )
            return error(rpc.get("id"), err), {}
        return server.handle(rpc, headers)
