"""Tracing and metrics across agents.

The calling agent starts a W3C trace and sends `traceparent` with
every A2A request; each agent records a span with the parent it
was given and passes the trace on when it calls another agent.
Spans record the agent, method, skill, task state and duration,
never message contents.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from a2asys.model import rng
from a2asys.server import AgentServer

# Simulated time each agent takes per request, in milliseconds.
LATENCY = {
    "orders": 180,
    "returns": 260,
    "billing": 900,
    "carrier": 420,
    "catalog": 120,
}


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str
    agent: str
    method: str
    skill: str
    state: str
    ms: float


class Tracer:
    def __init__(self):
        self.spans: list[Span] = []
        self.n = 0

    def new_id(self) -> str:
        self.n += 1
        return f"{self.n:016x}"

    def traceparent(self, trace_id: str, span_id: str) -> str:
        return f"00-{trace_id}-{span_id}-01"


class Traced:
    """Wraps an AgentServer so every request records a span."""

    def __init__(
        self, server: AgentServer, agent: str, tracer: Tracer
    ):
        self.server = server
        self.agent = agent
        self.tracer = tracer
        self.card = server.card
        self.clock = server.clock
        self.tasks = server.tasks

    def __getattr__(self, name):
        return getattr(self.server, name)

    def handle(self, rpc: dict, headers: dict):
        lower = {k.lower(): v for k, v in headers.items()}
        parent = lower.get("traceparent", "")
        trace_id, parent_id = "", ""
        if parent.count("-") == 3:
            _, trace_id, parent_id, _ = parent.split("-")
        reply, extra = self.server.handle(rpc, headers)
        result = reply.get("result") or {}
        task = result.get("task") or (
            result if "status" in result else {}
        )
        state = task.get("status", {}).get("state", "")
        if "message" in result:
            state = "MESSAGE"
        if "error" in reply:
            state = f"ERROR {reply['error']['code']}"
        meta = (
            (rpc.get("params") or {}).get("message") or {}
        ).get("metadata") or {}
        r = rng("ms", self.agent, trace_id, self.tracer.n)
        ms = LATENCY.get(self.agent, 200) * (0.6 + r.random())
        if r.random() < 0.05:
            ms *= 6  # an occasional slow call
        self.tracer.spans.append(
            Span(
                trace_id=trace_id,
                span_id=self.tracer.new_id(),
                parent_id=parent_id,
                agent=self.agent,
                method=rpc.get("method", ""),
                skill=meta.get("skillId", ""),
                state=state.replace("TASK_STATE_", ""),
                ms=round(ms),
            )
        )
        return reply, extra


def metrics(spans: list[Span]) -> dict[str, dict]:
    by: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        by[s.agent].append(s)
    out = {}
    for agent, group in sorted(by.items()):
        ms = sorted(s.ms for s in group)
        out[agent] = {
            "calls": len(group),
            "errors": sum(
                s.state.startswith(("ERROR", "FAILED"))
                for s in group
            ),
            "p50": statistics.median(ms),
            "p95": ms[min(len(ms) - 1, int(0.95 * len(ms)))],
        }
    return out
