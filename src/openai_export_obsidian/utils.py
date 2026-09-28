from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


URL_RE = re.compile(r"https?://[^\s<>()]+")
CODE_BLOCK_RE = re.compile(r"```(?P<lang>[A-Za-z0-9_+.-]*)\n(?P<body>.*?)```", re.DOTALL)


def as_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 32_503_680_000:
            numeric = numeric / 1000
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    if isinstance(value, str):
        return value
    return None


def date_prefix(value: Any) -> str:
    iso = as_iso(value)
    if not iso:
        return "unknown-date"
    return iso[:10]


def year_month(value: Any) -> tuple[str, str, str]:
    iso = as_iso(value)
    if not iso or len(iso) < 7:
        return "unknown-year", "unknown-month", "unknown-month"
    year = iso[:4]
    month = iso[5:7]
    return year, month, f"{year}-{month}"


def json_dumps(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").replace("\x85", "\\u0085")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json_dumps(row) + "\n" for row in rows), encoding="utf-8")


def basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def strip_dat_suffix(value: str | None) -> str | None:
    if not value:
        return None
    return value[:-4] if value.endswith(".dat") else value


def short_identifier(value: str | None, length: int = 14) -> str:
    if not value:
        return "unknown"
    return value[:length]


def normalize_file_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("file-service://"):
        value = value[len("file-service://") :]
    if value.startswith("sediment://"):
        value = value[len("sediment://") :]
    if value.endswith(".dat"):
        value = value[:-4]
    return value if value.startswith(("file-", "file_")) else value


def dat_name_for_file_id(value: str | None) -> str | None:
    if not value:
        return None
    return value if value.endswith(".dat") else f"{value}.dat"


def extension_from_name(name: str | None) -> str | None:
    if not name:
        return None
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix or None


def safe_filename(name: str | None, fallback: str = "unnamed") -> str:
    raw = _clean_filename(name, fallback)
    return raw[:180] or fallback


def safe_filename_preserving_suffix(name: str | None, fallback: str = "unnamed", *, max_length: int = 180) -> str:
    """Return a filesystem-safe filename without truncating its final suffix."""
    raw = _clean_filename(name, fallback)
    suffix = Path(raw).suffix
    if suffix and len(suffix) < max_length:
        stem = raw[: -len(suffix)]
        return f"{stem[: max_length - len(suffix)]}{suffix}" or fallback
    return raw[:max_length] or fallback


def _clean_filename(name: str | None, fallback: str) -> str:
    raw = (name or fallback).replace("\\", "_").replace("/", "_").strip()
    raw = unicodedata.normalize("NFC", raw)
    raw = re.sub(r"[\x00-\x1f:]+", "_", raw)
    return re.sub(r"\s+", " ", raw).strip(" .")


def slugify(value: str | None, fallback: str = "untitled") -> str:
    value = value or fallback
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return (value or fallback)[:70]


def unique_filename(desired: str, used: set[str]) -> str:
    desired = safe_filename(desired)
    stem = Path(desired).stem or "asset"
    suffix = Path(desired).suffix
    candidate = desired
    i = 2
    used_keys = {filename_key(name) for name in used}
    while filename_key(candidate) in used_keys:
        candidate = f"{stem}--{i}{suffix}"
        i += 1
    used.add(filename_key(candidate))
    return candidate


def unique_note_name(desired: str, used: set[str]) -> str:
    desired = safe_filename(desired)
    stem = Path(desired).stem or "note"
    suffix = Path(desired).suffix or ".md"
    candidate = f"{stem}{suffix}"
    i = 2
    used_keys = {filename_key(name) for name in used}
    while filename_key(candidate) in used_keys:
        candidate = f"{stem} ({i}){suffix}"
        i += 1
    used.add(filename_key(candidate))
    return candidate


def filename_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def extract_urls(text: str) -> list[str]:
    return sorted(set(match.group(0).rstrip(".,)") for match in URL_RE.finditer(text or "")))


def extract_code_blocks(text: str) -> list[dict[str, str]]:
    blocks = []
    for index, match in enumerate(CODE_BLOCK_RE.finditer(text or "")):
        blocks.append({"index": index, "language": match.group("lang") or "", "text": match.group("body")})
    return blocks


def compact_raw(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: compact_raw(v) for k, v in value.items() if v not in (None, [], {})}
    if isinstance(value, list):
        return [compact_raw(v) for v in value]
    return value
