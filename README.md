# A2A and Multi-Agent Systems in Production

Companion code for the book **A2A and Multi-Agent Systems in
Production** by Nachiketh Murthy.

A support agent that delegates to other teams' agents is easy to
demo and hard to run. This repository takes one such system from
a demo setup to one you can defend in production, and measures
every step:

- when to split work across agents at all: one agent, a team of
  agents, or one agent per skill;
- Agent Cards a calling agent can choose between, and registries
  full of look-alikes;
- structured skill requests and results instead of prose;
- what to share with each agent, and what to keep;
- the task lifecycle: follow-up questions, customer verification,
  cancellation and work that takes days;
- streaming, push notifications and webhook safety;
- lost replies, retries and duplicate refunds;
- JSON-RPC, HTTP+JSON and Server-Sent Events, versions,
  extensions and tenants;
- identity between agents: audiences, scopes, token exchange;
- signed Agent Cards (JWS over canonical JSON) and trusted keys;
- instructions planted in another agent's output, approvals and a
  gateway with an egress policy;
- conformance tests, an evaluation gate, tracing and metrics.

The protocol code follows **A2A 1.0**: Agent Cards at
`/.well-known/agent-card.json` with `supportedInterfaces`, the
`A2A-Version` and `A2A-Extensions` headers, `SendMessage`,
`SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask`, push notification configurations,
`GetExtendedAgentCard`, task states such as
`TASK_STATE_INPUT_REQUIRED`, and the A2A error codes -32001 to
-32009.

Everything runs offline with the Python standard library. The
business, Harbor & Pine, is a **fictional** outdoor-gear retailer
with teams for orders, returns, billing and the catalog, and an
outside courier, ParcelPath. The language models are
**simulated**, with the weaknesses that matter for delegation
built in: they choose agents by the words on their cards, pass on
what the user typed, misread prose, give up on tasks that ask a
question, retry after timeouts and sometimes follow instructions
they find in another agent's output. Change a card or a setting
and the measured results change, reproducibly.

> The numbers come from a simulator and 60 scripted support
> requests, not from a production system. They show the methods and
> the shape of each trade-off; they are not benchmarks of any model
> or product.

## Quick start

```bash
git clone https://github.com/manifoldailearning/a2a-agents-in-production
cd a2a-agents-in-production
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"      # Python 3.11+, no runtime dependencies
pytest -q
python examples/ch01_demo_vs_production.py
```

From the command line:

```bash
a2asys ladder
a2asys eval --config demo
a2asys card billing
a2asys send catalog "Is the Ridgeline 2P tent waterproof?"
```

## The ladder

`a2asys/configs.py` defines each step; each changes one thing.
`examples/ch16_improvement_loop.py` prints this table.

| Step | Tasks ok | Right agent first | Stray effects | Duplicates | Tasks left behind | Data overshared |
|---|---|---|---|---|---|---|
| demo | 12% | 23% | 10 | 0 | 2 | 395 |
| cards | 35% | 73% | 4 | 2 | 7 | 391 |
| contracts | 62% | 79% | 4 | 6 | 15 | 434 |
| lifecycle | 73% | 81% | 4 | 2 | 0 | 439 |
| idempotent | 77% | 81% | 4 | 0 | 0 | 429 |
| need-to-know | 77% | 81% | 4 | 0 | 0 | 6 |
| secured | 82% | 86% | 5 | 0 | 0 | 6 |
| guarded | 85% | 86% | 4 | 0 | 0 | 2 |
| production | 95% | 95% | 0 | 0 | 0 | 0 |

## Map of the repository

| Chapter | Modules | Example |
|---|---|---|
| 1 Why multi-agent systems break | `evals`, `configs` | `ch01_demo_vs_production.py` |
| 2 How A2A works | `protocol`, `server`, `client` | `ch02_protocol.py` |
| 3 When to use more than one agent | `topology` | `ch03_topologies.py` |
| 4 Agent Cards and discovery | `cards`, `registry` | `ch04_cards.py` |
| 5 Delegation contracts | `schema`, `agents` | `ch05_contracts.py` |
| 6 Context and need-to-know | `concierge` | `ch06_context.py` |
| 7 The task lifecycle | `server`, `concierge` | `ch07_lifecycle.py` |
| 8 Streaming and push | `push`, `client` | `ch08_streaming_push.py` |
| 9 Retries and idempotency | `client`, `server` | `ch09_retries.py` |
| 10 Bindings, versions, tenants | `http` | `ch10_bindings.py` |
| 11 Identity and authorization | `auth` | `ch11_identity.py` |
| 12 Trusting Agent Cards | `jws`, `registry` | `ch12_signed_cards.py` |
| 13 Untrusted agents and results | `gateway`, `concierge` | `ch13_untrusted.py` |
| 14 Testing | `conformance`, `evals` | `ch14_testing.py` |
| 15 Observability | `observe` | `ch15_observability.py` |
| 16 Gateways and the loop | `gateway`, `configs` | `ch16_improvement_loop.py` |

## Go further

This repository accompanies the book. If you want to build a
complete production agentic system with these patterns, alongside
MCP tool integration, retrieval, evaluation, governance and
operations, with live sessions and review of your design
decisions, see the
[Agentic AI Enterprise Mastery Bootcamp](https://manifoldailearning.com/services/agenticaibootcamp?utm_source=github&utm_medium=readme&utm_campaign=a2a-agents-in-production)
from Manifold AI Learning.

The companion book on tools is **MCP and Tool Integration for AI
Agents**, with code at
[mcp-tools-for-agents](https://github.com/manifoldailearning/mcp-tools-for-agents).

## License

MIT. See `LICENSE`.
