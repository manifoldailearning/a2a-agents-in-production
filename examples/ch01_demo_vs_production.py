"""Chapter 1: three demo requests pass; sixty requests do not."""

from collections import Counter

from a2asys.configs import DEMO, PRODUCTION
from a2asys.evals import evaluate, run_task, summarize
from a2asys.requests import BY_ID, REQUESTS

DEMO_REQUESTS = [BY_ID[t] for t in ("t01", "t27", "t42")]


def main():
    for cfg in (DEMO, PRODUCTION):
        ok = [run_task(cfg, r)[1].success for r in DEMO_REQUESTS]
        print(
            f"{cfg.name:<11} demo requests passed: {sum(ok)} of 3"
        )

    print("\nall sixty requests")
    print("setup         ok  agent  stray  dupes  left  shared")
    for cfg in (DEMO, PRODUCTION):
        s = summarize(evaluate(cfg))
        print(
            f"{cfg.name:<11} {s['ok']:>4.0%} {s['right']:>5.0%} "
            f"{s['stray']:>6} {s['dupes']:>6} "
            f"{s['abandoned']:>5} "
            f"{s['shared']:>7}"
        )

    kinds = Counter(
        g.type for g in evaluate(DEMO) if not g.success
    )
    print("\ndemo failures by request type:")
    ranked = [f"{t} {n}" for t, n in kinds.most_common()]
    print("  " + ", ".join(ranked[:6]))
    print("  " + ", ".join(ranked[6:]))
    print(
        f"request types: {len(Counter(r.type for r in REQUESTS))}"
    )


if __name__ == "__main__":
    main()
