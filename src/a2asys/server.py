"""An A2A server: the task store and every operation of v1.0.

The server owns tasks. An executor, supplied by each agent,
decides what happens to a task when it starts, when a new
message arrives for it, when its long-running work is due and
when it is canceled. The server enforces the rules around that:
who may see a task, which states accept messages, which
operations a terminal task refuses, and how updates reach
subscribers and push notification endpoints.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from a2asys.protocol import (
    AGENT,
    CANCELED,
    CONTENT_TYPE_NOT_SUPPORTED,
    EXTENDED_CARD_NOT_CONFIGURED,
    EXTENSION_SUPPORT_REQUIRED,
    INTERNAL_ERROR,
    INTERRUPTED,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PUSH_NOT_SUPPORTED,
    SUBMITTED,
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    TERMINAL,
    UNSUPPORTED_OPERATION,
    USER,
    VERSION,
    VERSION_NOT_SUPPORTED,
    WORKING,
    A2AError,
    Ids,
    error,
    message,
    now_iso,
    result,
    text_part,
)


class Clock:
    """Simulated time in seconds, shared by agents and clients."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class AuthError(Exception):
    """Authentication failed; transports map this to HTTP 401."""

    def __init__(self, challenge: str):
        super().__init__(challenge)
        self.challenge = challenge


class Pending(Exception):
    """A blocking call is waiting for work that is not done yet.

    The transport decides what that means: wait, or give up when
    its own timeout is shorter than the work.
    """

    def __init__(self, task_id: str, wait: float):
        super().__init__(task_id)
        self.task_id = task_id
        self.wait = wait


@dataclass
class Outcome:
    """What an executor decided for a task."""

    state: str
    text: str = ""
    artifacts: list[dict] = field(default_factory=list)
    ready_at: float | None = None  # when WORKING: next tick
    data: dict | None = None  # structured detail for the status
    reply_only: bool = False  # answer with a Message, no Task


@dataclass
class Turn:
    """Everything an executor sees when it is asked to act."""

    phase: str  # start, resume, tick or cancel
    task: dict
    message: dict | None
    caller: str
    now: float
    memo: dict
    skill: str | None


Executor = Callable[[Turn], Outcome]


def artifact(artifact_id: str, name: str, parts: list) -> dict:
    return {
        "artifactId": artifact_id,
        "name": name,
        "parts": parts,
    }


@dataclass
class Record:
    task: dict
    owner: str
    memo: dict = field(default_factory=dict)
    ready_at: float | None = None
    events: list[dict] = field(default_factory=list)
    push: dict[str, dict] = field(default_factory=dict)


