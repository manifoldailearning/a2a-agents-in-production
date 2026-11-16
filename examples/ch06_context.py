"""Chapter 6: contexts, follow-ups and sharing only what is
needed."""

from collections import Counter

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.client import A2AClient, InMemory
from a2asys.configs import IDEMPOTENT, NEED_TO_KNOW
from a2asys.evals import evaluate, run_task, summarize
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.requests import BY_ID
from a2asys.server import AgentServer
from a2asys.store import Store


def main():
    server = AgentServer(
        DESIGNED["orders"],
        TeamAgent("orders", Store.fresh(), SMALL),
    )
    client = A2AClient(InMemory(server), extensions=(SKILL_EXT,))

    def status(oid, n, **kw):
        parts = [
            {
                "data": {"orderId": oid},
                "mediaType": "application/json",
            }
        ]
        meta = {"skillId": "order-status"}
        msg = message(USER, parts, f"m-{n}", metadata=meta, **kw)
        return client.send(msg)["task"]

    first = status("HP-20459", 1)
    second = status(
        "HP-20459",
        2,
        context_id=first["contextId"],
        reference_task_ids=[first["id"]],
    )
    print("first ", first["id"], first["contextId"])
    print("second", second["id"], second["contextId"])
    same = client.list_tasks(contextId=first["contextId"])
    print("tasks in context:", same["totalSize"])

    print("\nwhat the courier received for t56 (lost parcel)")
    for cfg in (IDEMPOTENT, NEED_TO_KNOW):
        _, _, world = run_task(cfg, BY_ID["t56"])
        got = Counter(
            f
            for agent, fields in world.store.received
            if agent == "carrier"
            for f in fields
        )
        print(f"  {cfg.name:<13}", ", ".join(sorted(got)))

    print("\nsharing        ok  shared  tokens")
    for cfg in (IDEMPOTENT, NEED_TO_KNOW):
        s = summarize(evaluate(cfg))
        print(
            f"{cfg.name:<13} {s['ok']:>4.0%} {s['shared']:>7} "
            f"{s['tokens']:>7}"
        )


if __name__ == "__main__":
    main()
