"""Explicit persistence for the local pipeline console's settings."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from dotenv import dotenv_values

KEYS = {
    "WHITESPACE_CONTACT",
    "WHITESPACE_UI_EXTRACTOR",
    "WHITESPACE_UI_LIMIT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_RPM",
    "AZURE_OPENAI_TPM",
    "AZURE_OPENAI_BATCH_SIZE",
}
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def read_settings(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {
        k: v
        for k, v in dotenv_values(path, interpolate=False).items()
        if k in KEYS and v is not None
    }


def _update_env(text: str, updates: dict[str, str]) -> str:
    if not updates.keys() <= KEYS:
        raise ValueError("Unsupported setting")
    for value in updates.values():
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Settings cannot contain control characters or newlines")

    newline = "\r\n" if "\r\n" in text else "\n"

    def assignment(key: str, comment: str = "") -> str:
        value = updates[key].replace("\\", "\\\\").replace("'", "\\'")
        return f"{key}='{value}'{comment}{newline}"

    def comment_of(line: str, start: int) -> str:
        quote = None
        escaped = False
        for i in range(start, len(line)):
            char = line[i]
            if escaped:
                escaped = False
            elif char == "\\" and quote:
                escaped = True
            elif char in "'\"":
                quote = None if char == quote else char if quote is None else quote
            elif char == "#" and quote is None and (i == start or line[i - 1].isspace()):
                return "  " + line[i:].rstrip("\r\n")
        return ""

    seen = set()
    lines = []
    for line in text.splitlines(keepends=True):
        match = ASSIGNMENT.match(line)
        key = match.group(1) if match else None
        if key in updates:
            lines.append(assignment(key, comment_of(line, match.end())))
            seen.add(key)
        else:
            lines.append(line)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    lines.extend(assignment(key) for key in updates if key not in seen)
    return "".join(lines)


def _update_appids(text: str, appids: list[int]) -> str:
    root = yaml.compose(text)

    def lookup(node, key):
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("Invalid sources configuration")
        matches = [value for k, value in node.value if k.value == key]
        if len(matches) != 1:
            raise ValueError(f"Missing or duplicate {key} in sources configuration")
        return matches[0]

    node = lookup(lookup(lookup(root, "sources"), "steam_reviews"), "appids")
    if not isinstance(node, yaml.SequenceNode) or not node.flow_style:
        raise ValueError("Expected appids as an inline YAML list; edit the configuration manually")
    new_text = text[: node.start_mark.index] + str(appids) + text[node.end_mark.index :]
    yaml.safe_load(new_text)
    return new_text


def save_settings(
    env_path: Path, sources_path: Path, appids: list[int], updates: dict[str, str]
) -> None:
    """Update selected settings only; never create or replace the mounted files."""
    with env_path.open(encoding="utf-8", newline="") as file:
        env_text = file.read()
    with sources_path.open(encoding="utf-8", newline="") as file:
        source_text = file.read()
    new_env = _update_env(env_text, updates)
    new_sources = _update_appids(source_text, appids)
    # Bind-mounted .env cannot be atomically replaced. Report partial updates if a write fails.
    with sources_path.open("w", encoding="utf-8", newline="") as file:
        file.write(new_sources)
    try:
        with env_path.open("w", encoding="utf-8", newline="") as file:
            file.write(new_env)
    except OSError as exc:
        raise OSError("Game IDs were saved, but .env could not be written") from exc
