"""enforce the account-currency rule in the database

An entry must carry its account's currency. `assert_accounts_valid` in
`app/domain/ledger.py` has checked this since the rule was introduced,
and still does, because that is what turns a bad submission into a
readable form error. Until now nothing else did: a raw INSERT could put a
EUR entry on a USD account, and the overview, which sums each account's
balance under `accounts.currency`, would then add two currencies into one
number without any error.

Two triggers, because the rule has two sides:

**`ledger_entries_match_account_currency`** — a BEFORE INSERT OR UPDATE
row trigger that looks up the entry's account and refuses a mismatch. An
entry naming an account that does not exist is left to the foreign key,
which reports that more precisely.

**`accounts_currency_immutable`** — refuses an UPDATE that changes an
account's currency. The domain already treats the currency as fixed at
creation. Without this, rewriting it would silently put every existing
entry on that account in violation, and a check on entries alone could
never see it.

Both raise `check_violation`, which SQLAlchemy reports as
`IntegrityError`.

Existing data: creating the triggers does not re-check rows already
there. Entries written before the application validated currencies could
mismatch. They can be listed with

    SELECT e.id, e.currency, a.currency
      FROM ledger_entries e JOIN accounts a ON a.id = e.account_id
     WHERE e.currency <> a.currency;

The SQL is spelled out here rather than imported from `app.db.schema`,
for the reason given in de4f1aec2fe6. `schema.py` carries an identical
copy for `metadata.create_all`.
"""

from alembic import op

revision = "3c9e5a7b2d14"
down_revision = "7d2e4b9c1a58"
branch_labels = None
depends_on = None

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


def upgrade() -> None:
    # See 7d2e4b9c1a58 for why the plpgsql `%` and `:=` are safe through op.execute.
    for statement in (
        ENTRY_CURRENCY_FUNCTION,
        ENTRY_CURRENCY_TRIGGER,
        ACCOUNT_CURRENCY_FUNCTION,
        ACCOUNT_CURRENCY_TRIGGER,
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS accounts_currency_immutable ON accounts")
    op.execute("DROP FUNCTION IF EXISTS accounts_currency_immutable()")
    op.execute(
        "DROP TRIGGER IF EXISTS ledger_entries_match_account_currency ON ledger_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_match_account_currency()")
