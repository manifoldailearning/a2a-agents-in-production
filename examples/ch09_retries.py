"""Chapter 9: lost replies, retries and duplicate refunds."""

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.client import A2AClient, InMemory, Lost, Network
from a2asys.configs import IDEMPOTENT, LIFECYCLE
from a2asys.evals import evaluate, summarize
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.server import AgentServer
from a2asys.store import Store

REFUND = {
    "orderId": "HP-20436",
    "amount": 45.0,
    "reason": "broken headlamp",
}


def attempt(dedup: bool, reuse_id: bool) -> int:
    store = Store.fresh()
    server = AgentServer(
        DESIGNED["billing"],
        TeamAgent("billing", store, SMALL),
        dedup=dedup,
    )
    net = Network(drop=1.0)  # the first reply is always lost
    client = A2AClient(
        net.wrap(InMemory(server)), extensions=(SKILL_EXT,)
    )
    msg_id = "m-1"
    for n in range(2):
        net.drop = 1.0 if n == 0 else 0.0
        net.key = ("refund", n)
        msg = message(
            USER,
            [{"data": REFUND, "mediaType": "application/json"}],
            msg_id,
            metadata={"skillId": "refund-payment"},
        )
        try:
            client.send(msg)
        except Lost:
            if not reuse_id:
                msg_id = "m-2"
    return len(store.refunds)


def main():
    print("server dedup  client reuses id  refunds issued")
    for dedup in (False, True):
        for reuse in (False, True):
            print(
                f"{dedup!s:<13} {reuse!s:<17} "
                f"{attempt(dedup, reuse)}"
            )

    print("\nretries       ok  dupes")
    for cfg in (LIFECYCLE, IDEMPOTENT):
        s = summarize(evaluate(cfg))
        print(f"{cfg.name:<11} {s['ok']:>4.0%} {s['dupes']:>6}")


if __name__ == "__main__":
    main()
