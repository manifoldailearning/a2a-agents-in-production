"""Chapter 12: deciding which Agent Cards to believe."""

import copy

from a2asys import jws
from a2asys.cards import cards
from a2asys.configs import NEED_TO_KNOW, SECURED
from a2asys.evals import evaluate, run_task, summarize
from a2asys.registry import Registry, sign_all
from a2asys.requests import REQUESTS


def main():
    signed = sign_all(cards(True))
    keys = {k: jws.public(v) for k, v in jws.demo_keys().items()}
    sig = signed["billing"]["signatures"][0]
    print(
        "protected header:",
        jws.unb64url(sig["protected"]).decode(),
    )
    print(
        "verified with:", jws.verify_card(signed["billing"], keys)
    )

    tampered = copy.deepcopy(signed["billing"])
    tampered["supportedInterfaces"][0]["url"] = (
        "https://billing.attacker.example/a2a"
    )
    print(
        "tampered url verifies:", jws.verify_card(tampered, keys)
    )
    print(
        "impostor verifies:",
        jws.verify_card(signed["impostor"], keys),
    )

    registry = Registry(signed, verify=True)
    names = sorted(registry.visible())
    print("\nvisible:", " ".join(names[:5]))
    print("        ", " ".join(names[5:]))
    print("refused:", registry.refused)

    print("\ndiscovery     ok  agent  sent to impostor")
    for cfg in (NEED_TO_KNOW, SECURED):
        runs = [run_task(cfg, r) for r in REQUESTS]
        s = summarize([g for _, g, _ in runs])
        hit = sum(
            any(c.agent == "impostor" for c in run.calls)
            for run, _, _ in runs
        )
        print(
            f"{cfg.name:<12} {s['ok']:>4.0%} {s['right']:>5.0%} "
            f"{hit:>17}"
        )
    assert summarize(evaluate(SECURED))["ok"] >= 0.8


if __name__ == "__main__":
    main()
