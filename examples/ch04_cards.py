"""Chapter 4: cards written for the agents that read them."""

from collections import Counter

from a2asys.cards import DESIGNED, NAIVE, card_text
from a2asys.configs import CARDS, DEMO
from a2asys.evals import run_task, summarize
from a2asys.model import tokens
from a2asys.registry import validate_card
from a2asys.requests import REQUESTS


def main():
    for key in ("billing", "returns"):
        print(
            f"{key}: {NAIVE[key]['name']!r} -> "
            f"{DESIGNED[key]['name']!r}"
        )
        skill = DESIGNED[key]["skills"][0]
        print(f"  skill {skill['id']}: tags {skill['tags'][:2]}")
    print(
        "card text, tokens: naive",
        sum(tokens(card_text(c)) for c in NAIVE.values()),
        "designed",
        sum(tokens(card_text(c)) for c in DESIGNED.values()),
    )
    broken = {
        k: v
        for k, v in DESIGNED["orders"].items()
        if k != "skills"
    }
    print("card without skills:", validate_card(broken))

    print("\ncards      ok  agent  went to the wrong agent")
    for cfg in (DEMO, CARDS):
        runs = [run_task(cfg, r) for r in REQUESTS]
        s = summarize([g for _, g, _ in runs])
        wrong = Counter(
            run.first[i]
            for run, _, _ in runs
            for i, step in enumerate(run.request.steps)
            if i in run.first and run.first[i] != step.agent
        )
        top = ", ".join(
            f"{k} {n}" for k, n in wrong.most_common(3)
        )
        print(
            f"{cfg.name:<8} {s['ok']:>4.0%} {s['right']:>5.0%}  "
            f"{top}"
        )


if __name__ == "__main__":
    main()
