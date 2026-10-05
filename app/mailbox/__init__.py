"""Gmail mailbox connection, credential custody, and permissions (C06).

Read-only first. A mailbox is connected through an explicit OAuth flow started
by an authorized person, for one named address, with the smallest scope set
that lets the product read and watch it. The resulting refresh token is
encrypted before it touches the database and is bound to that one mailbox, so a
token row copied onto another mailbox fails to decrypt rather than silently
reading the wrong inbox.

Nothing in this package can send, draft, label, or modify mail. Those scopes
are refused at connection time and a token carrying them is revoked, not
stored.
"""
