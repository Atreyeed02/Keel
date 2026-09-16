"""
Account creation input.

Accounts are reference data for the ledger: entries point at them, but
creating one carries no double-entry invariant, so there's no event or
idempotency handling here — just validation of what the form submitted.

`account_type` is closed over the five classical types the schema
comments reference; the balance-sign logic in the overview page keys
off it, so an unrecognised value would silently render a wrong balance.
"""

from pydantic import BaseModel, Field, ValidationError, field_validator

ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")


class InvalidAccountError(ValueError):
    """Raised when submitted account data fails validation."""


class AccountInput(BaseModel):
    name: str = Field(max_length=255)
    account_type: str
    currency: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required")
        return v

    @field_validator("account_type")
    @classmethod
    def validate_account_type(cls, v: str) -> str:
        if v not in ACCOUNT_TYPES:
            raise ValueError(f"must be one of: {', '.join(ACCOUNT_TYPES)}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("must be a 3-letter code")
        return v


def _describe(exc: ValidationError) -> str:
    """Flatten a pydantic error into one line fit for an inline form alert."""
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "input"
        message = err["msg"].removeprefix("Value error, ")
        parts.append(f"{field} {message[0].lower()}{message[1:]}")
    return "; ".join(parts)


def validate_account(data: dict[str, str]) -> AccountInput:
    """Validate raw form data, raising a single readable error on failure."""
    try:
        return AccountInput.model_validate(data)
    except ValidationError as exc:
        raise InvalidAccountError(_describe(exc)) from exc
