import pytest

from a2asys import jws
from a2asys.agents import TeamAgent
from a2asys.auth import Issuer, Verifier
from a2asys.cards import DESIGNED, SKILL_EXT, cards
from a2asys.client import A2AClient, InMemory, Timeout
from a2asys.conformance import check
from a2asys.model import SMALL
from a2asys.protocol import (
    COMPLETED,
    EXTENSION_SUPPORT_REQUIRED,
    INPUT_REQUIRED,
    TASK_NOT_FOUND,
    USER,
    WORKING,
    A2AError,
    message,
)
from a2asys.push import PushReceiver, check_webhook
from a2asys.registry import Registry, sign_all, validate_card
from a2asys.schema import validate
from a2asys.server import AgentServer, AuthError, Clock
from a2asys.store import Store


def data_msg(args, skill, mid="m-1", **kw):
    return message(
        USER,
        [{"data": args, "mediaType": "application/json"}],
        mid,
        metadata={"skillId": skill},
        **kw,
    )


def server_for(key, store=None, **kw):
    store = store or Store.fresh()
    return AgentServer(
        DESIGNED[key], TeamAgent(key, store, SMALL), **kw
    )


def client_for(server, **kw):
    return A2AClient(
        InMemory(server), extensions=(SKILL_EXT,), **kw
    )


def test_every_card_is_valid():
    for card in cards(True).values():
        assert validate_card(card) == []


def test_status_task_completes_with_data():
    c = client_for(server_for("orders"))
    task = c.send(
        data_msg({"orderId": "HP-20409"}, "order-status")
    )
    task = task["task"]
    assert task["status"]["state"] == COMPLETED
    data = task["artifacts"][0]["parts"][1]["data"]
    assert data["trackingNumber"] == "PP-77204090"


def test_conformance_checks_pass():
    srv = server_for(
        "orders", authenticate=lambda h: h.get("x-caller", "me")
    )
    sample = data_msg({"orderId": "HP-20401"}, "order-status")
    results = check(srv, sample)
    assert results and all(ok for _, ok, _ in results)


def test_input_required_then_completed():
    store = Store.fresh()
    c = client_for(server_for("returns", store))
    task = c.send(
        data_msg({"orderId": "HP-20428"}, "start-return")
    )
    task = task["task"]
    assert task["status"]["state"] == INPUT_REQUIRED
    answer = message(
        USER,
        [{"data": {"condition": "used"}}],
        "m-2",
        task_id=task["id"],
    )
    assert c.send(answer)["task"]["status"]["state"] == COMPLETED
    assert store.returns == [("HP-20428", "used", "returns")]


def test_schema_errors_are_specific():
    errors = validate(
        {"orderId": "HP20431"},
        {
            "required": ["orderId"],
            "properties": {
                "orderId": {
                    "type": "string",
                    "pattern": r"^HP-\d{5}$",
                }
            },
        },
    )
    assert "must match" in errors[0]


def test_blocking_long_work_times_out_and_push_arrives():
    clock = Clock()
    receiver = PushReceiver()
    srv = AgentServer(
        DESIGNED["carrier"],
        TeamAgent("carrier", Store.fresh(), SMALL),
        clock,
        push_sender=lambda cfg, e: receiver.receive(
            {"X-A2A-Notification-Token": cfg["token"]},
            __import__("json").dumps(e),
        ),
    )
    c = client_for(srv)
    args = {
        "trackingNumber": "PP-77204570",
        "name": "Elena Petrova",
        "address": "73 Fir St, Duluth, MN",
    }
    with pytest.raises(Timeout):
        c.send(data_msg(args, "open-investigation"))
    receiver.expect_token("t")
    cfg = {
        "returnImmediately": True,
        "taskPushNotificationConfig": {
            "url": "https://hooks.harborpine.example/a2a",
            "token": "t",
        },
    }
    task = c.send(
        data_msg(args, "open-investigation", "m-2"), cfg
    )
    task = task["task"]
    assert task["status"]["state"] == WORKING
    clock.advance(3 * 24 * 3600)
    srv.tick()
    assert receiver.latest_state(task["id"]) == COMPLETED


def test_required_extension_is_enforced():
    card = dict(DESIGNED["orders"])
    card["capabilities"] = {
        "extensions": [{"uri": "urn:x", "required": True}]
    }
    srv = AgentServer(
        card, TeamAgent("orders", Store.fresh(), SMALL)
    )
    with pytest.raises(A2AError) as err:
        client_for(srv).send(
            data_msg({"orderId": "HP-20401"}, "x")
        )
    assert err.value.code == EXTENSION_SUPPORT_REQUIRED


def test_tasks_are_private_to_their_caller():
    srv = server_for(
        "orders", authenticate=lambda h: h.get("x-caller", "a")
    )
    a = client_for(srv)
    b = client_for(srv, extra_headers={"X-Caller": "b"})
    tid = a.send(
        data_msg({"orderId": "HP-20401"}, "order-status")
    )["task"]["id"]
    with pytest.raises(A2AError) as err:
        b.get_task(tid)
    assert err.value.code == TASK_NOT_FOUND


def test_tokens_check_audience_scope_and_expiry():
    clock = Clock()
    idp = Issuer("idp", b"k", clock)
    idp.register("svc", {"a", "b"})
    v = Verifier(idp, audience="https://x", required={"a"})
    ok = idp.token("svc", "https://x", {"a"})
    assert v({"authorization": "Bearer " + ok}) == "svc"
    for bad in (
        idp.token("svc", "https://y", {"a"}),
        idp.token("svc", "https://x", {"b"}),
    ):
        with pytest.raises(AuthError):
            v({"authorization": "Bearer " + bad})
    clock.advance(301)
    with pytest.raises(AuthError):
        v({"authorization": "Bearer " + ok})
    with pytest.raises(PermissionError):
        idp.exchange(ok, "other", "https://x", {"a", "b"})


def test_signed_cards_and_impostor():
    signed = sign_all(cards(True))
    keys = {k: jws.public(v) for k, v in jws.demo_keys().items()}
    assert jws.verify_card(signed["billing"], keys) == "hp-2026"
    assert jws.verify_card(signed["impostor"], keys) is None
    visible = Registry(signed, verify=True).visible()
    assert "impostor" not in visible and "carrier" in visible


def test_rs256_matches_a_reference_implementation():
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import (
        padding,
        rsa,
    )

    key = jws.demo_keys()["hp-2026"]
    pub = rsa.RSAPublicNumbers(key["e"], key["n"]).public_key()
    sig = jws.rs256_sign(b"hello", key)
    pub.verify(sig, b"hello", padding.PKCS1v15(), hashes.SHA256())
    assert crypto


def test_webhook_checks():
    assert check_webhook("https://a.harborpine.example/x") is None
    assert check_webhook("http://a.harborpine.example/x")
    assert check_webhook("https://127.0.0.1/x")
    assert check_webhook("https://localhost/x")
