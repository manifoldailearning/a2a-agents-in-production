"""Chapter 2: an Agent Card, a message, a task and its errors."""

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.client import A2AClient, InMemory
from a2asys.model import SMALL
from a2asys.protocol import ERROR_NAMES, USER, A2AError, message
from a2asys.server import AgentServer
from a2asys.store import Store


def main():
    store = Store.fresh()
    server = AgentServer(
        DESIGNED["orders"], TeamAgent("orders", store, SMALL)
    )
    client = A2AClient(InMemory(server), extensions=(SKILL_EXT,))

    card = client.card()
    print("card:", card["name"])
    for iface in card["supportedInterfaces"]:
        print("  ", iface["protocolBinding"], iface["url"])
    print("  skills:", [s["id"] for s in card["skills"]])

    msg = message(
        USER,
        [
            {
                "data": {"orderId": "HP-20402"},
                "mediaType": "application/json",
            }
        ],
        "msg-0001",
        metadata={"skillId": "order-status"},
    )
    task = client.send(msg)["task"]
    print("\nSendMessage ->", task["id"], task["status"]["state"])
    art = task["artifacts"][0]
    print("artifact:", art["name"])
    print("  text:", art["parts"][0]["text"][:52] + "...")
    data = art["parts"][1]["data"]
    print("  data:", data["status"], data["trackingNumber"])

    again = client.get_task(task["id"], history_length=0)
    print(
        "\nGetTask history:",
        again["history"],
        "context:",
        again["contextId"],
    )

    follow = message(
        USER,
        [{"text": "and the other one?"}],
        "msg-0002",
        task_id=task["id"],
    )
    for label, call in [
        (
            "message to a completed task",
            lambda: client.send(follow),
        ),
        ("unknown task", lambda: client.get_task("task-0999")),
        (
            "cancel a completed task",
            lambda: client.cancel(task["id"]),
        ),
        (
            "client speaking 0.3",
            lambda: A2AClient(
                InMemory(server), version="0.3"
            ).send(msg),
        ),
    ]:
        try:
            call()
        except A2AError as err:
            name = ERROR_NAMES[err.code]
            print(f"{label:<28} {err.code} {name}")


if __name__ == "__main__":
    main()
