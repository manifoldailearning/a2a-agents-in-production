"""A small validator for the JSON Schema subset skills declare."""

from __future__ import annotations

import re


def validate(args: dict, schema: dict) -> list[str]:
    """One plain sentence per problem; empty means valid."""
    errors = []
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args or args[name] in ("", None):
            errors.append(f"'{name}' is required.")
    for name, value in args.items():
        rule = props.get(name)
        if rule is None:
            errors.append(
                f"'{name}' is not an argument of this skill."
            )
            continue
        kind = rule.get("type")
        if kind == "string" and not isinstance(value, str):
            errors.append(f"'{name}' must be a string.")
        elif kind == "number" and not isinstance(
            value, int | float
        ):
            errors.append(f"'{name}' must be a number.")
        elif "pattern" in rule and not re.fullmatch(
            rule["pattern"].strip("^$"), str(value)
        ):
            errors.append(
                f"'{name}' must match {rule['pattern']}, got "
                f"{value!r}."
            )
        if "enum" in rule and value not in rule["enum"]:
            allowed = ", ".join(rule["enum"])
            errors.append(f"'{name}' must be one of: {allowed}.")
    return errors
