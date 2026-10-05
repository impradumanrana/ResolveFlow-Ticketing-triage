"""The client's own AI provider, behind one contract (C10).

Everything that reaches a model goes through `AIGateway`. It decides, in this
order, whether a call may happen at all and what happens when it fails:

1. **Policy.** The model must be approved by the client for this operation, in
   the configured region. Nothing unapproved is ever called.
2. **Budget.** The worst-case cost is reserved against the provider's monthly
   budget *before* the call, atomically, so concurrent calls cannot overspend.
3. **Credential.** The key is read from Secret Manager at call time. It never
   occupies a column, an environment variable, a log line, or an exception.
4. **Call.** Bounded retries for failures that retrying can fix.
5. **Structure.** Output is validated against a schema, with one repair.
6. **Fallback.** Only for availability failures, only to approved models in the
   same region, never for embeddings, and always recorded.

Every failure is a stable code with a safe message. None carries provider text,
because provider error text can quote part of the key.
"""
