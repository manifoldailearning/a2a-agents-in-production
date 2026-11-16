"""Other ways to split the same work, for comparison.

A monolith gives one agent every skill as a tool. Micro-agents
give every skill its own agent, which then has to ask other
agents for what it does not hold itself.
"""

from __future__ import annotations

import copy

from a2asys.cards import SKILL_EXT, TEAM
from a2asys.model import rng
from a2asys.protocol import FAILED, USER, data_of, message
from a2asys.server import Outcome, Turn

# Skills that change something check the order first.
NEEDS_ORDER = {
    "cancel-order",
    "change-address",
    "start-return",
    "refund-payment",
    "price-adjustment",
    "invoice-copy",
}


def micro_cards(cards: dict[str, dict]) -> dict[str, dict]:
    """One card per skill: 'billing.refund-payment' and so on."""
    out = {}
    for team in TEAM:
        base = cards[team]
        for s in base["skills"]:
            c = copy.deepcopy(base)
            c.pop("signatures", None)
            c["name"] = s["name"]
            c["description"] = s["description"]
            c["skills"] = [s]
            for iface in c["supportedInterfaces"]:
                iface["url"] += "/" + s["id"]
            for ext in c["capabilities"].get("extensions", []):
                if ext["uri"] == SKILL_EXT:
                    schemas = ext["params"]["schemas"]
                    ext["params"]["schemas"] = {
                        s["id"]: schemas[s["id"]]
                    }
            out[f"{team}.{s['id']}"] = c
    return out


class MicroAgent:
    """A one-skill agent that asks the order agent before
    acting."""

    def __init__(
        self, executor, lookup, salt: str, counter: list
    ):
        self.executor = executor
        self.lookup = (
            lookup  # an A2AClient for orders.order-status
        )
        self.salt = salt
        self.counter = counter
        self.n = 0

    def __call__(self, turn: Turn) -> Outcome:
        if turn.phase == "start" and turn.skill in NEEDS_ORDER:
            self.n += 1
            self.counter.append(turn.skill)
            oid = data_of(turn.message["parts"]).get(
                "orderId", ""
            )
            ask = message(
                USER,
                [
                    {
                        "data": {"orderId": oid},
                        "mediaType": "application/json",
                    }
                ],
                f"dep-{self.salt}-{self.n}",
                metadata={"skillId": "order-status"},
            )
            if (
                rng("dep", self.salt, turn.skill, self.n).random()
                < 0.05
            ):
                return Outcome(
                    FAILED,
                    "The order service did not answer in time.",
                    data={"reason": "dependency_unavailable"},
                )
            self.lookup.send(ask)
        return self.executor(turn)
