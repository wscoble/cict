"""Prompt rendering: load system/user refs from config, replace placeholders.

Two syntaxes carry the same variables into a prompt, and both are resolved
here so the agent receives real paths instead of placeholders it must
compose itself:

  {{key}}   the value, shown in a "Variables" block the agent can read
  <key>     the same value, inlined into instructions and JSON examples

Leaving <key> for the agent to substitute is a strong-model assumption that
breaks weak ones: a 7B handed "<context_handoff_dir>/scout_findings.md" writes
to a directory literally named "<context_handoff_dir>". Resolving both forms
here removes that burden. Schema placeholders such as "<one sentence on what
you found>" are not variable names, so they are untouched — only keys present
in `variables` are replaced.
"""

from __future__ import annotations

from pathlib import Path


def render(template_path: str | Path, variables: dict[str, str]) -> str:
    text = Path(template_path).read_text()
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    # Also inline <key> placeholders used in instructions and JSON examples, so
    # the agent gets real paths instead of having to compose them from the
    # Variables block. Only known variable names are touched.
    for key, value in variables.items():
        text = text.replace("<" + key + ">", value)
    return text


def save(directory: str | Path, name: str, content: str) -> Path:
    """Save the exact prompt sent, before execution — the audit copy."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    return path
