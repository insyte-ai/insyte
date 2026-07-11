"""Natural-language layer.

Turns a free-form question into a structured analysis intent using the user's *own* local AI
CLI (Claude Code / Codex) as a translator only. The model sees metric and dimension *names*
and the question — never data, never credentials. Insyte still executes every resulting
query through the same validated, read-only, audited pipeline.
"""
