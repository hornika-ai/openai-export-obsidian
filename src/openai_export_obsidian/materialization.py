from __future__ import annotations

"""Resolve readable payload filenames before a pack writer copies bytes.

This module deliberately does not decide a payload's relationship to a
conversation or context.  Callers choose the destination directory; this module
only records the source facts and derives a safe output filename.
"""

from dataclasses import asdict, dataclass
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

from .inventory import InventoryEntry
from .models import ArchiveMember
from .utils import extension_from_name, filename_key, safe_filename_preserving_suffix


@dataclass(frozen=True)
class MaterializationHint:
    """A name, MIME or extension explicitly carried by a source reference."""

    logical_path: str | None = None
    declared_mime: str | None = None
    reference_extension: str | None = None
    source: str = "reference"


@dataclass
class MaterializedFile:
    physical_name: str
    physical_archive_path: str
    logical_path: str | None
    logical_basename: str | None
    declared_extension: str | None
    detected_extension: str | None
    detected_mime: str | None
    output_filename: str
    naming_basis: str
    identity_status: str
    type_status: str
    materialization_status: str
    collision_key: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_materialized_file(
    *,
    physical_member: ArchiveMember,
    inventory_entry: InventoryEntry | None,
    filename_map: Mapping[str, str],
    reference_hints: Iterable[MaterializationHint] = (),
) -> MaterializedFile:
    """Resolve a final readable name without reading or transforming payload bytes.

    Mapping keys are matched only against the exact physical basename, preserving
    the distinction between ``file-`` and ``file_`` identifiers.
    """
    hints = tuple(reference_hints)
    mapped_path = filename_map.get(physical_member.basename)
    named_hint = next((hint for hint in hints if _logical_basename(hint.logical_path)), None)
    mime_hint = next((hint for hint in hints if hint.declared_mime), None)
    extension_hint = next((hint for hint in hints if hint.reference_extension), None)

    detected_extension = inventory_entry.detected_extension if inventory_entry else None
    detected_mime = inventory_entry.mime_type if inventory_entry else None

    if mapped_path:
        logical_path = mapped_path
        logical_basename = _logical_basename(mapped_path)
        naming_basis = "openai_filename_map"
        identity_status = "mapped_by_openai"
    elif named_hint:
        logical_path = named_hint.logical_path
        logical_basename = _logical_basename(named_hint.logical_path)
        naming_basis = named_hint.source
        identity_status = "identified_by_reference"
    else:
        logical_path = None
        logical_basename = None
        naming_basis = "physical_fallback"
        identity_status = "unknown"

    declared_extension = extension_from_name(logical_basename)
    resolved_extension = declared_extension
    if not resolved_extension and detected_extension:
        resolved_extension = detected_extension
        naming_basis = f"{naming_basis}+signature" if logical_basename else "signature"
        if not logical_basename:
            identity_status = "identified_by_signature"
    if not resolved_extension:
        mime_extension = _extension_for_mime(mime_hint.declared_mime if mime_hint else None)
        if mime_extension:
            resolved_extension = mime_extension
            naming_basis = "declared_mime"
            if not logical_basename:
                identity_status = "identified_by_reference"
    if not resolved_extension and extension_hint and extension_hint.reference_extension:
        resolved_extension = extension_hint.reference_extension.lstrip(".").lower() or None
        if resolved_extension:
            naming_basis = "reference_extension"
            if not logical_basename:
                identity_status = "identified_by_reference"

    output_filename = _output_filename(
        logical_basename=logical_basename,
        physical_name=physical_member.basename,
        extension=resolved_extension,
    )
    type_status = _type_status(declared_extension, detected_extension)
    materialization_status = "fallback_dat" if identity_status == "unknown" and output_filename.lower().endswith(".dat") else "materialized"
    return MaterializedFile(
        physical_name=physical_member.basename,
        physical_archive_path=physical_member.archive_path,
        logical_path=logical_path,
        logical_basename=logical_basename,
        declared_extension=declared_extension,
        detected_extension=detected_extension,
        detected_mime=detected_mime,
        output_filename=output_filename,
        naming_basis=naming_basis,
        identity_status=identity_status,
        type_status=type_status,
        materialization_status=materialization_status,
        collision_key=filename_key(output_filename),
    )


def mark_collision(materialized: MaterializedFile, output_filename: str) -> MaterializedFile:
    if output_filename == materialized.output_filename:
        return materialized
    materialized.output_filename = output_filename
    materialized.collision_key = filename_key(output_filename)
    materialized.materialization_status = "collision_renamed"
    return materialized


def _logical_basename(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("\\", "/")
    return PurePosixPath(normalized).name or None


def _output_filename(*, logical_basename: str | None, physical_name: str, extension: str | None) -> str:
    if logical_basename:
        clean = safe_filename_preserving_suffix(logical_basename)
        if extension and not extension_from_name(clean):
            return safe_filename_preserving_suffix(f"{clean}.{extension}")
        return clean
    physical = safe_filename_preserving_suffix(physical_name)
    if not extension:
        return physical
    stem = Path(physical).stem if Path(physical).suffix else physical
    return safe_filename_preserving_suffix(f"{stem}.{extension}")


def _extension_for_mime(value: str | None) -> str | None:
    if not value or value == "application/octet-stream":
        return None
    overrides = {
        "audio/wav": "wav",
        "image/jpeg": "jpg",
        "text/markdown": "md",
    }
    if value in overrides:
        return overrides[value]
    suffix = mimetypes.guess_extension(value, strict=False)
    return suffix.lstrip(".") if suffix else None


def _type_status(declared_extension: str | None, detected_extension: str | None) -> str:
    if not declared_extension:
        return "identified_by_signature" if detected_extension else "unknown"
    if not detected_extension:
        return "signature_unchecked"
    if _extensions_are_compatible(declared_extension, detected_extension):
        return "confirmed"
    return "type_conflict"


def _extensions_are_compatible(declared: str, detected: str) -> bool:
    if declared == detected or {declared, detected} == {"jpg", "jpeg"}:
        return True
    if declared in {"docx", "xlsx"} and detected == "zip":
        return True
    return declared in {"csv", "json", "md", "py", "rtf", "svg", "txt"} and detected in {"csv", "json", "txt"}
