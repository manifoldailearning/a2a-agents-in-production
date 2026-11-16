"""Chapter 5: prose requests versus structured skill requests."""

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SCHEMAS, SKILL_EXT
from a2asys.client import A2AClient, InMemory
from a2asys.configs import CARDS, CONTRACTS
from a2asys.evals import evaluate, summarize
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.schema import validate
from a2asys.server import AgentServer
from a2asys.store import Store


def ask(client, parts, n, skill=None):
    meta = {"skillId": skill} if skill else None
    task = client.send(
        message(USER, parts, f"m-{n}", metadata=meta)
    )["task"]
    status = task["status"]
    text = (
        status.get("message", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    if task.get("artifacts"):
        text = task["artifacts"][0]["parts"][0]["text"]
    short = text if len(text) < 44 else text[:41] + "..."
    return status["state"][11:], short


def main():
    server = AgentServer(
        DESIGNED["billing"],
        TeamAgent("billing", Store.fresh(), SMALL),
    )
    client = A2AClient(InMemory(server), extensions=(SKILL_EXT,))
    for n, prose in enumerate(
        [
            "Please refund $60 on order hp 20438, the tent "
            "poles are missing.",
            "Refund $60 for #20438, poles missing from the tent.",
        ]
    ):
        print("prose     ", *ask(client, [{"text": prose}], n))
    bad = {
        "orderId": "HP20438",
        "amount": 60,
        "reason": "missing poles",
    }
    print(
        "schema    ",
        " ".join(validate(bad, SCHEMAS["refund-payment"])),
    )
    part = [{"data": bad, "mediaType": "application/json"}]
    print("structured", *ask(client, part, 5, "refund-payment"))
    good = dict(bad, orderId="HP-20438")
    part = [{"data": good, "mediaType": "application/json"}]
    print("fixed     ", *ask(client, part, 6, "refund-payment"))

    print("\nrequests    ok  agent  stray  shared  tokens")
    for cfg in (CARDS, CONTRACTS):
        s = summarize(evaluate(cfg))
        print(
            f"{cfg.name:<10} {s['ok']:>4.0%} {s['right']:>5.0%} "
            f"{s['stray']:>6} {s['shared']:>7} {s['tokens']:>7}"
        )


if __name__ == "__main__":
    main()
