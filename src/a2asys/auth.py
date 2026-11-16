"""Identity between agents: tokens, audiences, scopes, delegation.

Each organization runs an authorization server that issues short
signed tokens (JWT, HS256 here for brevity) to its own agents.
A token names who is calling (sub), which agent it is for (aud),
what it may do there (scope) and, when one agent acts for a user
or another agent, who that is (act). The receiving agent checks
every one of those on every request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from a2asys.server import AuthError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Issuer:
    """An organization's authorization server."""

    def __init__(self, name: str, secret: bytes, clock):
        self.name = name
        self.secret = secret
        self.clock = clock
        self.clients: dict[str, set[str]] = {}  # client -> scopes

    def register(self, client: str, scopes: set[str]) -> None:
        self.clients[client] = set(scopes)

    def token(
        self,
        client: str,
        audience: str,
        scopes: set[str],
        ttl: int = 300,
        act: dict | None = None,
    ) -> str:
        """Client credentials grant, for one audience."""
        allowed = self.clients.get(client)
        if allowed is None:
            raise PermissionError(f"unknown client {client}")
        if not scopes <= allowed:
            raise PermissionError(
                f"{client} may not request "
                f"{sorted(scopes - allowed)}"
            )
        now = int(self.clock.now())
        claims = {
            "iss": self.name,
            "sub": client,
            "aud": audience,
            "scope": " ".join(sorted(scopes)),
            "iat": now,
            "exp": now + ttl,
        }
        if act:
            claims["act"] = act
        return self.sign(claims)

    def exchange(
        self,
        subject_token: str,
        actor: str,
        audience: str,
        scopes: set,
    ) -> str:
        """Token exchange (RFC 8693): act for the token's subject.

        The new token keeps the original subject, names the actor,
        and can only narrow the original token's scopes.
        """
        original = self.read(subject_token)
        have = set(original["scope"].split())
        if not scopes <= have:
            raise PermissionError(
                f"cannot widen scopes: {sorted(scopes - have)}"
            )
        claims = {
            **original,
            "aud": audience,
            "scope": " ".join(sorted(scopes)),
            "act": {"sub": actor},
        }
        return self.sign(claims)

    def sign(self, claims: dict) -> str:
        head = _b64(
            json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        )
        body = _b64(json.dumps(claims, sort_keys=True).encode())
        mac = hmac.new(
            self.secret, f"{head}.{body}".encode(), hashlib.sha256
        )
        return f"{head}.{body}.{_b64(mac.digest())}"

    def read(self, token: str) -> dict:
        try:
            head, body, sig = token.split(".")
        except ValueError as exc:
            raise AuthError(
                'Bearer error="invalid_token"'
            ) from exc
        mac = hmac.new(
            self.secret, f"{head}.{body}".encode(), hashlib.sha256
        )
        if not hmac.compare_digest(_b64(mac.digest()), sig):
            raise AuthError('Bearer error="invalid_token"')
        return json.loads(_unb64(body))


class Verifier:
    """What an agent server runs before every request."""

    def __init__(
        self,
        issuer: Issuer,
        audience: str,
        required: set[str] = frozenset(),
        clients: set[str] | None = None,
    ):
        self.issuer = issuer
        self.audience = audience
        self.required = set(required)
        self.clients = clients
        self.checked: list[dict] = []

    def __call__(self, headers: dict) -> str:
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise AuthError('Bearer realm="a2a"')
        claims = self.issuer.read(auth[7:])
        if claims.get("iss") != self.issuer.name:
            raise AuthError('Bearer error="invalid_token"')
        if claims["exp"] <= self.issuer.clock.now():
            raise AuthError(
                'Bearer error="invalid_token", expired'
            )
        if claims.get("aud") != self.audience:
            raise AuthError(
                'Bearer error="invalid_token", wrong audience'
            )
        if (
            self.clients is not None
            and claims["sub"] not in self.clients
        ):
            raise AuthError('Bearer error="invalid_client"')
        have = set(claims.get("scope", "").split())
        if not self.required <= have:
            missing = " ".join(sorted(self.required - have))
            raise AuthError(
                'Bearer error="insufficient_scope", '
                f'scope="{missing}"'
            )
        self.checked.append(claims)
        return claims["sub"]
