"""Where agents find each other, and what makes a card believable.

An open registry lists every card anyone published. A careful
client checks each card before trusting it: the required fields
are there, the card is signed by a key the client trusts, and the
agent is one this caller is allowed to use.
"""

from __future__ import annotations

from urllib.parse import urlparse

from a2asys import jws
from a2asys.cards import TEAM

REQUIRED = (
    "name",
    "description",
    "supportedInterfaces",
    "version",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
)
SKILL_REQUIRED = ("id", "name", "description", "tags")

# Which organization signs which agent's card.
SIGNER = {key: "hp-2026" for key in TEAM}
SIGNER.update(
    carrier="pp-2026",
    messaging="hp-2026",
    procurement="hp-2026",
    inventory="hp-2026",
    people="hp-2026",
)
# A trusted key may vouch only for agents on its owner's domains.
TRUSTED = {
    "hp-2026": ("harborpine.example",),
    "pp-2026": ("parcelpath.example",),
}


def validate_card(card: dict) -> list[str]:
    problems = [f"missing {f}" for f in REQUIRED if f not in card]
    for i, s in enumerate(card.get("skills", [])):
        problems += [
            f"skill {i} missing {f}"
            for f in SKILL_REQUIRED
            if f not in s
        ]
    for i, iface in enumerate(
        card.get("supportedInterfaces", [])
    ):
        if not str(iface.get("url", "")).startswith("https://"):
            problems.append(f"interface {i} is not https")
        if iface.get("protocolBinding") not in (
            "JSONRPC",
            "GRPC",
            "HTTP+JSON",
        ):
            problems.append(
                f"interface {i} has an unknown binding"
            )
    return problems


def sign_all(cards: dict[str, dict]) -> dict[str, dict]:
    """Sign every card the way its organization would.

    The impostor cannot sign with Harbor & Pine's key, so it signs
    with its own and claims Harbor & Pine's key id.
    """
    keys = jws.demo_keys()
    out = {}
    for key, card in cards.items():
        if key == "impostor":
            out[key] = jws.sign_card(
                card, keys["xx-2026"], "hp-2026"
            )
        else:
            kid = SIGNER[key.split(".")[0]]
            out[key] = jws.sign_card(card, keys[kid], kid)
    return out


class Registry:
    def __init__(
        self,
        cards: dict[str, dict],
        verify: bool = False,
        allow: tuple[str, ...] | None = None,
    ):
        self.cards = cards
        self.verify = verify
        self.allow = allow
        self.trusted = {
            kid: jws.public(k)
            for kid, k in jws.demo_keys().items()
            if kid in TRUSTED
        }
        self.refused: dict[str, str] = {}

    def visible(self) -> dict[str, dict]:
        out = {}
        for key, card in self.cards.items():
            if validate_card(card):
                self.refused[key] = "invalid card"
                continue
            if self.allow is not None and key not in self.allow:
                continue
            if self.verify:
                kid = jws.verify_card(card, self.trusted)
                if kid is None:
                    self.refused[key] = (
                        "no valid trusted signature"
                    )
                    continue
                if not on_domains(card, TRUSTED[kid]):
                    self.refused[key] = (
                        f"{kid} cannot vouch for this host"
                    )
                    continue
            out[key] = card
        return out


def on_domains(card: dict, domains: tuple[str, ...]) -> bool:
    """True when every interface URL is on one of the domains."""
    for iface in card["supportedInterfaces"]:
        host = urlparse(iface["url"]).hostname or ""
        if not any(
            host == d or host.endswith("." + d) for d in domains
        ):
            return False
    return True
