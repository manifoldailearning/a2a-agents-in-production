"""Chapter 8: long-running work, streamed and pushed."""

import json

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.client import A2AClient, InMemory, Timeout
from a2asys.model import SMALL
from a2asys.protocol import USER, message
from a2asys.push import PushReceiver, PushSender, check_webhook
from a2asys.server import AgentServer, Clock
from a2asys.store import Store

HOOK = "https://concierge.harborpine.example/push"


def investigation(n):
    args = {
        "trackingNumber": "PP-77204570",
        "name": "Elena Petrova",
        "address": "73 Fir St, Duluth, MN",
    }
    return message(
        USER,
        [{"data": args, "mediaType": "application/json"}],
        f"m-{n}",
        metadata={"skillId": "open-investigation"},
    )


def verdict(url):
    return check_webhook(url, ("harborpine.example",)) or "ok"


def event_line(e):
    if "task" in e:
        return f"task {e['task']['status']['state'][11:]}"
    if "statusUpdate" in e:
        return (
            f"status {e['statusUpdate']['status']['state'][11:]}"
        )
    art = e["artifactUpdate"]["artifact"]
    text = art["parts"][0]["text"][:36]
    return f"artifact {art['name']}: {text}..."


def main():
    clock = Clock()
    receiver = PushReceiver()
    sender = PushSender(
        lambda url, h, body: receiver.receive(h, body),
        allow=("harborpine.example",),
        duplicate_every=2,
    )
    server = AgentServer(
        DESIGNED["carrier"],
        TeamAgent("carrier", Store.fresh(), SMALL),
        clock,
        push_sender=sender,
    )
    client = A2AClient(
        InMemory(server, timeout=30), extensions=(SKILL_EXT,)
    )

    try:
        client.send(investigation(1))
    except Timeout as err:
        print("blocking call:", err)

    receiver.expect_token("tok-77")
    cfg = {
        "returnImmediately": True,
        "taskPushNotificationConfig": {
            "url": HOOK,
            "token": "tok-77",
        },
    }
    task = client.send(investigation(2), cfg)["task"]
    receiver.bind("tok-77", task["id"])
    print(
        "non-blocking:", task["id"], task["status"]["state"][11:]
    )
    clock.advance(2 * 24 * 3600)
    server.tick()
    print(
        "pushed state:",
        receiver.latest_state(task["id"])[11:],
        "| duplicates ignored:",
        receiver.duplicates,
    )
    bogus = json.dumps({"statusUpdate": {"taskId": task["id"]}})
    status = receiver.receive(
        {"X-A2A-Notification-Token": "guess"}, bogus
    )
    print("forged notification:", status)

    print("\nstreamed:")
    long_wait = A2AClient(
        InMemory(server, timeout=3 * 24 * 3600),
        extensions=(SKILL_EXT,),
    )
    for e in long_wait.stream(investigation(3)):
        print("  " + event_line(e))

    print("\nwebhook checks:")
    for url in (
        HOOK,
        "http://concierge.harborpine.example/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.7/hook",
        "https://attacker.example/hook",
    ):
        print(f"  {url:<42} {verdict(url)}")


if __name__ == "__main__":
    main()
