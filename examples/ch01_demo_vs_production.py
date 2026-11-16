"""Chapter 1: three demo requests pass; sixty requests do not."""

from collections import Counter

from a2asys.configs import DEMO, PRODUCTION
from a2asys.evals import evaluate, run_task, summarize
from a2asys.requests import BY_ID

DEMO_REQUESTS = [BY_ID[t] for t in ("t01", "t27", "t42")]
ROWS = [
    ("all 60 requests", "ok", "{:.0%}"),
    ("right agent first", "right", "{:.0%}"),
    ("stray side effects", "stray", "{}"),
    ("duplicate effects", "dupes", "{}"),
    ("tasks left behind", "abandoned", "{}"),
    ("details overshared", "shared", "{}"),
]


def main():
    configs = (DEMO, PRODUCTION)
    grades = {c.name: evaluate(c) for c in configs}
    summary = {name: summarize(g) for name, g in grades.items()}

    print(f"{'':<22}{'demo':>8}{'production':>12}")
    passed = [
        sum(run_task(c, r)[1].success for r in DEMO_REQUESTS)
        for c in configs
    ]
    print(
        f"{'demo requests':<22}{passed[0]:>6}/3{passed[1]:>10}/3"
    )
    for label, key, fmt in ROWS:
        a, b = (fmt.format(summary[c.name][key]) for c in configs)
        print(f"{label:<22}{a:>8}{b:>12}")

    print(f"\n{'by type':<22}{'demo':>8}{'production':>12}")
    totals = Counter(g.type for g in grades["demo"])
    for t in sorted(totals):
        ok = [
            sum(g.success for g in grades[c.name] if g.type == t)
            for c in configs
        ]
        n = totals[t]
        print(f"  {t:<20}{ok[0]:>6}/{n}{ok[1]:>10}/{n}")


if __name__ == "__main__":
    main()
