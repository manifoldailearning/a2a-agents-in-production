"""A simulated language model that delegates work to agents.

It picks an agent by comparing what it wants done with the text
of each Agent Card, writes requests from what the user said,
reads results, answers follow-up questions and is sometimes
steered by instructions it finds inside another agent's output.
Every weakness is a named probability, so a change to a card, a
message format or a client setting changes the measured results
reproducibly.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass

CONCEPTS = {
    "order": "order orders purchase purchased bought",
    "ship": "ship shipped shipment shipping deliver delivery "
    "delivered arrive arrival package parcel transit eta courier",
    "track": "track tracking tracked whereabouts",
    "refund": "refund refunds refunded money reimburse "
    "reimbursement payment payments charge charged",
    "cancel": "cancel cancelled canceled cancellation",
    "return": "return returns returned rma",
    "address": "address readdress redirect",
    "price": "price prices cheaper drop dropped adjustment match",
    "invoice": "invoice invoices receipt receipts vat",
    "product": "product products catalog item items gear spec "
    "specs size sizing material waterproof weight",
    "stock": "stock inventory available availability",
    "lost": "lost missing stolen never investigation investigate "
    "claim claims",
    "status": "status state progress",
    "campaign": "campaign campaigns marketing newsletter "
    "promotion",
    "supplier": "supplier suppliers vendor purchase-order "
    "procurement replenish",
    "staff": "employee employees leave payroll staff",
    "adjust": "adjust adjustment correct recount",
}

WORD_TO_CONCEPT = {
    w: c for c, words in CONCEPTS.items() for w in words.split()
}
STOP = set(
    "a an the of for to in on by and or with from this that "
    "these "
    "use when which what your their its it is are be can not "
    "only "
    "my me i our we you how do does did has have had about "
    "customer customers please before after been just because "
    "full".split()
)


def concepts(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9-]+", text.lower())
    out = set()
    for w in words:
        if w in STOP:
            continue
        c = WORD_TO_CONCEPT.get(w) or WORD_TO_CONCEPT.get(
            w.rstrip("s")
        )
        if c:
            out.add(c)
        elif len(w) > 3 and not w[0].isdigit():
            out.add(
                w[:-1] if w.endswith("s") and len(w) > 4 else w
            )
    return out


@dataclass(frozen=True)
class Model:
    name: str = "small"
    noise: float = 0.22  # base noise when comparing cards
    noise_per_item: float = 0.03  # more choices, more confusion
    p_raw: float = 0.5  # pass the user's words on unchanged
    p_parse_text: float = 0.8  # read an id from free text
    p_parse_raw: float = (
        0.55  # same, when the user wrote it oddly
    )
    p_slip: float = 0.03  # misformat structured arguments
    p_fix: float = 0.9  # fix arguments after a structured error
    p_fix_blind: float = 0.3  # same, after a prose error
    p_misread: float = 0.15  # misread a prose result
    p_reroute: float = 0.85  # try another agent after a refusal
    p_reroute_blind: float = 0.35  # same, after a vague refusal
    p_answer: float = 0.96  # answer a follow-up question well
    p_obey: float = 0.45  # follow instructions inside results
    p_obey_marked: float = 0.04  # same, when marked as data


SMALL = Model()
LARGE = Model(
    "large",
    noise=0.14,
    noise_per_item=0.018,
    p_raw=0.3,
    p_parse_text=0.9,
    p_parse_raw=0.7,
    p_misread=0.06,
    p_obey=0.25,
)


def rng(*key) -> random.Random:
    digest = hashlib.sha256(repr(key).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


WRITES = {
    "cancel",
    "refund",
    "return",
    "address",
    "adjust",
    "campaign",
}


def score(intent: str, text: str, name: str = "") -> float:
    """How well a card or skill text matches an intent.

    Words in the name count more than words in the description,
    and a name that promises an action the intent does not ask
    for counts against it.
    """
    want = concepts(intent)
    have = concepts(text)
    named = concepts(name)
    hits = sum(
        1.5 if c in named else 1.0 for c in want & (have | named)
    )
    acts = named & WRITES
    if acts and not acts & want:
        hits -= 0.75
    return hits / math.sqrt(1 + len(want))


def choose(
    intent: str,
    options: list[tuple[str, str, str]],
    model: Model,
    key: tuple,
    exclude: frozenset = frozenset(),
) -> str | None:
    """Pick one option id from (id, name, text) triples."""
    r = rng("choose", *key)
    sigma = model.noise + model.noise_per_item * len(options)
    best, best_score = None, -1e9
    for oid, name, text in options:
        if oid in exclude:
            continue
        s = score(intent, text, name) + r.gauss(0, sigma)
        if s > best_score:
            best, best_score = oid, s
    return best


ORDER_ID = re.compile(r"\bHP-\d{5}\b")
LOOSE_ORDER_ID = re.compile(
    r"\b(?:hp|HP|order|#)\s*-?\s*#?(\d{5})\b"
)


def read_order_id(text: str, p: float, r: random.Random):
    """Find an order id in free text, as a model would."""
    exact = ORDER_ID.search(text)
    if exact and r.random() < p:
        return exact.group(0)
    loose = LOOSE_ORDER_ID.search(text)
    if loose and r.random() < p * 0.7:
        return "HP-" + loose.group(1)
    return None


def tokens(text: str) -> int:
    """A rough token count: about 1.3 tokens per word."""
    return math.ceil(len(text.split()) * 1.3)
