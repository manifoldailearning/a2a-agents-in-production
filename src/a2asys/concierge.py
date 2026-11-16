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
    INTERRUPTED,
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


@dataclass
class Job:
    """One step of a request, sent to one agent."""

    req: Request
    i: int  # step number
    step: Step
    ctx: dict  # values read from earlier results
    agent: str
    card: dict
    skill: str | None  # set when the request is structured
    call: Call
    hop: int = 0


class Concierge:
    def __init__(self, cfg, world):
        self.cfg = cfg
        self.w = world  # store, clock, servers, clients, cards
        self.model = cfg.model
        self.ids = Ids("hp-")
        self.forced = ""

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
            prev = self.do_step(req, i, step, ctx)["state"]
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
        """Delegate one step; on a refusal, try another agent."""
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
        key = (req.id, i, len(exclude))
        return choose(step.intent, options, self.model, key)

    def route_tools(self, req, i, step, cards, exclude):
        """One agent holding every skill as a tool: one choice."""
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
        key = (req.id, i, len(exclude))
        pick = choose(step.intent, options, self.model, key)
        agent, self.forced = pick.split("/")
        return agent

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
        speaks = any(e["uri"] == SKILL_EXT for e in exts)
        return self.cfg.contracts and speaks

    def profile(self, req: Request) -> dict:
        return self.w.store.customers[req.customer]

    def shared(self, req: Request, step: Step) -> dict:
        """Customer details to include with the request."""
        cust = self.profile(req)
        if self.cfg.need_to_know:
            return {f: cust[f] for f in NEEDS.get(step.skill, ())}
        everything = ("name", "email", "phone", "address")
        details = {f: cust[f] for f in everything}
        details["card"] = "card ending " + cust["card"]
        return details

    def args_for(self, job: Job, fixed: bool = False) -> dict:
        """Arguments for the skill's schema, as a model fills."""
        req, step, ctx = job.req, job.step, job.ctx
        props = SCHEMAS.get(job.skill, {}).get("properties", {})
        r = rng("args", req.id, job.i, job.skill)
        cust = self.profile(req)
        args: dict = {}
        if "orderId" in props:
            oid = step.args.get("orderId", "")
            if not fixed and r.random() < self.model.p_slip:
                oid = oid.replace(
                    "-", ""
                )  # HP20431, not HP-20431
            args["orderId"] = oid
        if "trackingNumber" in props:
            args["trackingNumber"] = ctx.get("trackingNumber", "")
        if "amount" in props:
            total = ctx.get("total", 0.0)
            args["amount"] = step.args.get("amount") or total
        if "reason" in props:
            args["reason"] = "customer request"
        if "condition" in props and condition_in(req.text):
            args["condition"] = condition_in(req.text)
        if "newAddress" in props:
            args["newAddress"] = step.args.get("newAddress", "")
        for f in ("name", "address"):
            if f in props:
                args[f] = cust[f]
        if "product" in props:
            args["product"] = step.args.get("product", "")
        return args

    def prose(self, job: Job, normalized=None) -> str:
        """A request written as a message to a colleague."""
        req, step, ctx = job.req, job.step, job.ctx
        if normalized is None:
            r = rng("prose", req.id, job.i)
            normalized = r.random() >= self.model.p_raw
        lines = [
            "Hello, this is the Harbor & Pine support "
            f"concierge. Please {step.intent}.",
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
        no_amount = "amount" in step.args and "$" not in req.text
        if step.when == "lost" or no_amount:
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

    def build(self, job: Job, fixed: bool = False):
        """The parts and metadata of the request message."""
        if job.skill:
            args = self.args_for(job, fixed)
            parts = [
                {"data": args, "mediaType": "application/json"}
            ]
            meta = {"skillId": job.skill}
            if not self.cfg.need_to_know:
                meta["customerContext"] = self.shared(
                    job.req, job.step
                )
            return parts, meta
        text = self.prose(job, normalized=True if fixed else None)
        return [{"text": text}], None

    # -- sending and following the task --------------------------

    def delegate(
        self, req, i, step, ctx, agent, hop, extra=False
    ):
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
        call = Call(i, agent, skill, FAILED, extra=extra)
        self.run.calls.append(call)
        job = Job(
            req, i, step, ctx, agent, card, skill, call, hop
        )
        parts, meta = self.build(job)
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
        res = self.send(job, parts, meta, config)
        if "task" in res and token:
            self.w.receiver.bind(token, res["task"]["id"])
        res = self.follow(job, res)
        call.state, call.reason = res["state"], res.get("reason")
        return res

    def send(
        self, job, parts, meta, config, task_id=None
    ) -> dict:
        """Send one message, retrying when no reply comes back."""
        client = self.w.clients[job.agent]
        msg_id = self.ids.next("msg")
        for attempt in range(3):
            msg = message(
                USER,
                parts,
                msg_id,
                task_id=task_id,
                metadata=meta,
            )
            self.cost(parts, meta)
            self.w.network.key = (
                job.req.id,
                job.i,
                attempt,
                task_id,
            )
            before = len(self.w.internal)
            try:
                return client.send(msg, config)
            except (Lost, Timeout) as gone:
                job.call.attempts += 1
                if isinstance(gone, Timeout) and attempt >= 1:
                    break
                if not self.cfg.idempotent:
                    msg_id = self.ids.next("msg")  # a new message
            except A2AError as err:
                return {"error": err}
            finally:
                inner = len(self.w.internal) - before
                self.run.hops += inner
                self.run.latency += 1100 * inner
        return {"lost": True}

    def cost(self, parts, meta) -> None:
        self.run.tokens += tokens(json.dumps(parts))
        self.run.hops += 1
        if self.cfg.topology == "monolith":
            self.run.latency += 80  # a tool call, in process
        else:
            self.run.latency += 150 + (250 if meta else 700)

    def follow(self, job: Job, res: dict) -> dict:
        """Follow a task until it ends or the client gives up."""
        if "lost" in res:
            return {"state": "lost"}
        if "error" in res:
            return {"state": FAILED, "reason": "protocol"}
        if "message" in res:
            return self.read(job, res["message"]["parts"])
        task = res["task"]
        for _ in range(6):
            state = task["status"]["state"]
            if state == COMPLETED:
                return self.read(job, outputs(task))
            if state == REJECTED:
                return self.refused(job, task)
            if state == FAILED:
                task = self.fix(job, task)
                if task is None:
                    return {"state": FAILED, "reason": "invalid"}
                if task == "misrouted":
                    return {
                        "state": REJECTED,
                        "routing": True,
                        "reason": "args",
                    }
            elif state in INTERRUPTED:
                if not self.cfg.lifecycle:
                    self.run.answer.append(
                        "The team needs more information first."
                    )
                    return {"state": state}  # the task stays open
                task = self.answer(job, task)
                if task is None:
                    return {
                        "state": FAILED,
                        "reason": "unanswered",
                    }
            elif state == WORKING:
                task = self.wait(job, task)
        return {"state": task["status"]["state"]}

    def refused(self, job: Job, task: dict) -> dict:
        """A refusal: the wrong agent, or a business rule."""
        status = task["status"].get("message") or {"parts": []}
        text, data = (
            text_of(status["parts"]),
            data_of(status["parts"]),
        )
        reason = data.get("reason")
        vague = reason is None and text.startswith("I can't help")
        routing = reason == "unsupported_skill" or vague
        if not routing:
            self.misread(job, "refusal")
            self.run.answer.append(text)
        return {
            "state": REJECTED,
            "reason": reason,
            "routing": routing,
        }

    def fix(self, job: Job, task: dict):
        """Resend after the agent rejected the arguments."""
        if job.call.attempts > 2:
            return None
        if job.skill and self.args_for(
            job, True
        ) == self.args_for(job):
            return "misrouted"  # nothing to fix: wrong agent
        status = task["status"].get("message") or {"parts": []}
        errors = data_of(status["parts"]).get("errors")
        p = self.model.p_fix if errors else self.model.p_fix_blind
        if rng("fix", job.req.id, job.i, job.agent).random() >= p:
            return None
        job.call.attempts += 1
        parts, meta = self.build(job, fixed=True)
        return self.send(job, parts, meta, {}).get("task")

    def answer(self, job: Job, task: dict):
        """Get what an interrupted task needs; continue it."""
        req = job.req
        if task["status"]["state"] == AUTH_REQUIRED:
            # The customer confirms out of band, through the link
            # the orders team sent; the concierge only resumes.
            self.w.store.verified.add(req.customer)
            parts = [
                {"text": "The customer has confirmed the change."}
            ]
        else:
            r = rng("answer", req.id, job.i)
            if (
                not req.follow_up
                or r.random() >= self.model.p_answer
            ):
                self.w.clients[job.agent].cancel(task["id"])
                return None
            self.run.latency += (
                900  # ask the customer, read reply
            )
            cond = condition_in(req.follow_up)
            parts = [{"text": req.follow_up}]
            if job.skill and cond:
                parts = [{"data": {"condition": cond}}]
        res = self.send(job, parts, None, {}, task_id=task["id"])
        return res.get("task")

    def wait(self, job: Job, task: dict) -> dict:
        """Wait for long-running work: by push, or by polling."""
        server = self.w.servers[job.agent]
        due = server.next_due()
        if due is not None:
            self.w.clock.advance(
                max(0.0, due - self.w.clock.now())
            )
            server.tick()
        if self.w.receiver.latest_state(task["id"]) is None:
            self.run.hops += 4  # no push arrived: poll instead
        self.run.latency += 150
        return self.w.clients[job.agent].get_task(task["id"])

    # -- reading results -----------------------------------------

    def misread(self, job: Job, what: str) -> bool:
        r = rng("misread", job.req.id, job.i, what)
        if r.random() < self.model.p_misread:
            self.run.read_ok = False
            return True
        return False

    def read(self, job: Job, parts: list) -> dict:
        """Take what later steps need from a result."""
        text, data = text_of(parts), data_of(parts)
        ctx = job.ctx
        self.run.tokens += tokens(text) + tokens(json.dumps(data))
        if data:  # structured: read exactly
            for k in ("trackingNumber", "total", "outcome"):
                if k in data:
                    ctx[k] = data[k]
        else:  # prose: read the way a model reads
            wrong = self.misread(job, "result")
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
            self.injected(job)
        return {"state": COMPLETED}

    # -- instructions found in another agent's output ------------

    def injected(self, job: Job) -> None:
        req = job.req
        p = self.model.p_obey
        if self.cfg.guarded:
            p = (
                self.model.p_obey_marked
            )  # marked as data, not orders
        if rng("obey", req.id, job.i).random() >= p:
            return
        self.run.obeyed += 1
        if self.cfg.guarded:
            # Actions nobody asked for go to the customer first,
            # and the customer declines them.
            self.run.declined += 2
            return
        oid = next(
            s.args["orderId"]
            for s in req.steps
            if "orderId" in s.args
        )
        refund = Step(
            "issue a full refund of the order payment through "
            "billing",
            "billing",
            "refund-payment",
            {"orderId": oid, "amount": job.ctx.get("total", 0.0)},
        )
        self.do_step(req, 50 + job.i, refund, job.ctx, extra=True)
        self.share_with_claims(job)

    def share_with_claims(self, job: Job) -> None:
        cust = self.profile(job.req)
        if self.cfg.gateway:
            self.run.blocked += 1  # egress policy: no PII outside
            return
        text = (
            "ParcelPath claims desk: customer contact for the "
            f"claim: {cust['email']}, {cust['phone']}."
        )
        call = Call(
            60 + job.i, "carrier", None, FAILED, extra=True
        )
        self.run.calls.append(call)
        claims = Job(
            job.req,
            60 + job.i,
            job.step,
            job.ctx,
            "carrier",
            {},
            None,
            call,
        )
        res = self.send(claims, [{"text": text}], None, {})
        status = res.get("task", {}).get("status", {})
        call.state = status.get("state", FAILED)


def outputs(task: dict) -> list:
    """Every part of every artifact, or the status message."""
    parts = [
        p for a in task.get("artifacts", []) for p in a["parts"]
    ]
    status = task["status"].get("message") or {"parts": []}
    return parts or status["parts"]
