import importlib

import pytest

EXAMPLES = [
    "ch01_demo_vs_production",
    "ch02_protocol",
    "ch05_contracts",
    "ch07_lifecycle",
    "ch08_streaming_push",
    "ch09_retries",
    "ch10_bindings",
    "ch11_identity",
    "ch12_signed_cards",
    "ch13_untrusted",
]


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_runs(name, capsys):
    importlib.import_module(name).main()
    assert capsys.readouterr().out.strip()
