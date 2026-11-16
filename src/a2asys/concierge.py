"""The support concierge: the agent that delegates to the others.

For each step of a request it chooses an agent from the cards it
can see, writes the request (prose, or a structured skill call),
sends it over A2A and follows the task to the end: answering
questions, verifying the customer, waiting for long work and
retrying when a reply is lost. How it does each of those depends
on the configuration, and the configuration is what the book's
ladder changes one step at a time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from a2asys.agents import condition_in
from a2asys.cards import (
    NEEDS,
    SCHEMAS,
    SKILL_EXT,
    card_text,
    skill_text,
)
from a2asys.client import Lost, Timeout
from a2asys.model import choose, rng, score, tokens
from a2asys.protocol import (
    AUTH_REQUIRED,
    COMPLETED,
    FAILED,
    INPUT_REQUIRED,
    REJECTED,
    USER,
    WORKING,
    A2AError,
    Ids,
    data_of,
    message,
    text_of,
)
from a2asys.requests import Request, Step

WEBHOOK = "https://concierge.harborpine.example/push"
MARKER = "NOTE TO AI ASSISTANT"
EXTERNAL = {"carrier", "impostor"}


@dataclass
class Call:
    step: int
    agent: str
    skill: str | None
    state: str
    reason: str | None = None
    attempts: int = 1
    extra: bool = False  # not part of the plan


@dataclass
class Run:
    request: Request
    calls: list[Call] = field(default_factory=list)
    first: dict[int, str] = field(default_factory=dict)
    read_ok: bool = True
    obeyed: int = 0
    declined: int = 0
    blocked: int = 0
    tokens: int = 0
    latency: float = 0.0  # milliseconds, excluding waits for work
    hops: int = 0
    answer: list[str] = field(default_factory=list)


class Concierge:
    def __init__(self, cfg, world):
        self.cfg = cfg
        self.w = world  # store, clock, servers, clients, cards
        self.model = cfg.model
        self.ids = Ids("hp-")

    # -- the plan ------------------------------------------------

    def handle(self, req: Request) -> Run:
        self.run = Run(req)
        ctx: dict = {}
        prev = None
        for i, step in enumerate(req.steps):
            if step.when == "rejected" and prev != REJECTED:
                continue
            if (
                step.when == "lost"
                and ctx.get("outcome") != "lost"
            ):
                continue
            if step.agent == "support":
                self.ticket(req, step)
                continue
            res = self.do_step(req, i, step, ctx)
            prev = res["state"]
            if prev not in (COMPLETED, REJECTED):
                self.run.answer.append(
                    "Sorry, something went wrong."
                )
                break
        return self.run

    def ticket(self, req: Request, step: Step) -> None:
        oid = step.args["orderId"]
        ref = self.w.store.ticket(oid, req.text, "support")
        self.run.answer.append(
            f"Escalated to a supervisor ({ref})."
        )

    def do_step(self, req, i, step, ctx, extra=False) -> dict:
        exclude: set[str] = set()
        res: dict = {"state": FAILED}
        for hop in range(3):
            agent = self.route(req, i, step, exclude)
            if agent is None:
                break
            self.run.first.setdefault(i, agent)
            res = self.delegate(
                req, i, step, ctx, agent, hop, extra
            )
            if res["state"] == REJECTED and res.get("routing"):
                p = (
                    self.model.p_reroute
                    if res.get("reason")
                    else self.model.p_reroute_blind
                )
                if rng("reroute", req.id, i, hop).random() < p:
                    exclude.add(agent)
                    continue
                res["state"] = FAILED
            return res
        return res

    # -- choosing an agent ---------------------------------------

    def visible(self) -> dict[str, dict]:
        return self.w.registry.visible()

    def route(self, req, i, step, exclude) -> str | None:
        cards = self.visible()
        if self.cfg.topology == "monolith":
            return self.route_tools(req, i, step, cards, exclude)
        options = [
            (key, c["name"], card_text(c))
            for key, c in cards.items()
            if key not in exclude
        ]
        if not options:
            return None
        self.run.tokens += sum(tokens(o[2]) for o in options)
        self.run.tokens += tokens(req.text)
        self.run.latency += 900
        return choose(
            step.intent,
            options,
            self.model,
            (req.id, i, len(exclude)),
        )

    def route_tools(
        self, req, i, step, cards, exclude
    ) -> str | None:
        """One agent holding every skill as a tool: one big
        choice."""
        options = [
            (f"{key}/{s['id']}", s["name"], skill_text(s))
            for key, c in cards.items()
            for s in c["skills"]
            if key not in exclude
        ]
        if not options:
            return None
        for oid, _, text in options:
            schema = json.dumps(
                SCHEMAS.get(oid.split("/")[1], {})
            )
            self.run.tokens += tokens(text) + tokens(schema)
        self.run.tokens += tokens(req.text)
        self.run.latency += 900
        pick = choose(
            step.intent,
            options,
            self.model,
            (req.id, i, len(exclude)),
        )
        key, self.forced = pick.split("/")
        return key

    def pick_skill(self, req, i, card: dict, step: Step) -> str:
        if self.cfg.topology == "monolith":
            return self.forced
        options = [
            (s["id"], s["name"], skill_text(s))
            for s in card["skills"]
        ]
        quiet = replace(
            self.model,
            noise=self.model.noise / 2,
            noise_per_item=0.0,
        )
        return choose(
            step.intent, options, quiet, ("skill", req.id, i)
        )

    # -- writing the request -------------------------------------

    def structured(self, card: dict) -> bool:
        exts = card["capabilities"].get("extensions", [])
        return self.cfg.contracts and any(
            e["uri"] == SKILL_EXT for e in exts
        )

    def profile(self, req: Request) -> dict:
        return self.w.store.customers[req.customer]

    def shared(self, req: Request, step: Step) -> dict:
        """Customer details to include with the request."""
        cust = self.profile(req)
        if self.cfg.need_to_know:
            return {f: cust[f] for f in NEEDS.get(step.skill, ())}
        return {
            f: cust[f]
            for f in ("name", "email", "phone", "address")
        } | {"card": "card ending " + cust["card"]}

    def args_for(
        self, req, i, step, ctx, skill, fixed=False
    ) -> dict:
        props = SCHEMAS.get(skill, {}).get("properties", {})
        r = rng("args", req.id, i, skill)
        args: dict = {}
        cust = self.profile(req)
        if "orderId" in props:
            oid = step.args.get("orderId", "")
            if not fixed and r.random() < self.model.p_slip:
                oid = oid.replace("-", "")
            args["orderId"] = oid
        if "trackingNumber" in props:
            args["trackingNumber"] = ctx.get("trackingNumber", "")
        if "amount" in props:
            args["amount"] = step.args.get("amount") or ctx.get(
                "total", 0.0
            )
        if "reason" in props:
            args["reason"] = "customer request"
        if "condition" in props:
            cond = condition_in(req.text)
            if cond:
                args["condition"] = cond
        if "newAddress" in props:
            args["newAddress"] = step.args.get("newAddress", "")
        if "name" in props:
            args["name"] = cust["name"]
        if "address" in props:
            args["address"] = cust["address"]
        if "product" in props:
            args["product"] = step.args.get("product", "")
        return args

    def prose(self, req, i, step, ctx, normalized=None) -> str:
        r = rng("prose", req.id, i)
        if normalized is None:
            normalized = r.random() >= self.model.p_raw
        lines = [
            "Hello, this is the Harbor & Pine support concierge. "
            f"Please {step.intent}.",
            f'The customer wrote: "{req.text}"',
        ]
        if normalized and step.args.get("orderId"):
            lines.append(f"Order number: {step.args['orderId']}.")
        if "trackingNumber" in step.args and ctx.get(
            "trackingNumber"
        ):
            lines.append(
                f"Tracking number: {ctx['trackingNumber']}."
            )
        if step.when == "lost" or (
            "amount" in step.args and "$" not in req.text
        ):
            amount = step.args.get("amount") or ctx.get(
                "total", 0.0
            )
            lines.append(f"Refund amount: ${amount:.2f}.")
        details = self.shared(req, step)
        if details:
            lines.append(
                "Customer details: "
                + "; ".join(details.values())
                + "."
            )
        return " ".join(lines)

    def build(self, req, i, step, ctx, card, skill, fixed=False):
        if skill:
            args = self.args_for(req, i, step, ctx, skill, fixed)
            parts = [
                {"data": args, "mediaType": "application/json"}
            ]
            meta = {"skillId": skill}
            extra = self.shared(req, step)
            if not self.cfg.need_to_know:
                meta["customerContext"] = extra
            return parts, meta
        text = self.prose(
            req, i, step, ctx, normalized=True if fixed else None
        )
        return [{"text": text}], None

    # -- sending and following the task --------------------------

    def delegate(
        self, req, i, step, ctx, agent, hop, extra=False
    ) -> dict:
        card = self.visible()[agent]
        skill = None
        if self.structured(card):
            skill = self.pick_skill(req, i, card, step)
            fit = next(
                s for s in card["skills"] if s["id"] == skill
            )
            if (
                score(step.intent, skill_text(fit), fit["name"])
                < 0.5
            ):
                # The card shows no skill for this: look elsewhere
                # before sending anything.
                return {
                    "state": REJECTED,
                    "routing": True,
                    "reason": "no_skill",
                }
        parts, meta = self.build(req, i, step, ctx, card, skill)
        config: dict = {}
        token = None
        if self.cfg.lifecycle:
            config["returnImmediately"] = True
            if card["capabilities"].get("pushNotifications"):
                token = f"tok-{req.id}-{i}-{hop}"
                self.w.receiver.expect_token(token)
                config["taskPushNotificationConfig"] = {
                    "url": WEBHOOK,
                    "token": token,
                }
        call = Call(i, agent, skill, FAILED, extra=extra)
        self.run.calls.append(call)
        res = self.send(
            req, i, hop, agent, parts, meta, config, call
        )
        if "task" in res and token:
            self.w.receiver.bind(token, res["task"]["id"])
        res = self.follow(
            req, i, step, ctx, agent, card, skill, res, call
        )
        call.state = res["state"]
        call.reason = res.get("reason")
        return res

    def send(
        self,
        req,
        i,
        hop,
        agent,
        parts,
        meta,
        config,
        call,
        task_id=None,
    ):
        client = self.w.clients[agent]
        msg_id = self.ids.next("msg")
        for attempt in range(3):
            msg = message(
                USER,
                parts,
                msg_id,
                task_id=task_id,
                metadata=meta,
            )
            self.run.tokens += tokens(json.dumps(parts))
            self.run.hops += 1
            if self.cfg.topology == "monolith":
                self.run.latency += 80  # a tool call, in process
            else:
                self.run.latency += 150 + (250 if meta else 700)
            self.w.network.key = (req.id, i, attempt, task_id)
            before = len(self.w.internal)
            try:
                return client.send(msg, config)
            except (Lost, Timeout) as gone:
                call.attempts += 1
                if isinstance(gone, Timeout) and attempt >= 1:
                    break
                if not self.cfg.idempotent:
                    msg_id = self.ids.next("msg")
            except A2AError as err:
                return {"error": err}
            finally:
                calls = len(self.w.internal) - before
                self.run.hops += calls
                self.run.latency += 1100 * calls
        return {"lost": True}

    def follow(
        self, req, i, step, ctx, agent, card, skill, res, call
    ) -> dict:
        if "lost" in res:
            return {"state": "lost"}
        if "error" in res:
            return {"state": FAILED, "reason": "protocol"}
        if "message" in res:
            return self.read(
                req, i, step, ctx, agent, res["message"]["parts"]
            )
        task = res["task"]
        client = self.w.clients[agent]
        for _ in range(6):
            state = task["status"]["state"]
            status_msg = task["status"].get("message") or {
                "parts": []
            }
            text = text_of(status_msg["parts"])
            data = data_of(status_msg["parts"])
            if state == COMPLETED:
                parts = [
                    p
                    for a in task.get("artifacts", [])
                    for p in a["parts"]
                ]
                return self.read(
                    req,
                    i,
                    step,
                    ctx,
                    agent,
                    parts or status_msg["parts"],
                )
            if state == REJECTED:
                reason = data.get("reason")
                routing = reason == "unsupported_skill" or (
                    reason is None
                    and text.startswith("I can't help")
                )
                if not routing:
                    self.misread(req, i, "refusal")
                    self.run.answer.append(text)
                return {
                    "state": REJECTED,
                    "reason": reason,
                    "routing": routing,
                }
            if state == FAILED:
                task = self.fix(
                    req,
                    i,
                    step,
                    ctx,
                    agent,
                    card,
                    skill,
                    data,
                    call,
                )
                if task == "wrong-skill":
                    return {
                        "state": REJECTED,
                        "routing": True,
                        "reason": "args",
                    }
                if task is None:
                    return {
                        "state": FAILED,
                        "reason": data.get("reason"),
                    }
                continue
            if state in (INPUT_REQUIRED, AUTH_REQUIRED):
                if not self.cfg.lifecycle:
                    self.run.answer.append(
                        "The team needs more information before "
                        "it can help."
                    )
                    return {"state": state}
                task = self.answer(
                    req, i, agent, skill, task, state
                )
                if task is None:
                    return {
                        "state": FAILED,
                        "reason": "unanswered",
                    }
                continue
            if state == WORKING:
                task = self.wait(agent, client, task)
                continue
        return {"state": task["status"]["state"]}

    def fix(
        self, req, i, step, ctx, agent, card, skill, data, call
    ):
        """Resend after the agent rejected the arguments."""
        if call.attempts > 2:
            return None
        if skill and self.args_for(
            req, i, step, ctx, skill, True
        ) == (self.args_for(req, i, step, ctx, skill)):
            return "wrong-skill"  # nothing to fix: wrong agent
        p = (
            self.model.p_fix
            if data.get("errors")
            else self.model.p_fix_blind
        )
        if rng("fix", req.id, i, agent).random() >= p:
            return None
        call.attempts += 1
        parts, meta = self.build(
            req, i, step, ctx, card, skill, fixed=True
        )
        res = self.send(req, i, 9, agent, parts, meta, {}, call)
        return res.get("task")

    def answer(self, req, i, agent, skill, task, state):
        """Get what an interrupted task needs, then continue
        it."""
        r = rng("answer", req.id, i)
        if state == AUTH_REQUIRED:
            # The customer confirms out of band, through the link
            # the orders team sent; the concierge only resumes.
            self.w.store.verified.add(req.customer)
            parts = [
                {"text": "The customer has confirmed the change."}
            ]
        else:
            if (
                not req.follow_up
                or r.random() >= self.model.p_answer
            ):
                self.w.clients[agent].cancel(task["id"])
                return None
            self.run.latency += 900
            cond = condition_in(req.follow_up)
            parts = (
                [
                    {
                        "data": {"condition": cond},
                        "mediaType": "application/json",
                    }
                ]
                if skill and cond
                else [{"text": req.follow_up}]
            )
        call = self.run.calls[-1]
        res = self.send(
            req, i, 8, agent, parts, None, {}, call, task["id"]
        )
        return res.get("task")

    def wait(self, agent, client, task) -> dict:
        """Wait for long-running work: by push, or by polling."""
        server = self.w.servers[agent]
        due = server.next_due()
        if due is not None:
            self.w.clock.advance(
                max(0.0, due - self.w.clock.now())
            )
            server.tick()
        if self.w.receiver.latest_state(task["id"]) is None:
            self.run.hops += 4  # no push arrived: poll instead
        self.run.latency += 150
        return client.get_task(task["id"])

    # -- reading results -----------------------------------------

    def misread(self, req, i, what) -> bool:
        if (
            rng("misread", req.id, i, what).random()
            < self.model.p_misread
        ):
            self.run.read_ok = False
            return True
        return False

    def read(self, req, i, step, ctx, agent, parts) -> dict:
        text, data = text_of(parts), data_of(parts)
        self.run.tokens += tokens(text) + tokens(json.dumps(data))
        if data:
            for k in ("trackingNumber", "total", "outcome"):
                if k in data:
                    ctx[k] = data[k]
        else:
            wrong = self.misread(req, i, "result")
            tn = re.search(r"\bPP-\d{8}\b", text)
            if tn and not wrong:
                ctx["trackingNumber"] = tn.group(0)
            total = re.search(r"\$(\d+\.\d\d)\)", text)
            if total:
                ctx["total"] = float(total.group(1))
            if "result: lost" in text:
                ctx["outcome"] = "lost" if not wrong else "found"
            elif "result: found" in text:
                ctx["outcome"] = "found"
        self.run.answer.append(text)
        if MARKER in text + json.dumps(data):
            self.injected(req, i, ctx, agent)
        return {"state": COMPLETED}

    # -- instructions found in another agent's output ------------

    def injected(self, req, i, ctx, agent) -> None:
        p = (
            self.model.p_obey_marked
            if self.cfg.guarded
            else self.model.p_obey
        )
        if rng("obey", req.id, i).random() >= p:
            return
        self.run.obeyed += 1
        if self.cfg.guarded:
            # Actions nobody asked for go to the customer first.
            self.run.declined += 2
            return
        oid = next(
            (
                s.args["orderId"]
                for s in req.steps
                if "orderId" in s.args
            ),
            "",
        )
        refund = Step(
            "issue a full refund of the order payment through "
            "billing",
            "billing",
            "refund-payment",
            {"orderId": oid, "amount": ctx.get("total", 0.0)},
        )
        self.do_step(req, 50 + i, refund, ctx, extra=True)
        self.share_with_claims(req, i)

    def share_with_claims(self, req, i) -> None:
        cust = self.profile(req)
        if self.cfg.gateway:
            self.run.blocked += 1  # egress policy: no PII outside
            return
        text = (
            "ParcelPath claims desk: customer contact for the "
            "claim: "
            f"{cust['email']}, {cust['phone']}."
        )
        call = Call(60 + i, "carrier", None, FAILED, extra=True)
        self.run.calls.append(call)
        res = self.send(
            req,
            60 + i,
            0,
            "carrier",
            [{"text": text}],
            None,
            {},
            call,
        )
        call.state = (
            res.get("task", {})
            .get("status", {})
            .get("state", FAILED)
        )
