"""Cross-cutting security controls (C13).

Each module here exists because a control has to be in exactly one place to be
verifiable: a redaction rule applied in three files is three rules, and the one
that was forgotten is the one that leaks.
"""
