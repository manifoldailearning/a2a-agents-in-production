"""Command line: run the ladder, one configuration, or one
agent."""

from __future__ import annotations

import argparse
import json
import sys

from a2asys.cards import cards
from a2asys.configs import CONFIGS, LADDER
from a2asys.evals import evaluate, setup, summarize
from a2asys.protocol import USER, message


def row(name: str, s: dict) -> str:
    return (
        f"{name:<13} {s['ok']:>4.0%} {s['right']:>5.0%} "
        f"{s['stray']:>6} "
        f"{s['dupes']:>6} {s['abandoned']:>5} {s['shared']:>7} "
        f"{s['tokens']:>7}"
    )


HEADER = (
    "step            ok  agent  stray  dupes  left  shared"
    "  tokens"
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="a2asys")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval", help="evaluate one configuration")
    e.add_argument(
        "--config", default="production", choices=sorted(CONFIGS)
    )
    sub.add_parser(
        "ladder", help="evaluate every step of the ladder"
    )
    c = sub.add_parser("card", help="print an agent's card")
    c.add_argument("agent")
    s = sub.add_parser(
        "send", help="send one prose message to an agent"
    )
    s.add_argument("agent")
    s.add_argument("text")
    args = p.parse_args(argv)

    if args.cmd == "eval":
        summary = summarize(evaluate(CONFIGS[args.config]))
        print(HEADER)
        print(row(args.config, summary))
        if args.config == "production" and summary["ok"] < 0.9:
            print(
                "production is below its 90% bar", file=sys.stderr
            )
            return 1
        return 0
    if args.cmd == "ladder":
        print(HEADER)
        for cfg in LADDER:
            print(row(cfg.name, summarize(evaluate(cfg))))
        return 0
    if args.cmd == "card":
        print(json.dumps(cards(True)[args.agent], indent=2))
        return 0
    world = setup(CONFIGS["production"])
    msg = message(USER, [{"text": args.text}], "cli-1")
    print(
        json.dumps(world.clients[args.agent].send(msg), indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
