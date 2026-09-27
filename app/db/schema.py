"""
SQLAlchemy Core table definitions.

Two concerns live here, deliberately kept separate:

1. Event log (`events`) — the source of truth. Every state change is
   appended here first; nothing is ever updated or deleted.
2. Ledger read model (`accounts`, `transactions`, `ledger_entries`) —
   derived from the event log, structured for double-entry invariants
   and fast balance queries. Rebuildable from `events` if needed.

`idempotency_keys` backs the idempotency layer: a client-supplied key
is stored with a hash of the request body and the response that was
returned, so a retried request short-circuits instead of re-applying.

Three rules are enforced by Postgres triggers rather than only by the code
that writes: `events` refuses UPDATE, DELETE and TRUNCATE, every
transaction's entries must balance per currency at commit, and an entry
must carry its account's currency, which never changes. Migrations
7d2e4b9c1a58 and 3c9e5a7b2d14 create them for `alembic upgrade head`; the
`after_create` listeners below create the same ones for
`metadata.create_all`, which is what the integration tests build their
schema with.
"""

import uuid

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

# The five account types are defined once, in the domain layer, and the
# CHECK constraint below is generated from them so the database and the
# Python validator cannot drift apart. `app.domain.account_types` imports
# nothing, so this does not create a cycle with the domain modules that
# import this one (`ledger`, `accounts`, `rebuild`).
from app.domain.account_types import ACCOUNT_TYPES

metadata = MetaData()


def _ddl(sql: str) -> DDL:
    """
    Wrap raw SQL for an event listener. `DDL` runs its text through
    Python %-formatting, so the literal `%` that plpgsql's RAISE and
    format() use has to be doubled first.
    """
    return DDL(sql.replace("%", "%%"))


# --- Event log ---------------------------------------------------------

events = Table(
    "events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    # The log's total order. Database-generated and monotonic: a
    # timestamp cannot do this job, because Postgres CURRENT_TIMESTAMP is
    # transaction-start time, so every event appended in one transaction
    # would share a value and their relative order would be lost.
    # GENERATED ALWAYS means the application cannot supply or fudge it.
    Column("sequence", BigInteger, Identity(always=True), unique=True, index=True, nullable=False),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    # Append-only: no updated_at, no soft-delete flag. If it's wrong,
    # a compensating event gets appended, not a mutation.
)

# Declared here as well as in the migration so `metadata.create_all`
# (used by the integration tests) and `alembic upgrade head` produce the
# same schema, and autogenerate doesn't propose dropping them.
Index("ix_events_created_at", events.c.created_at.desc())
Index("ix_events_aggregate", events.c.aggregate_type, events.c.aggregate_id)

# The append-only rule above, enforced. Statement-level so it covers
# TRUNCATE too, which row triggers never see. DROP TABLE is untouched —
# that is a schema change, not a rewrite of history, and the tests rely on
# it. SQLSTATE 23000 makes SQLAlchemy raise IntegrityError, which is what
# this is: a write the schema forbids.
EVENTS_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$
"""
EVENTS_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_append_only()
"""
event.listen(events, "after_create", _ddl(EVENTS_APPEND_ONLY_FUNCTION))
event.listen(events, "after_create", _ddl(EVENTS_APPEND_ONLY_TRIGGER))
# The trigger goes with the table; the function would otherwise outlive it.
event.listen(events, "after_drop", _ddl("DROP FUNCTION IF EXISTS events_append_only()"))

# --- Ledger read model ---------------------------------------------------

