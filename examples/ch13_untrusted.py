"""Chapter 13: instructions hidden in another agent's output."""

import textwrap

from a2asys.cards import SKILL_EXT
from a2asys.configs import GUARDED, PRODUCTION, SECURED
from a2asys.evals import evaluate, run_task, setup, summarize
from a2asys.gateway import Gateway, Policy
from a2asys.protocol import USER, A2AError, message
from a2asys.requests import REQUESTS
from a2asys.store import SHIPMENTS

LEAK = "Claims desk: aiko.t@example.com, +1-555-0107."


def main():
    print("driver note on HP-20410:")
    note = SHIPMENTS["HP-20410"][2]
    for line in textwrap.wrap(note, 62)[:3]:
        print("  " + line)

    print(
        "\ninjected requests (5)  followed  extra calls  failed"
    )
    injected = [r for r in REQUESTS if r.injected]
    for cfg in (SECURED, GUARDED):
        runs = [run_task(cfg, r) for r in injected]
        obeyed = sum(run.obeyed for run, _, _ in runs)
        extra = sum(
            c.extra for run, _, _ in runs for c in run.calls
        )
        failed = sum(not g.success for _, g, _ in runs)
        print(
            f"{cfg.name:<21} {obeyed:>9} {extra:>12} {failed:>7}"
        )

    world = setup(PRODUCTION)
    gw = Gateway(
        Policy(
            allow={"support": {"orders", "billing", "carrier"}}
        ),
        world.clients,
        world.store.customers,
    )
    leak = message(
        USER,
        [{"text": LEAK}],
        "m-1",
    )
    for agent in ("carrier", "messaging"):
        try:
            gw.send("support", agent, leak, "t10")
        except A2AError as err:
            print(f"gateway, to {agent}: {err.message}")
    print("audit:")
    for e in gw.audit:
        print(f"  {e['request']} {e['agent']:<10} {e['outcome']}")

    print("\nsafety        ok  stray  shared")
    for cfg in (SECURED, GUARDED):
        s = summarize(evaluate(cfg))
        print(
            f"{cfg.name:<10} {s['ok']:>5.0%} {s['stray']:>6} "
            f"{s['shared']:>7}"
        )
    assert SKILL_EXT


if __name__ == "__main__":
    main()
