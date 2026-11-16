"""Chapter 14: conformance checks and an evaluation gate."""

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.configs import PRODUCTION
from a2asys.conformance import check
from a2asys.evals import evaluate, gate, summarize
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.server import AgentServer
from a2asys.store import Store


def main():
    server = AgentServer(
        DESIGNED["orders"],
        TeamAgent("orders", Store.fresh(), SMALL),
        authenticate=lambda h: h.get("x-caller", "support"),
    )
    sample = message(
        USER,
        [
            {
                "data": {"orderId": "HP-20401"},
                "mediaType": "application/json",
            }
        ],
        "m-1",
        metadata={"skillId": "order-status"},
    )
    results = check(server, sample)
    for name, ok, _ in results:
        print(f"  {'pass' if ok else 'FAIL'}  {name}")
    assert all(ok for _, ok, _ in results)

    base = evaluate(PRODUCTION)
    edits = {
        "carrier: 'Where is my order?'": (
            ("carrier", "track-package", "Where is my order?"),
        ),
        "lost-parcel example": (
            (
                "billing",
                "refund-payment",
                "Refund an order whose parcel was lost",
            ),
        ),
    }
    print("\ncandidate                      ok  ships  why")
    for label, change in edits.items():
        cand = evaluate(PRODUCTION.but(name=label, edits=change))
        ok, why = gate(base, cand)
        s = summarize(cand)
        print(
            f"{label:<29} {s['ok']:>4.0%} {str(ok):>6}  "
            f"{why[0] if why else '-'}"
        )
    assert SKILL_EXT


if __name__ == "__main__":
    main()
