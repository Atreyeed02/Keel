"""enforce the two ledger rules in the database: append-only events, balanced entries

Both rules already held in practice, but only because every writer went
through the application. This moves them into Postgres, so they also hold
for a migration, a manual `psql` session or a future importer.

**`events` is append-only.** `app/db/schema.py` has always said so in a
comment. A statement-level trigger now refuses UPDATE, DELETE and
TRUNCATE on the table. It is statement-level because TRUNCATE fires no
row triggers. DROP TABLE is unaffected: that changes the schema, it does
not rewrite history.

**Every transaction balances per currency.** `assert_balanced` in
`app/domain/ledger.py` checked this before anything was written. It
still does, because that is what turns a bad form submission into a
readable error. The new constraint trigger is the backstop: deferred
until commit, because a transaction's entries arrive one row at a time
and only balance once the last one is in. An UPDATE re-checks both the
old and the new `transaction_id`, since moving an entry unbalances the
transaction it left.

Both raise SQLSTATEs in class 23 (integrity constraint violation), so
SQLAlchemy reports them as `IntegrityError`.

Existing data: creating the balance trigger does not re-check rows that
are already there. Every row the application wrote passed
`assert_balanced`, so none should fail; anything inserted around the app
is not detected by this migration and should be checked by hand if it
might exist.

The SQL is spelled out here rather than imported from `app.db.schema`,
for the reason given in de4f1aec2fe6: a migration has to keep producing
the DDL it produced the day it was written. `schema.py` carries its own
copy for `metadata.create_all`; the two are kept identical by hand.
"""

from alembic import op

revision = "7d2e4b9c1a58"
down_revision = "fca143a4d6d9"
branch_labels = None
depends_on = None

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


def upgrade() -> None:
    # op.execute wraps each string in text(), which doubles the literal `%`
    # in the plpgsql bodies before psycopg sees them — psycopg would
    # otherwise read `%` as a placeholder even with no parameters. `:=` is
    # safe: text() only treats `:name` as a bind parameter.
    for statement in (
        EVENTS_APPEND_ONLY_FUNCTION,
        EVENTS_APPEND_ONLY_TRIGGER,
        LEDGER_BALANCE_FUNCTION,
        LEDGER_BALANCE_TRIGGER,
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_balanced ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_assert_balanced()")
    op.execute("DROP TRIGGER IF EXISTS events_append_only ON events")
    op.execute("DROP FUNCTION IF EXISTS events_append_only()")
