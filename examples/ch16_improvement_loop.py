"""Chapter 16: the whole ladder, what still fails, and one fix."""

from a2asys.configs import CONFIGS, LADDER, PRODUCTION
from a2asys.evals import evaluate, gate, run_task, summarize
from a2asys.requests import BY_ID

FIX = (
    (
        "billing",
        "refund-payment",
        "Refund an order whose parcel was lost",
    ),
)


def main():
    print(
        "step            ok  agent  stray  dupes  left  shared  "
        "tokens"
    )
    for cfg in [*LADDER, CONFIGS["large-model"]]:
        s = summarize(evaluate(cfg))
        print(
            f"{cfg.name:<13} {s['ok']:>4.0%} {s['right']:>5.0%} "
            f"{s['stray']:>6} {s['dupes']:>6} "
            f"{s['abandoned']:>5} {s['shared']:>7} "
            f"{s['tokens']:>7}"
        )

    print("\nproduction failures")
    base = evaluate(PRODUCTION)
    for g in base:
        if g.success:
            continue
        run, _, _ = run_task(PRODUCTION, BY_ID[g.task])
        req = run.request
        wrong = [
            f"step {i + 1} went to {run.first[i]}, not {s.agent}"
            for i, s in enumerate(req.steps)
            if i in run.first and run.first[i] != s.agent
        ]
        print(
            f"  {g.task} {g.type:<7} {g.why:<8} "
            f"{'; '.join(wrong)}"
        )

    cand = evaluate(PRODUCTION.but(name="fix", edits=FIX))
    ok, why = gate(base, cand)
    print(
        "\nwith a lost-parcel example on refund-payment: "
        f"{summarize(cand)['ok']:.0%}, gate "
        f"{'passes' if ok else 'fails'}"
    )


if __name__ == "__main__":
    main()
