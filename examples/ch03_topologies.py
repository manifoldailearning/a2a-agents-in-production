"""Chapter 3: one agent, a team of agents, or one agent per
skill."""

from a2asys.configs import PRODUCTION
from a2asys.evals import evaluate, summarize

# Systems the front agent can change directly, by holding their
# credentials. Delegation leaves that with the owning teams.
WRITE_ACCESS = {"monolith": 4, "delegate": 0, "micro": 0}


def main():
    print(
        "topology    ok  agent  p50 s  calls  tokens  write "
        "access"
    )
    for topology in ("monolith", "delegate", "micro"):
        cfg = PRODUCTION.but(name=topology, topology=topology)
        s = summarize(evaluate(cfg))
        print(
            f"{topology:<9} {s['ok']:>4.0%} {s['right']:>5.0%} "
            f"{s['latency']:>6.1f} {s['hops']:>6.1f} "
            f"{s['tokens']:>7} "
            f"{WRITE_ACCESS[topology]:>13}"
        )


if __name__ == "__main__":
    main()