class AgentServer:
    def __init__(
        self,
        card: dict,
        executor: Executor,
        clock: Clock | None = None,
        authenticate: Callable[[dict], str] | None = None,
        push_sender: Callable[[dict, dict], None] | None = None,
        dedup: bool = True,
        extended_card: dict | None = None,
        prefix: str = "",
    ):
        self.card = card
        self.executor = executor
        self.clock = clock or Clock()
        self.authenticate = authenticate
        self.push_sender = push_sender
        self.dedup = dedup
        self.extended_card = extended_card
        self.ids = Ids(prefix)
        self.tasks: dict[str, Record] = {}
        self.seen: dict[tuple[str, str], dict] = {}
        self.log: list[dict] = []  # every request, for tests
        self.active: list[str] = []

    # -- JSON-RPC entry point ------------------------------------

    def handle(
        self, rpc: dict, headers: dict
    ) -> tuple[dict, dict]:
        """Answer one JSON-RPC request: (reply, headers)."""
        rid = rpc.get("id")
        headers = {k.lower(): v for k, v in headers.items()}
        try:
            caller = self.caller(headers)
            self.check_version(headers)
            active = self.activate(headers)
            method = rpc.get("method", "")
            params = rpc.get("params") or {}
            self.log.append({"method": method, "caller": caller})
            handler = getattr(self, "op_" + method, None)
            if not handler:
                raise A2AError(
                    METHOD_NOT_FOUND, f"Unknown method: {method}"
                )
            value = handler(params, caller)
            out = {"a2a-extensions": ",".join(active)}
            return result(rid, value), out
        except A2AError as err:
            return error(rid, err), {}

    def caller(self, headers: dict) -> str:
        if self.authenticate is None:
            return "anonymous"
        return self.authenticate(headers)  # may raise AuthError

    def check_version(self, headers: dict) -> None:
        asked = headers.get("a2a-version", "") or "0.3"
        if asked.split(".")[:2] != VERSION.split("."):
            raise A2AError(
                VERSION_NOT_SUPPORTED,
                f"A2A version {asked} is not supported",
                {"supported": [VERSION]},
            )

    def activate(self, headers: dict) -> list[str]:
        asked = {
            u.strip()
            for u in headers.get("a2a-extensions", "").split(",")
            if u.strip()
        }
        declared = self.card["capabilities"].get("extensions", [])
        active = [e["uri"] for e in declared if e["uri"] in asked]
        for ext in declared:
            if ext.get("required") and ext["uri"] not in asked:
                raise A2AError(
                    EXTENSION_SUPPORT_REQUIRED,
                    f"Extension required: {ext['uri']}",
                )
        self.active = active
        return active

    # -- messages and tasks --------------------------------------

    def op_SendMessage(self, params: dict, caller: str) -> dict:
        msg = self.valid_message(params)
        config = params.get("configuration") or {}
        key = (caller, msg["messageId"])
        if self.dedup and key in self.seen:
            # A retry of a message already accepted: answer with
            # what that message produced, and do nothing again.
            first = self.seen[key]
            if "taskId" in first:
                rec = self.tasks[first["taskId"]]
                return {"task": self.view(rec, config)}
            return copy.deepcopy(first)
        rec, reply = self.accept(msg, caller, config)
        if reply is not None:
            if self.dedup:
                self.seen[key] = {"message": reply}
            return {"message": reply}
        if self.dedup:
            self.seen[key] = {"taskId": rec.task["id"]}
        blocking = not config.get("returnImmediately")
        if blocking and rec.task["status"]["state"] == WORKING:
            raise Pending(
                rec.task["id"], rec.ready_at - self.clock.now()
            )
        return {"task": self.view(rec, config)}

    def op_SendStreamingMessage(
        self, params: dict, caller
    ) -> dict:
        msg = self.valid_message(params)
        if not self.card["capabilities"].get("streaming"):
            raise A2AError(
                UNSUPPORTED_OPERATION,
                "Streaming is not supported",
            )
        config = params.get("configuration") or {}
        rec, reply = self.accept(msg, caller, config)
        if reply is not None:
            return {"events": [{"message": reply}]}
        return {
            "events": list(rec.events),
            "taskId": rec.task["id"],
            "next": len(rec.events),
        }

    def valid_message(self, params: dict) -> dict:
        msg = params.get("message")
        if not isinstance(msg, dict) or not msg.get("parts"):
            raise A2AError(
                INVALID_PARAMS, "message.parts is required"
            )
        if not msg.get("messageId"):
            raise A2AError(
                INVALID_PARAMS, "message.messageId is required"
            )
        if msg.get("role") != USER:
            raise A2AError(
                INVALID_PARAMS, "message.role must be ROLE_USER"
            )
        accepted = self.card["defaultInputModes"]
        for part in msg["parts"]:
            mode = part.get("mediaType") or (
                "text/plain"
                if "text" in part
                else "application/json"
            )
            if mode not in accepted:
                raise A2AError(
                    CONTENT_TYPE_NOT_SUPPORTED,
                    f"Input mode not accepted: {mode}",
                )
        return msg

    def accept(self, msg: dict, caller: str, config: dict):
        """Start a task, or continue an interrupted one."""
        skill = (msg.get("metadata") or {}).get("skillId")
        if msg.get("taskId"):
            rec = self.owned(msg["taskId"], caller)
            state = rec.task["status"]["state"]
            if state in TERMINAL:
                raise A2AError(
                    UNSUPPORTED_OPERATION,
                    f"Task {msg['taskId']} is {state} and "
                    f"accepts "
                    "no more messages",
                )
            rec.task["history"].append(msg)
            self.run(rec, "resume", msg, caller, skill)
            return rec, None
        context_id = msg.get("contextId") or self.ids.next("ctx")
        task = {
            "id": self.ids.next("task"),
            "contextId": context_id,
            "status": self.status(SUBMITTED),
            "history": [msg],
            "artifacts": [],
        }
        rec = Record(task=task, owner=caller)
        push = config.get("taskPushNotificationConfig")
        if push:
            self.add_push(rec, push)
        rec.events.append({"task": copy.deepcopy(task)})
        outcome = self.call(rec, "start", msg, caller, skill)
        if outcome.reply_only:
            return rec, message(
                AGENT,
                [text_part(outcome.text)],
                self.ids.next("msg"),
                context_id=context_id,
            )
        self.tasks[task["id"]] = rec
        self.apply(rec, outcome)
        return rec, None

    def call(self, rec, phase, msg, caller, skill) -> Outcome:
        turn = Turn(
            phase=phase,
            task=copy.deepcopy(rec.task),
            message=msg,
            caller=caller,
            now=self.clock.now(),
            memo=rec.memo,
            skill=skill,
        )
        try:
            return self.executor(turn)
        except A2AError:
            raise
        except Exception as exc:  # the agent itself broke
            raise A2AError(
                INTERNAL_ERROR, f"Agent error: {exc}"
            ) from exc

    def run(self, rec, phase, msg, caller, skill=None) -> None:
        self.apply(rec, self.call(rec, phase, msg, caller, skill))

    def apply(self, rec: Record, outcome: Outcome) -> None:
        task = rec.task
        for art in outcome.artifacts:
            task["artifacts"].append(art)
            self.emit(
                rec,
                {
                    "artifactUpdate": {
                        "taskId": task["id"],
                        "contextId": task["contextId"],
                        "artifact": art,
                        "lastChunk": True,
                    }
                },
            )
        rec.ready_at = outcome.ready_at
        status = self.status(
            outcome.state, outcome.text, outcome.data
        )
        task["status"] = status
        if status.get("message"):
            task["history"].append(status["message"])
        self.emit(
            rec,
            {
                "statusUpdate": {
                    "taskId": task["id"],
                    "contextId": task["contextId"],
                    "status": status,
                }
            },
        )

    def status(self, state, text="", data=None) -> dict:
        status = {
            "state": state,
            "timestamp": now_iso(self.clock.now()),
        }
        parts = []
        if text:
            parts.append(text_part(text))
        if data:
            parts.append(
                {"data": data, "mediaType": "application/json"}
            )
        if parts:
            status["message"] = message(
                AGENT, parts, self.ids.next("msg")
            )
        return status

    def emit(self, rec: Record, event: dict) -> None:
        rec.events.append(copy.deepcopy(event))
        if self.push_sender:
            for cfg in rec.push.values():
                self.push_sender(cfg, copy.deepcopy(event))

    def tick(self) -> int:
        """Resume every working task whose time has come."""
        due = [
            r
            for r in self.tasks.values()
            if r.task["status"]["state"] == WORKING
            and r.ready_at is not None
            and r.ready_at <= self.clock.now()
        ]
        for rec in due:
            self.run(rec, "tick", None, rec.owner)
        return len(due)

    def next_due(self) -> float | None:
        times = [
            r.ready_at
            for r in self.tasks.values()
            if r.task["status"]["state"] == WORKING
            and r.ready_at is not None
        ]
        return min(times) if times else None

    # -- reading, listing, canceling, subscribing ----------------

    def owned(self, task_id: str, caller: str) -> Record:
        rec = self.tasks.get(task_id)
        # Another caller's task is reported as not found, so the
        # error itself does not reveal that the task exists.
        if rec is None or rec.owner != caller:
            raise A2AError(
                TASK_NOT_FOUND, f"Task not found: {task_id}"
            )
        return rec

    def view(
        self, rec: Record, config: dict | None = None
    ) -> dict:
        task = copy.deepcopy(rec.task)
        limit = (config or {}).get("historyLength")
        if limit is not None:
            task["history"] = (
                task["history"][-limit:] if limit else []
            )
        return task

    def op_GetTask(self, params: dict, caller: str) -> dict:
        rec = self.owned(params.get("id", ""), caller)
        return self.view(rec, params)

    def op_ListTasks(self, params: dict, caller: str) -> dict:
        mine = [
            r for r in self.tasks.values() if r.owner == caller
        ]
        if params.get("contextId"):
            mine = [
                r
                for r in mine
                if r.task["contextId"] == params["contextId"]
            ]
        if params.get("status"):
            mine = [
                r
                for r in mine
                if r.task["status"]["state"] == params["status"]
            ]
        mine.sort(
            key=lambda r: (
                r.task["status"]["timestamp"],
                r.task["id"],
            ),
            reverse=True,
        )
        start = int(params.get("pageToken") or 0)
        size = params.get("pageSize") or 50
        page = mine[start : start + size]
        tasks = []
        for r in page:
            t = self.view(
                r,
                {"historyLength": params.get("historyLength", 0)},
            )
            if not params.get("includeArtifacts"):
                t.pop("artifacts", None)
            tasks.append(t)
        out = {"tasks": tasks, "totalSize": len(mine)}
        if start + size < len(mine):
            out["nextPageToken"] = str(start + size)
        return out

    def op_CancelTask(self, params: dict, caller: str) -> dict:
        rec = self.owned(params.get("id", ""), caller)
        if rec.task["status"]["state"] in TERMINAL:
            raise A2AError(
                TASK_NOT_CANCELABLE,
                f"Task {rec.task['id']} is already "
                f"{rec.task['status']['state']}",
            )
        outcome = self.call(rec, "cancel", None, caller, None)
        outcome.state = CANCELED
        self.apply(rec, outcome)
        return self.view(rec)

    def op_SubscribeToTask(
        self, params: dict, caller: str
    ) -> dict:
        rec = self.owned(params.get("id", ""), caller)
        if rec.task["status"]["state"] in TERMINAL:
            raise A2AError(
                UNSUPPORTED_OPERATION,
                "Cannot subscribe to a task in a terminal state",
            )
        return {
            "events": [{"task": self.view(rec)}],
            "taskId": rec.task["id"],
            "next": len(rec.events),
        }

    def events_since(
        self, task_id: str, start: int
    ) -> list[dict]:
        """Events a stream has not delivered yet."""
        return list(self.tasks[task_id].events[start:])

    # -- push notifications --------------------------------------

    def add_push(self, rec: Record, cfg: dict) -> dict:
        if not self.card["capabilities"].get("pushNotifications"):
            raise A2AError(
                PUSH_NOT_SUPPORTED,
                "Push notifications are not supported",
            )
        if not str(cfg.get("url", "")).startswith("https://"):
            raise A2AError(
                INVALID_PARAMS, "Push URL must use https"
            )
        cfg = dict(cfg)
        cfg["taskId"] = rec.task["id"]
        cfg.setdefault("id", self.ids.next("push"))
        rec.push[cfg["id"]] = cfg
        return cfg

    def op_CreateTaskPushNotificationConfig(self, params, caller):
        rec = self.owned(params.get("taskId", ""), caller)
        return self.add_push(rec, params)

    def op_GetTaskPushNotificationConfig(self, params, caller):
        rec = self.owned(params.get("taskId", ""), caller)
        cfg = rec.push.get(params.get("id", ""))
        if cfg is None:
            raise A2AError(
                TASK_NOT_FOUND, "Push configuration not found"
            )
        return cfg

    def op_ListTaskPushNotificationConfigs(self, params, caller):
        rec = self.owned(params.get("taskId", ""), caller)
        return {"configs": list(rec.push.values())}

    def op_DeleteTaskPushNotificationConfig(self, params, caller):
        rec = self.owned(params.get("taskId", ""), caller)
        rec.push.pop(params.get("id", ""), None)  # idempotent
        return {}

    def op_GetExtendedAgentCard(self, params, caller):
        if caller == "anonymous":
            raise AuthError('Bearer realm="a2a"')
        if not self.extended_card:
            raise A2AError(
                EXTENDED_CARD_NOT_CONFIGURED,
                "No extended agent card is configured",
            )
        return self.extended_card


def interrupted(task: dict) -> bool:
    return task["status"]["state"] in INTERRUPTED


def state_of(task: dict) -> str:
    return task["status"]["state"]
