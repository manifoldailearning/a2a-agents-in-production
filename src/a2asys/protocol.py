"""The A2A v1.0 data model and JSON-RPC envelope, as plain dicts.

Objects use the protocol's JSON names: camelCase fields, and
enum values as their full names, such as "TASK_STATE_COMPLETED"
and "ROLE_USER". Nothing here talks to a network; the server,
client and transports build on these helpers.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

VERSION = "1.0"
CARD_PATH = "/.well-known/agent-card.json"

SUBMITTED = "TASK_STATE_SUBMITTED"
WORKING = "TASK_STATE_WORKING"
COMPLETED = "TASK_STATE_COMPLETED"
FAILED = "TASK_STATE_FAILED"
CANCELED = "TASK_STATE_CANCELED"
REJECTED = "TASK_STATE_REJECTED"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"

TERMINAL = {COMPLETED, FAILED, CANCELED, REJECTED}
INTERRUPTED = {INPUT_REQUIRED, AUTH_REQUIRED}

USER = "ROLE_USER"
AGENT = "ROLE_AGENT"

# JSON-RPC 2.0 codes, then the A2A codes from the specification.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
PUSH_NOT_SUPPORTED = -32003
UNSUPPORTED_OPERATION = -32004
CONTENT_TYPE_NOT_SUPPORTED = -32005
INVALID_AGENT_RESPONSE = -32006
EXTENDED_CARD_NOT_CONFIGURED = -32007
EXTENSION_SUPPORT_REQUIRED = -32008
VERSION_NOT_SUPPORTED = -32009

ERROR_NAMES = {
    TASK_NOT_FOUND: "TaskNotFoundError",
    TASK_NOT_CANCELABLE: "TaskNotCancelableError",
    PUSH_NOT_SUPPORTED: "PushNotificationNotSupportedError",
    UNSUPPORTED_OPERATION: "UnsupportedOperationError",
    CONTENT_TYPE_NOT_SUPPORTED: "ContentTypeNotSupportedError",
    INVALID_AGENT_RESPONSE: "InvalidAgentResponseError",
    EXTENDED_CARD_NOT_CONFIGURED: (
        "ExtendedAgentCardNotConfiguredError"
    ),
    EXTENSION_SUPPORT_REQUIRED: "ExtensionSupportRequiredError",
    VERSION_NOT_SUPPORTED: "VersionNotSupportedError",
}

METHODS = (
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "ListTasks",
    "CancelTask",
    "SubscribeToTask",
    "CreateTaskPushNotificationConfig",
    "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
    "GetExtendedAgentCard",
)


class A2AError(Exception):
    """An error that travels back to the caller as JSON-RPC."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_json(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class Ids:
    """Readable, reproducible identifiers: task-0001, ctx-0001."""

    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self.counters: dict[str, itertools.count] = {}

    def next(self, kind: str) -> str:
        n = next(
            self.counters.setdefault(kind, itertools.count(1))
        )
        return f"{self.prefix}{kind}-{n:04d}"


def now_iso(seconds: float) -> str:
    """Simulated time as an ISO 8601 timestamp."""
    return datetime.fromtimestamp(
        1_794_816_000 + seconds, tz=UTC
    ).isoformat(timespec="seconds")


def text_part(text: str) -> dict:
    return {"text": text}


def data_part(data: dict, media_type="application/json") -> dict:
    return {"data": data, "mediaType": media_type}


def message(
    role: str,
    parts: list[dict],
    message_id: str,
    context_id: str | None = None,
    task_id: str | None = None,
    metadata: dict | None = None,
    reference_task_ids: list[str] | None = None,
) -> dict:
    msg = {"messageId": message_id, "role": role, "parts": parts}
    if context_id:
        msg["contextId"] = context_id
    if task_id:
        msg["taskId"] = task_id
    if reference_task_ids:
        msg["referenceTaskIds"] = reference_task_ids
    if metadata:
        msg["metadata"] = metadata
    return msg


def text_of(parts: list[dict]) -> str:
    return "\n".join(p["text"] for p in parts if "text" in p)


def data_of(parts: list[dict]) -> dict:
    out: dict = {}
    for p in parts:
        if isinstance(p.get("data"), dict):
            out.update(p["data"])
    return out


def request(method: str, params: dict, rid) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": method,
        "params": params,
    }


def result(rid, value: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": value}


def error(rid, err: A2AError) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": err.to_json()}
