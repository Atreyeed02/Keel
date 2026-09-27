"""
The shape version of each event payload.

Every `account.created` and `transaction.posted` payload written now carries
`"schema_version"`. Events written before the field existed have none, and
replay reads them as version 1, which is what they are: the shape has not
changed incompatibly since the log began.

A version number changes only for a change an existing reader could not
handle: a field renamed, removed, or given a new meaning. Adding an
optional field is not one. `transaction.posted` entries gained `position`
that way, and remain version 1, because replay already knows what an entry
without a position means (ARCHITECTURE.md §3.3).

Replay refuses a version it does not know rather than guessing. A new
version means a new replay rule, added here and in `app.domain.rebuild`
together.

Imports nothing, so the writers and the replay can all use it.
"""

# What new payloads are written as.
CURRENT_VERSION = {"account.created": 1, "transaction.posted": 1}

# What replay can read. The first incompatible change adds a version here
# and a branch in app.domain.rebuild; the old version stays readable.
SUPPORTED_VERSIONS = {"account.created": {1}, "transaction.posted": {1}}

# How replay reads a payload that has no "schema_version".
UNVERSIONED = 1
