"""
The closed set of account types, on its own so anything can import it.

`app.db.schema` generates the `ck_account_type_valid` CHECK constraint
from this tuple, and `app.domain.accounts` validates form input against
it. It used to live in `accounts.py`, but that module now writes to the
database and so imports `app.db.schema` — keeping the constant there
would make the two import each other. This module imports nothing.

An ordered tuple, not a set: the account form's `<select>` renders from
it and a set has no stable order.
"""

ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")
