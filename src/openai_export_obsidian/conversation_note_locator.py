"""Producer-owned navigation locator for readable conversation notes.

The locator is deliberately a projection: it records the exact relative path
selected by the writer when it emits a Markdown note, without turning that path
into canonical source evidence.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .utils import append_jsonl


CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH = "40_Views/conversation_note_locators.jsonl"
CONVERSATION_NOTES_ROOT = "10_Conversations"
LOCATOR_RECORD_FIELDS = frozenset({"conversation_id", "relative_note_path"})


def locator_manifest_entry(*, emit_markdown: bool, conversation_count: int) -> dict[str, Any]:
    """Describe locator availability without embedding locator content or hashes."""
    return {
        "state": "emitted" if emit_markdown else "not_emitted",
        "relative_path": CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH if emit_markdown else None,
        "record_count": conversation_count if emit_markdown else 0,
    }


def write_conversation_note_locators(output_path: Path, records: list[dict[str, str]]) -> None:
    """Write records captured from actual note-emission paths in stable order."""
    append_jsonl(
        output_path / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH,
        sorted(records, key=lambda row: row["conversation_id"]),
    )


def locator_path_error(value: object) -> str | None:
    """Return a machine-readable reason when a locator path is unsafe."""
    if not isinstance(value, str) or not value:
        return "relative_note_path must be a non-empty string"
    if "\\" in value:
        return "relative_note_path must use POSIX separators"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return "relative_note_path contains a control character"
    path = PurePosixPath(value)
    if path.is_absolute():
        return "relative_note_path must not be absolute"
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return "relative_note_path contains an invalid path segment"
    if parts[0] != CONVERSATION_NOTES_ROOT:
        return f"relative_note_path must be rooted below {CONVERSATION_NOTES_ROOT}"
    if path.suffix != ".md":
        return "relative_note_path must end with .md"
    return None
