"""The ladder: from demo to production, one change a step."""

from __future__ import annotations

from a2asys.evals import Config
from a2asys.model import LARGE

DEMO = Config("demo")
CARDS = DEMO.but(name="cards", designed=True)
CONTRACTS = CARDS.but(name="contracts", contracts=True)
LIFECYCLE = CONTRACTS.but(name="lifecycle", lifecycle=True)
IDEMPOTENT = LIFECYCLE.but(name="idempotent", idempotent=True)
NEED_TO_KNOW = IDEMPOTENT.but(
    name="need-to-know", need_to_know=True
)
SECURED = NEED_TO_KNOW.but(name="secured", secured=True)
GUARDED = SECURED.but(name="guarded", guarded=True)
PRODUCTION = GUARDED.but(name="production", gateway=True)

LADDER = [
    DEMO,
    CARDS,
    CONTRACTS,
    LIFECYCLE,
    IDEMPOTENT,
    NEED_TO_KNOW,
    SECURED,
    GUARDED,
    PRODUCTION,
]

CONFIGS = {c.name: c for c in LADDER}
CONFIGS["large-model"] = PRODUCTION.but(
    name="large-model", model=LARGE
)