accounts = Table(
    "accounts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("name", String(255), nullable=False),
    Column("account_type", String(32), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    # Enforced in the database, not just in `validate_account`: the
    # overview page keys its balance sign off this column, so a row
    # written by anything that bypasses the app — a migration, a manual
    # INSERT, a future importer — would otherwise render a wrong balance.
    CheckConstraint(
        f"account_type IN ({', '.join(repr(t) for t in ACCOUNT_TYPES)})",
        name="ck_account_type_valid",
    ),
)

# The domain treats an account's currency as fixed at creation. Rewriting it
# would put every entry already on the account in violation of the
# entry-currency rule below, which only looks at entries, so the account
# side is closed here.
ACCOUNT_CURRENCY_FUNCTION = """
CREATE OR REPLACE FUNCTION accounts_currency_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.currency IS DISTINCT FROM OLD.currency THEN
        RAISE EXCEPTION 'account % is %: an account''s currency is fixed when it is created',
            OLD.id, OLD.currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""
ACCOUNT_CURRENCY_TRIGGER = """
CREATE TRIGGER accounts_currency_immutable
    BEFORE UPDATE OF currency ON accounts
    FOR EACH ROW EXECUTE FUNCTION accounts_currency_immutable()
"""
event.listen(accounts, "after_create", _ddl(ACCOUNT_CURRENCY_FUNCTION))
event.listen(accounts, "after_create", _ddl(ACCOUNT_CURRENCY_TRIGGER))
event.listen(accounts, "after_drop", _ddl("DROP FUNCTION IF EXISTS accounts_currency_immutable()"))

transactions = Table(
    "transactions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    # Posting order, for the same reason `events.sequence` exists: Postgres
    # evaluates CURRENT_TIMESTAMP at transaction start, so every transaction
    # written inside one database transaction shares a `created_at` and their
    # relative order is lost. The listing pages sort by this instead, which
    # also keeps pagination stable — tied rows can otherwise drift between
    # pages from one request to the next.
    Column("sequence", BigInteger, Identity(always=True), unique=True, index=True, nullable=False),
    Column("description", String(512), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

ledger_entries = Table(
    "ledger_entries",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column(
        "transaction_id",
        UUID(as_uuid=True),
        ForeignKey("transactions.id"),
        nullable=False,
    ),
    Column("account_id", UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False),
    Column("entry_type", String(6), nullable=False),  # 'debit' | 'credit'
    Column("amount", Numeric(18, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("entry_type IN ('debit', 'credit')", name="ck_entry_type_valid"),
    CheckConstraint("amount > 0", name="ck_amount_positive"),
    # Double-entry balance (sum(debits) == sum(credits) per transaction,
    # per currency) is checked twice: by `assert_balanced` in
    # app/domain/ledger.py, which turns a bad submission into a readable
    # form error, and by the constraint trigger below, which holds for
    # every writer — a migration, a manual INSERT, a future importer.
)

# Deferred to commit, because a transaction's entries arrive one row at a
# time and are only balanced once the last one is in. It fires per row, so
# a four-entry transaction runs four checks at commit, each an index
# lookup on ix_ledger_entries_transaction_id. An UPDATE re-checks both the
# old and the new transaction, since moving an entry unbalances the one it
# left. A transaction with no entries at all is not caught — there is no
# entry row to fire on — and is harmless to balances.
LEDGER_BALANCE_FUNCTION = """
CREATE OR REPLACE FUNCTION ledger_entries_assert_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    touched uuid[] := '{}';
    problem text;
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        touched := array_append(touched, NEW.transaction_id);
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        touched := array_append(touched, OLD.transaction_id);
    END IF;

    SELECT string_agg(
               format('transaction %s is off by %s %s', transaction_id, net, currency),
               '; ' ORDER BY transaction_id, currency)
      INTO problem
      FROM (SELECT transaction_id, currency,
                   sum(CASE WHEN entry_type = 'debit' THEN amount ELSE -amount END) AS net
              FROM ledger_entries
             WHERE transaction_id = ANY (touched)
             GROUP BY transaction_id, currency) AS per_currency
     WHERE net <> 0;

    IF problem IS NOT NULL THEN
        RAISE EXCEPTION 'ledger entries do not balance: %', problem
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$
"""
LEDGER_BALANCE_TRIGGER = """
CREATE CONSTRAINT TRIGGER ledger_entries_balanced
    AFTER INSERT OR UPDATE OR DELETE ON ledger_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_assert_balanced()
"""
event.listen(ledger_entries, "after_create", _ddl(LEDGER_BALANCE_FUNCTION))
event.listen(ledger_entries, "after_create", _ddl(LEDGER_BALANCE_TRIGGER))
event.listen(
    ledger_entries, "after_drop", _ddl("DROP FUNCTION IF EXISTS ledger_entries_assert_balanced()")
)

# An entry carries its account's currency. `assert_accounts_valid` checks
# this before posting, to produce a readable error; this holds for every
# other writer. A missing account is left to the foreign key, which says so
# more precisely.
ENTRY_CURRENCY_FUNCTION = """
CREATE OR REPLACE FUNCTION ledger_entries_match_account_currency() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    account_currency text;
BEGIN
    SELECT currency INTO account_currency FROM accounts WHERE id = NEW.account_id;
    IF account_currency IS NOT NULL AND account_currency <> NEW.currency THEN
        RAISE EXCEPTION 'entry currency % does not match account %, which is %',
            NEW.currency, NEW.account_id, account_currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""
ENTRY_CURRENCY_TRIGGER = """
CREATE TRIGGER ledger_entries_match_account_currency
    BEFORE INSERT OR UPDATE OF account_id, currency ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_match_account_currency()
"""
event.listen(ledger_entries, "after_create", _ddl(ENTRY_CURRENCY_FUNCTION))
event.listen(ledger_entries, "after_create", _ddl(ENTRY_CURRENCY_TRIGGER))
event.listen(
    ledger_entries,
    "after_drop",
    _ddl("DROP FUNCTION IF EXISTS ledger_entries_match_account_currency()"),
)

# Declared here as well as in the migration, for the same reason as the
# `events` indexes above. Each one backs a query the app actually runs:
# the overview's per-account balance join, the transaction-detail entry
# lookup, and the overview's "recent transactions" ordering.
Index("ix_ledger_entries_account_id", ledger_entries.c.account_id)
Index("ix_ledger_entries_transaction_id", ledger_entries.c.transaction_id)
# Kept even though nothing orders by created_at any more: it backs the
# /transactions date-range filter.
Index("ix_transactions_created_at", transactions.c.created_at.desc())
# The /transactions search is `description ILIKE '%term%'`. A leading
# wildcard rules out a btree index, so without this the search reads every
# row. A trigram GIN index answers substring matches, ILIKE and ESCAPE
# included. It needs pg_trgm, which `create_all` installs here and
# migration 5e8d2a1f9c63 installs for `alembic upgrade head`.
event.listen(transactions, "before_create", DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
Index(
    "ix_transactions_description_trgm",
    transactions.c.description,
    postgresql_using="gin",
    postgresql_ops={"description": "gin_trgm_ops"},
)

# --- Idempotency layer ---------------------------------------------------

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("key", String(255), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("response_body", JSONB, nullable=True),
    Column("response_status", String(3), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("key", name="uq_idempotency_key"),
)

# Backs the retention cleanup's range delete, `created_at < now() - interval`.
Index("ix_idempotency_keys_created_at", idempotency_keys.c.created_at)
