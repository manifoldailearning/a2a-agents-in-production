"""Signed Agent Cards: JWS (RS256) over a canonical card.

The specification signs the card with JWS (RFC 7515) after
canonicalizing it with JCS (RFC 8785), leaving out the signatures
field itself. This module does both with the standard library.
RSA here is textbook-correct PKCS#1 v1.5 but not hardened against
side channels: use a vetted library for keys you care about. The
demo keys in data/keys.json are public and for examples only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import random
from importlib import resources

# DER prefix of a SHA-256 DigestInfo, from RFC 8017.
SHA256_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(card: dict) -> bytes:
    """The card's canonical bytes, without its signatures.

    For cards made of strings, integers, booleans, lists and
    objects with ASCII keys, sorted keys and no whitespace give
    the same bytes as JCS. Cards with floats or non-ASCII keys
    need a full RFC 8785 implementation.
    """
    body = {k: v for k, v in card.items() if k != "signatures"}
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _probable_prime(bits: int, r: random.Random) -> int:
    while True:
        n = r.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(
            n % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31)
        ):
            d, s = n - 1, 0
            while d % 2 == 0:
                d, s = d // 2, s + 1
            for _ in range(24):
                x = pow(r.randrange(2, n - 1), d, n)
                if x in (1, n - 1):
                    continue
                for _ in range(s - 1):
                    x = pow(x, 2, n)
                    if x == n - 1:
                        break
                else:
                    break
            else:
                return n


def generate_key(bits: int = 2048, seed: int = 0) -> dict:
    """An RSA key pair, reproducible from its seed (demo only)."""
    r = random.Random(seed)
    e = 65537
    while True:
        p = _probable_prime(bits // 2, r)
        q = _probable_prime(bits // 2, r)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    return {"n": p * q, "e": e, "d": pow(e, -1, phi)}


def _digest_info(data: bytes, k: int) -> int:
    t = SHA256_PREFIX + hashlib.sha256(data).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return int.from_bytes(em, "big")


def rs256_sign(data: bytes, key: dict) -> bytes:
    k = (key["n"].bit_length() + 7) // 8
    return pow(
        _digest_info(data, k), key["d"], key["n"]
    ).to_bytes(k, "big")


def rs256_verify(data: bytes, sig: bytes, key: dict) -> bool:
    k = (key["n"].bit_length() + 7) // 8
    if len(sig) != k:
        return False
    return pow(
        int.from_bytes(sig, "big"), key["e"], key["n"]
    ) == (_digest_info(data, k))


def sign_card(
    card: dict, key: dict, kid: str, jku: str = ""
) -> dict:
    """Return a copy of the card with one more signature."""
    header = {"alg": "RS256", "kid": kid, "typ": "JOSE"}
    if jku:
        header["jku"] = jku
    protected = b64url(
        json.dumps(header, separators=(",", ":")).encode()
    )
    payload = b64url(canonical(card))
    sig = rs256_sign(f"{protected}.{payload}".encode(), key)
    out = copy.deepcopy(card)
    out.setdefault("signatures", []).append(
        {"protected": protected, "signature": b64url(sig)}
    )
    return out


def verify_card(card: dict, keys: dict[str, dict]) -> str | None:
    """Return the kid of a valid signature by a trusted key."""
    payload = b64url(canonical(card))
    for s in card.get("signatures", []):
        try:
            header = json.loads(unb64url(s["protected"]))
        except (ValueError, KeyError):
            continue
        key = keys.get(header.get("kid", ""))
        if header.get("alg") != "RS256" or key is None:
            continue
        data = f"{s['protected']}.{payload}".encode()
        if rs256_verify(data, unb64url(s["signature"]), key):
            return header["kid"]
    return None


def demo_keys() -> dict[str, dict]:
    """The public demo signing keys of each organization."""
    raw = json.loads(
        resources.files("a2asys")
        .joinpath("data/keys.json")
        .read_text()
    )
    return {
        kid: {k: int(v, 16) for k, v in key.items()}
        for kid, key in raw.items()
    }


def public(key: dict) -> dict:
    return {"n": key["n"], "e": key["e"]}
