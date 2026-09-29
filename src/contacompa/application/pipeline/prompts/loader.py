"""Prompts are versioned Markdown files next to this module: `<name>_<version>.md`, with a
`## system` section and a `## user` section. The version is part of the file name so a change is
a new file, never an edit that silently alters eval results."""

import re
from functools import lru_cache
from pathlib import Path

from contacompa.domain.prompt import Prompt

_DIR = Path(__file__).parent
_SECTION_RE = re.compile(r"^## (system|user)\s*$", re.MULTILINE)


@lru_cache
def load_prompt(name: str, version: str) -> Prompt:
    path = _DIR / f"{name}_{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt '{name}' version '{version}' not found at {path.name}")
    text = path.read_text(encoding="utf-8")
    parts = _SECTION_RE.split(text)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"prompt file {path.name} must contain '## system' and '## user' sections")
    return Prompt(name=name, version=version, system=sections["system"], user=sections["user"])
