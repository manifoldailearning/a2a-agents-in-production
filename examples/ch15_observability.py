"""Chapter 15: one trace across agents, and metrics across
many."""

import hashlib

from a2asys.concierge import Concierge
from a2asys.configs import PRODUCTION
from a2asys.evals import setup
from a2asys.observe import Traced, Tracer, metrics
from a2asys.requests import BY_ID, REQUESTS


def traced_world(req_id, tracer):
    world = setup(PRODUCTION, salt=req_id)
    trace_id = hashlib.sha256(req_id.encode()).hexdigest()[:32]
    for key, client in world.clients.items():
        world.servers[key] = Traced(
            world.servers[key], key, tracer
        )
        client.transport.inner.server = world.servers[key]
        client.transport.server = world.servers[key]
        span = tracer.new_id()
        client.extra_headers["traceparent"] = tracer.traceparent(
            trace_id, span
        )
    return world


def main():
    tracer = Tracer()
    world = traced_world("t54", tracer)
    run = Concierge(PRODUCTION, world).handle(BY_ID["t54"])
    print("trace for t54 (lost parcel)")
    for s in tracer.spans:
        print(
            f" {s.trace_id[:8]} {s.agent:<7} {s.method:<11} "
            f"{s.skill or '-':<18} {s.state:<9} {s.ms:>4.0f} ms"
        )
    print(f"  tokens {run.tokens}, calls {run.hops}")

    tracer = Tracer()
    for req in REQUESTS:
        world = traced_world(req.id, tracer)
        Concierge(PRODUCTION, world).handle(req)
    print("\nagent     calls  errors   p50    p95")
    for agent, m in metrics(tracer.spans).items():
        print(
            f"{agent:<9} {m['calls']:>5} {m['errors']:>7} "
            f"{m['p50']:>5.0f} {m['p95']:>6.0f}"
        )


if __name__ == "__main__":
    main()
