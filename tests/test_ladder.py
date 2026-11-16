from a2asys.configs import CONFIGS, DEMO, LADDER, PRODUCTION
from a2asys.evals import evaluate, gate, summarize
from a2asys.requests import REQUESTS


def test_sixty_requests():
    assert len(REQUESTS) == 60


def test_production_is_safe_and_good():
    s = summarize(evaluate(PRODUCTION))
    assert s["ok"] >= 0.9
    assert s["stray"] == s["dupes"] == s["abandoned"] == 0
    assert s["shared"] == 0


def test_each_step_does_its_job():
    s = {c.name: summarize(evaluate(c)) for c in LADDER}
    assert s["demo"]["ok"] < 0.2
    assert s["cards"]["right"] > s["demo"]["right"]
    assert (
        s["lifecycle"]["abandoned"]
        == 0
        < s["contracts"]["abandoned"]
    )
    assert s["idempotent"]["dupes"] == 0 < s["lifecycle"]["dupes"]
    assert (
        s["need-to-know"]["shared"]
        < s["idempotent"]["shared"] / 10
    )
    assert s["guarded"]["obeyed"] == 0 < s["secured"]["obeyed"]


def test_results_are_reproducible():
    assert summarize(evaluate(DEMO)) == summarize(evaluate(DEMO))


def test_gate_blocks_a_regression():
    base = evaluate(PRODUCTION)
    bad = evaluate(
        PRODUCTION.but(
            edits=(
                (
                    "carrier",
                    "track-package",
                    "Where is my order?",
                ),
            )
        )
    )
    ok, why = gate(base, bad)
    assert not ok and why


def test_large_model_config_exists():
    assert (
        summarize(evaluate(CONFIGS["large-model"]))["ok"] >= 0.9
    )
