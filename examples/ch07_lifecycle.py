"""Chapter 7: following a task through questions, approvals and
cancellation instead of treating every reply as the end."""

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.client import A2AClient, InMemory
from a2asys.configs import CONTRACTS, LIFECYCLE
from a2asys.evals import evaluate, summarize
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.server import AgentServer
from a2asys.store import Store


def show(label, task):
    status = task["status"]
    text = (
        status.get("message", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    short = text if len(text) < 38 else text[:35] + "..."
    print(f"{label:<10} {status['state'][11:]:<15} {short}")


def main():
    store = Store.fresh()
    servers = {
        k: AgentServer(DESIGNED[k], TeamAgent(k, store, SMALL))
        for k in ("returns", "orders")
    }
    returns, orders = (
        A2AClient(InMemory(servers[k]), extensions=(SKILL_EXT,))
        for k in ("returns", "orders")
    )

    def data(d):
        return [{"data": d, "mediaType": "application/json"}]

    ask = message(
        USER,
        data({"orderId": "HP-20428"}),
        "m-1",
        metadata={"skillId": "start-return"},
    )
    task = returns.send(ask)["task"]
    show("return", task)
    answer = message(
        USER,
        data({"condition": "like-new"}),
        "m-2",
        task_id=task["id"],
    )
    show("answered", returns.send(answer)["task"])

    move = message(
        USER,
        data(
            {
                "orderId": "HP-20423",
                "newAddress": "9 Birch Lane, Boise, ID",
            }
        ),
        "m-3",
        metadata={"skillId": "change-address"},
    )
    task = orders.send(move)["task"]
    show("address", task)
    store.verified.add("c11")  # the customer clicked the link
    done = message(
        USER,
        [{"text": "Customer confirmed."}],
        "m-4",
        task_id=task["id"],
    )
    show("confirmed", orders.send(done)["task"])

    late = message(
        USER,
        data(
            {
                "orderId": "HP-20426",
                "newAddress": "1 Main St, Reno, NV",
            }
        ),
        "m-5",
        metadata={"skillId": "change-address"},
    )
    task = orders.send(late)["task"]
    show("address", task)
    show("canceled", orders.cancel(task["id"]))

    print("\nlifecycle     ok  left  dupes")
    for cfg in (CONTRACTS, LIFECYCLE):
        s = summarize(evaluate(cfg))
        print(
            f"{cfg.name:<11} {s['ok']:>4.0%} {s['abandoned']:>5} "
            f"{s['dupes']:>6}"
        )


if __name__ == "__main__":
    main()
