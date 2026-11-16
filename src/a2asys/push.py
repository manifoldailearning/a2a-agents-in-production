"""Push notifications: the agent that sends them, the client that
receives them, and the checks each side owes the other.

The sending agent refuses webhook URLs that point inside its own
network, so a caller cannot use it to reach private services. The
receiving client accepts a notification only with the token it
registered for that task, and handles the same event twice
without acting twice, because delivery is at least once.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
from collections.abc import Callable
from urllib.parse import urlparse

TOKEN_HEADER = "X-A2A-Notification-Token"
PRIVATE_NAMES = {"localhost", "metadata.google.internal"}


def check_webhook(
    url: str, allow: tuple[str, ...] = ()
) -> str | None:
    """Return why a webhook URL is unsafe to call, or None."""
    parts = urlparse(url)
    if parts.scheme != "https":
        return "not https"
    host = (parts.hostname or "").lower()
    if (
        not host
        or host in PRIVATE_NAMES
        or host.endswith(".internal")
    ):
        return "internal host"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (
        ip.is_private or ip.is_loopback or ip.is_link_local
    ):
        return "not a public address"
    if allow and not any(
        host == a or host.endswith("." + a) for a in allow
    ):
        return "not on the allowlist"
    return None


class PushSender:
    """What an agent uses to deliver task updates to webhooks."""

    def __init__(
        self,
        network: Callable[[str, dict, str], int],
        allow: tuple[str, ...] = (),
        duplicate_every: int = 0,
    ):
        self.network = network
        self.allow = allow
        self.duplicate_every = duplicate_every
        self.sent = 0
        self.refused: list[tuple[str, str]] = []

    def __call__(self, config: dict, event: dict) -> None:
        problem = check_webhook(config["url"], self.allow)
        if problem:
            self.refused.append((config["url"], problem))
            return
        headers = {"Content-Type": "application/json"}
        if config.get("token"):
            headers[TOKEN_HEADER] = config["token"]
        auth = config.get("authentication")
        if auth:
            headers["Authorization"] = (
                f"{auth['scheme']} {auth['credentials']}"
            )
        body = json.dumps(event)
        self.sent += 1
        times = 1
        if (
            self.duplicate_every
            and self.sent % self.duplicate_every == 0
        ):
            times = 2  # at-least-once delivery: sometimes twice
        for _ in range(times):
            self.network(config["url"], headers, body)


class PushReceiver:
    """The client's webhook endpoint."""

    def __init__(self):
        self.tokens: dict[
            str, str | None
        ] = {}  # token -> task id
        self.seen: set[str] = set()
        self.updates: dict[str, list[dict]] = {}
        self.rejected = 0
        self.duplicates = 0

    def expect_token(self, token: str) -> None:
        """Register a token before the task exists."""
        self.tokens[token] = None

    def bind(self, token: str, task_id: str) -> None:
        """Tie a token to the task it was registered for."""
        self.tokens[token] = task_id

    def receive(self, headers: dict, body: str) -> int:
        """Handle one HTTP POST; return the status code."""
        event = json.loads(body)
        update = (
            event.get("statusUpdate")
            or event.get("artifactUpdate")
            or event.get("task")
            or {}
        )
        task_id = update.get("taskId") or update.get("id", "")
        token = headers.get(TOKEN_HEADER, "")
        known = any(
            hmac.compare_digest(token, t) for t in self.tokens
        )
        bound = self.tokens.get(token)
        if not known or (bound is not None and bound != task_id):
            self.rejected += 1
            return 401
        key = json.dumps(event, sort_keys=True)
        if key in self.seen:
            self.duplicates += 1
            return 200  # acknowledged, not processed again
        self.seen.add(key)
        self.updates.setdefault(task_id, []).append(event)
        return 200

    def latest_state(self, task_id: str) -> str | None:
        for event in reversed(self.updates.get(task_id, [])):
            if "statusUpdate" in event:
                return event["statusUpdate"]["status"]["state"]
        return None
