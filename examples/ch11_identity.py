"""Chapter 11: who is calling, for whom, and with what rights."""

import re

from a2asys.agents import TeamAgent
from a2asys.auth import Issuer, Verifier
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.client import A2AClient, InMemory
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.server import AgentServer, AuthError, Clock
from a2asys.store import Store

BILLING = "https://agents.harborpine.example/billing"
ORDERS = "https://agents.harborpine.example/orders"


def short(challenge: str) -> str:
    """The error code and detail from a WWW-Authenticate value."""
    found = re.search(r'error="([^"]+)"(?:, (.*))?', challenge)
    if not found:
        return "no credentials"
    words = [x for x in found.groups() if x]
    detail = " ".join(words).replace('"', "")
    return detail.replace("scope=", "")


def main():
    clock = Clock()
    idp = Issuer(
        "https://login.harborpine.example", b"demo-secret", clock
    )
    idp.register(
        "support-concierge", {"refunds:write", "invoices:read"}
    )
    idp.register("returns-agent", {"refunds:write"})
    verifier = Verifier(
        idp, audience=BILLING, required={"refunds:write"}
    )
    server = AgentServer(
        DESIGNED["billing"],
        TeamAgent("billing", Store.fresh(), SMALL),
        clock,
        authenticate=verifier,
        extended_card={"name": "Billing and Refunds (internal)"},
    )
    refund = message(
        USER,
        [
            {
                "data": {
                    "orderId": "HP-20440",
                    "amount": 38.0,
                    "reason": "missing",
                },
                "mediaType": "application/json",
            }
        ],
        "m-1",
        metadata={"skillId": "refund-payment"},
    )

    def attempt(label, token):
        client = A2AClient(
            InMemory(server),
            extensions=(SKILL_EXT,),
            token=(lambda: token) if token else None,
        )
        try:
            task = client.send(refund)["task"]
            print(
                f"{label:<25} {task['status']['state'][11:]} as "
                f"{verifier.checked[-1]['sub']}"
            )
        except AuthError as err:
            print(f"{label:<25} 401 {short(err.challenge)}")

    attempt("no token", None)
    attempt(
        "token for orders",
        idp.token("support-concierge", ORDERS, {"refunds:write"}),
    )
    attempt(
        "read-only token",
        idp.token(
            "support-concierge", BILLING, {"invoices:read"}
        ),
    )
    good = idp.token(
        "support-concierge", BILLING, {"refunds:write"}
    )
    attempt("right audience and scope", good)
    clock.advance(600)
    attempt("same token, 10 min later", good)

    user = idp.token(
        "support-concierge",
        BILLING,
        {"refunds:write", "invoices:read"},
    )
    on_behalf = idp.exchange(
        user, "returns-agent", BILLING, {"refunds:write"}
    )
    claims = idp.read(on_behalf)
    print("\nexchanged token: sub", claims["sub"])
    print("  act", claims["act"]["sub"], "scope", claims["scope"])
    try:
        idp.exchange(
            user,
            "returns-agent",
            BILLING,
            {"refunds:write", "payouts:write"},
        )
    except PermissionError as err:
        print("widening scopes:", err)

    client = A2AClient(InMemory(server), token=lambda: on_behalf)
    print("extended card:", client.extended_card()["name"])
    try:
        A2AClient(InMemory(server)).extended_card()
    except AuthError as err:
        print(
            "extended card, no token: 401", short(err.challenge)
        )


if __name__ == "__main__":
    main()
