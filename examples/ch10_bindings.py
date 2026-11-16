"""Chapter 10: the same agent over JSON-RPC, REST and SSE."""

import json

from a2asys.agents import TeamAgent
from a2asys.cards import DESIGNED, SKILL_EXT
from a2asys.http import HttpAgent, MultiTenant
from a2asys.model import SMALL
from a2asys.protocol import CARD_PATH, request
from a2asys.server import AgentServer
from a2asys.store import Store

STATUS = {
    "message": {
        "messageId": "m-1",
        "role": "ROLE_USER",
        "parts": [{"data": {"orderId": "HP-20409"}}],
        "metadata": {"skillId": "order-status"},
    }
}


def main():
    store = Store.fresh()
    server = AgentServer(
        DESIGNED["orders"], TeamAgent("orders", store, SMALL)
    )
    http = HttpAgent(server, base="/orders")
    v1 = {"A2A-Version": "1.0", "A2A-Extensions": SKILL_EXT}

    status, headers, body = http("GET", CARD_PATH, {})
    print("GET", CARD_PATH, status, headers["Cache-Control"])

    rpc = json.dumps(request("SendMessage", STATUS, 1))
    status, headers, body = http("POST", "/orders", v1, rpc)
    task = json.loads(body)["result"]["task"]
    print(
        "POST /orders (JSON-RPC)",
        status,
        task["id"],
        task["status"]["state"],
    )
    echoed = headers.get("A2A-Extensions") == SKILL_EXT
    print("  A2A-Extensions echoed:", echoed)

    status, _, body = http(
        "GET", f"/orders/rest/tasks/{task['id']}", v1
    )
    print(
        f"GET /orders/rest/tasks/{task['id']}",
        status,
        json.loads(body)["status"]["state"],
    )
    status, _, body = http(
        "GET", "/orders/rest/tasks/task-0404", v1
    )
    print(
        "GET /orders/rest/tasks/task-0404",
        status,
        json.loads(body)["error"]["code"],
    )
    status, _, body = http("POST", "/orders", {}, rpc)
    print(
        "POST without A2A-Version",
        status,
        json.loads(body)["error"]["code"],
    )

    params = dict(
        STATUS, message=dict(STATUS["message"], messageId="m-2")
    )
    rpc = json.dumps(request("SendStreamingMessage", params, 2))
    status, headers, body = http("POST", "/orders", v1, rpc)
    print("\nstream:", headers["Content-Type"])
    for line in body.strip().split("\n\n"):
        event = json.loads(line[6:])["result"]
        kind = next(iter(event))
        print("  data:", kind)

    tenants = MultiTenant(
        {
            "eu": AgentServer(
                DESIGNED["orders"],
                TeamAgent("orders", store, SMALL),
                prefix="eu-",
            ),
            "us": AgentServer(
                DESIGNED["orders"],
                TeamAgent("orders", store, SMALL),
                prefix="us-",
            ),
        }
    )
    for tenant in ("eu", "us", "apac"):
        p = dict(
            STATUS,
            tenant=tenant,
            message=dict(STATUS["message"], messageId="m-3"),
        )
        reply, _ = tenants.handle(
            request("SendMessage", p, 3), v1
        )
        out = (
            reply.get("result", {}).get("task", {}).get("id")
            or reply["error"]["message"]
        )
        print(f"tenant {tenant:<5}", out)


if __name__ == "__main__":
    main()
