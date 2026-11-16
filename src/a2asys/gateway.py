"""An A2A gateway: one front door for every agent-to-agent call.

Callers reach other agents through the gateway instead of
directly. It decides which agents each caller may use, checks
what each message carries before it leaves the organization,
keeps a hop budget so delegation cannot loop, starts or continues
the trace, and writes an audit record of every call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from a2asys.agents import mentions
from a2asys.cards import NEEDS
from a2asys.protocol import (
    INVALID_PARAMS,
    A2AError,
    data_of,
    text_of,
)
from a2asys.store import PII

EXTERNAL = {"carrier"}


@dataclass
class Policy:
    allow: dict[str, set[str]]  # caller -> agents it may call
    max_hops: int = 6  # per incoming request
    external: set[str] = field(
        default_factory=lambda: set(EXTERNAL)
    )


class Gateway:
    def __init__(
        self, policy: Policy, clients: dict, customers: dict
    ):
        self.policy = policy
        self.clients = clients  # agent -> A2AClient
        self.customers = customers
        self.audit: list[dict] = []
        self.hops: dict[str, int] = {}

    def send(
        self,
        caller: str,
        agent: str,
        message: dict,
        request_id: str,
        **cfg,
    ):
        meta = message.get("metadata") or {}
        entry = {
            "request": request_id,
            "caller": caller,
            "agent": agent,
            "skill": meta.get("skillId"),
        }
        self.audit.append(entry)
        if agent not in self.policy.allow.get(caller, set()):
            entry["outcome"] = "denied: not allowed"
            raise A2AError(
                INVALID_PARAMS, f"{caller} may not call {agent}"
            )
        n = self.hops.get(request_id, 0) + 1
        self.hops[request_id] = n
        if n > self.policy.max_hops:
            entry["outcome"] = "denied: hop budget"
            raise A2AError(INVALID_PARAMS, "Hop budget exhausted")
        leaked = self.personal_data(message, entry["skill"])
        if agent in self.policy.external and leaked:
            fields = ", ".join(sorted(leaked))
            entry["outcome"] = f"denied: shares {fields}"
            raise A2AError(
                INVALID_PARAMS, f"{fields} may not go to {agent}"
            )
        out = self.clients[agent].send(message, cfg or None)
        entry["outcome"] = "sent"
        return out

    def personal_data(
        self, message: dict, skill: str | None
    ) -> set:
        """Customer fields a message carries but does not need."""
        blob = (
            text_of(message["parts"])
            + " "
            + str(data_of(message["parts"]))
        )
        blob += " " + str(message.get("metadata") or {})
        found = {
            f
            for cust in self.customers.values()
            for f in PII
            if mentions(blob, cust[f])
        }
        return found - NEEDS.get(skill or "", set())
