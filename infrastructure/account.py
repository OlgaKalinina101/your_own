"""There is one account, and it is this one.

The string ``"default"`` appeared 35 times across the backend, and reading the
code it looked like a placeholder — the kind of default that is waiting for
multi-tenancy to arrive. It is not. This is a personal AI: one installation, one
person, one account, permanently. A second account means someone forked the
repository and stood up their own copy, with their own ``data/`` directory and
their own database.

That is worth writing down rather than leaving to be inferred, because the two
readings lead to opposite work. Read as a placeholder, ``account_id`` is a hole
to be filled in with authentication, per-account tokens and row-level
filtering. Read as an invariant, it is a column that keeps the door open and
costs nothing, and the security model is one installation behind one token.

So the parameter stays — the schema and the ``data/autonomy/{account}/`` layout
already use it, and removing it would be a migration for no gain — but its value
comes from here.
"""
from __future__ import annotations

# The one account. Not a default among several; the only one there is.
ACCOUNT_ID = "default"


def resolve(account_id: str | None = None) -> str:
    """Normalise an incoming account id.

    Requests carry one because the API has always accepted it. Anything absent
    or empty is this installation's account, which is also the only one it has.
    """
    return account_id or ACCOUNT_ID
