"""
Turning validation failures into something a form can show.

Pydantic's `str(ValidationError)` is a multi-line technical dump — model
name, per-field blocks, types, and a docs URL. That is the right thing in
a traceback and the wrong thing in an inline alert above a form, so every
handler that renders a validation failure to a person routes it through
here first.

This module deliberately imports nothing but pydantic: `app.db.schema`
imports `app.domain.accounts`, which imports this, so anything heavier
here would put a database import underneath the schema definition.
"""

from pydantic import ValidationError


def describe_validation_error(exc: ValidationError) -> str:
    """Flatten a pydantic error into one line fit for an inline form alert."""
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "input"
        message = err["msg"].removeprefix("Value error, ")
        parts.append(f"{field} {message[0].lower()}{message[1:]}")
    return "; ".join(parts)
