from __future__ import annotations

"""Canonical, recursive inventory of an OpenAI export archive.

This module is deliberately limited to discovering archive members and describing
what is physically present. It does not extract conversation references, resolve
assets, or write reports. Those later stages consume ``ExportInventory``.
"""

import hashlib
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Callable

SIGNATURE_SAMPLE_SIZE = 8 * 1024
HASH_CHUNK_SIZE = 1024 * 1024


@dataclass
class InventoryError:
    code: str
    detail: str
    archive_internal: str | None = None
    member_path: str | None = None
    depth: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class InventoryEntry:
    """One member encountered while walking the source archive recursively."""

    outer_archive: str
    archive_chain: tuple[str, ...]
    depth: int
    member_path: str
    size: int
    compressed_size: int
    crc: int | None
    is_directory: bool
    extension: str | None
    detected_extension: str | None
    family: str
    namespace: str | None
    mime_type: str | None
    signature: str | None
    sha256: str | None
    status: str

    @property
    def archive_internal(self) -> str | None:
        return "::".join(self.archive_chain) if self.archive_chain else None

    @property
    def archive_path(self) -> str:
        if self.archive_chain:
            return "::".join((*self.archive_chain, self.member_path))
        return self.member_path

    @property
    def basename(self) -> str:
        return PurePosixPath(self.member_path.replace("\\", "/")).name

    @property
    def is_physical_payload(self) -> bool:
        path = self.member_path.replace("\\", "/")
        return not self.is_directory and (
            path.startswith("personal/files/")
            or (self.extension == "dat" and self.basename.startswith(("file-", "file_")))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "outer_archive": self.outer_archive,
            "archive_internal": self.archive_internal,
            "archive_chain": list(self.archive_chain),
            "depth": self.depth,
            "member_path": self.member_path,
            "archive_path": self.archive_path,
            "basename": self.basename,
            "size": self.size,
            "compressed_size": self.compressed_size,
            "crc": self.crc,
            "is_directory": self.is_directory,
            "extension": self.extension,
            "detected_extension": self.detected_extension,
            "family": self.family,
            "namespace": self.namespace,
            "mime_type": self.mime_type,
            "signature": self.signature,
            "sha256": self.sha256,
            "status": self.status,
            "is_physical_payload": self.is_physical_payload,
        }


@dataclass
class ExportInventory:
    """The canonical physical inventory of one export archive.

    ``entries`` include directories and members at every discoverable ZIP depth.
    ``errors`` preserve failures locally instead of silently dropping a member.
    """

    source_archive: str
    source_status: str
    forensic_hashes: bool
    source_sha256: str | None
    entries: list[InventoryEntry]
    errors: list[InventoryError]

    @classmethod
    def scan(cls, source_archive: str, *, forensic_hashes: bool = False) -> "ExportInventory":
        inventory = cls(
            source_archive=source_archive,
            source_status="inventoried",
            forensic_hashes=forensic_hashes,
            source_sha256=None,
            entries=[],
            errors=[],
        )
        if forensic_hashes:
            try:
                with open(source_archive, "rb") as handle:
                    inventory.source_sha256 = _hash_stream(handle)
            except OSError as exc:
                inventory.source_status = "unreadable"
                inventory.errors.append(InventoryError("source_read_error", str(exc)))
                return inventory
        try:
            with zipfile.ZipFile(source_archive) as archive:
                inventory._scan_zip(archive, archive_chain=())
        except FileNotFoundError as exc:
            inventory.source_status = "missing"
            inventory.errors.append(InventoryError("source_missing", str(exc)))
        except zipfile.BadZipFile as exc:
            inventory.source_status = "corrupt_archive"
            inventory.errors.append(InventoryError("source_corrupt_archive", str(exc)))
        except OSError as exc:
            inventory.source_status = "unreadable"
            inventory.errors.append(InventoryError("source_read_error", str(exc)))
        return inventory

    def find_entries(self, predicate: Callable[[InventoryEntry], bool]) -> list[InventoryEntry]:
        return [entry for entry in self.entries if predicate(entry)]

    def physical_entries(self) -> list[InventoryEntry]:
        return [entry for entry in self.entries if entry.is_physical_payload]

    def entry_for_archive_path(self, archive_path: str) -> InventoryEntry | None:
        return next((entry for entry in self.entries if entry.archive_path == archive_path), None)

    def basename_collisions(self) -> dict[str, list[InventoryEntry]]:
        by_basename: dict[str, list[InventoryEntry]] = defaultdict(list)
        for entry in self.entries:
            if not entry.is_directory:
                by_basename[entry.basename].append(entry)
        return {
            basename: entries
            for basename, entries in sorted(by_basename.items())
            if len(entries) > 1
        }

    def summary_dict(self) -> dict[str, object]:
        files = [entry for entry in self.entries if not entry.is_directory]
        collisions = self.basename_collisions()
        return {
            "source_archive": self.source_archive,
            "source_status": self.source_status,
            "forensic_hashes": self.forensic_hashes,
            "source_sha256": self.source_sha256,
            "counts": {
                "entries": len(self.entries),
                "files": len(files),
                "directories": len(self.entries) - len(files),
                "physical_payloads": sum(1 for entry in files if entry.is_physical_payload),
                "errors": len(self.errors),
                "max_depth": max((entry.depth for entry in self.entries), default=0),
                "basename_collision_groups": len(collisions),
            },
            "families": dict(sorted(Counter(entry.family for entry in self.entries).items())),
            "namespaces": dict(sorted(Counter(entry.namespace or "none" for entry in self.entries).items())),
            "extensions": dict(sorted(Counter(entry.extension or "none" for entry in files).items())),
            "detected_extensions": dict(
                sorted(Counter(entry.detected_extension or "unknown" for entry in files).items())
            ),
            "statuses": dict(sorted(Counter(entry.status for entry in self.entries).items())),
            "errors": [error.to_dict() for error in self.errors],
            "basename_collisions": {
                basename: [entry.archive_path for entry in entries]
                for basename, entries in collisions.items()
            },
        }

    def _scan_zip(self, archive: zipfile.ZipFile, *, archive_chain: tuple[str, ...]) -> None:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            entry = self._build_entry(archive, info, archive_chain)
            self.entries.append(entry)
            if entry.is_directory or not _looks_like_archive(entry):
                continue
            self._scan_nested_archive(archive, info, entry)

    def _build_entry(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        archive_chain: tuple[str, ...],
    ) -> InventoryEntry:
        member_path = info.filename.replace("\\", "/")
        extension = _extension(member_path)
        is_directory = info.is_dir()
        prefix = b""
        sha256 = None
        status = "directory" if is_directory else "inventoried"
        if not is_directory:
            try:
                with archive.open(info) as handle:
                    prefix = handle.read(SIGNATURE_SAMPLE_SIZE)
                if self.forensic_hashes:
                    with archive.open(info) as handle:
                        sha256 = _hash_stream(handle)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                status = "unreadable"
                self.errors.append(
                    InventoryError(
                        "member_read_error",
                        str(exc),
                        archive_internal="::".join(archive_chain) or None,
                        member_path=member_path,
                        depth=len(archive_chain),
                    )
                )
        detected_extension, mime_type, signature = detect_content(prefix)
        family = classify_family(member_path, extension, detected_extension, is_directory)
        return InventoryEntry(
            outer_archive=self.source_archive,
            archive_chain=archive_chain,
            depth=len(archive_chain),
            member_path=member_path,
            size=info.file_size,
            compressed_size=info.compress_size,
            crc=info.CRC,
            is_directory=is_directory,
            extension=extension,
            detected_extension=detected_extension,
            family=family,
            namespace=classify_namespace(member_path, archive_chain),
            mime_type=mime_type,
            signature=signature,
            sha256=sha256,
            status=status,
        )

    def _scan_nested_archive(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        entry: InventoryEntry,
    ) -> None:
        try:
            with archive.open(info) as nested_handle:
                with zipfile.ZipFile(nested_handle) as nested_archive:
                    self._scan_zip(nested_archive, archive_chain=(*entry.archive_chain, entry.member_path))
        except zipfile.BadZipFile as exc:
            entry.status = "corrupt_nested_archive"
            self.errors.append(
                InventoryError(
                    "nested_corrupt_archive",
                    str(exc),
                    archive_internal=entry.archive_internal,
                    member_path=entry.member_path,
                    depth=entry.depth,
                )
            )
        except (OSError, RuntimeError) as exc:
            entry.status = "unreadable_nested_archive"
            self.errors.append(
                InventoryError(
                    "nested_archive_read_error",
                    str(exc),
                    archive_internal=entry.archive_internal,
                    member_path=entry.member_path,
                    depth=entry.depth,
                )
            )


def detect_content(prefix: bytes) -> tuple[str | None, str | None, str | None]:
    """Return detected extension, MIME type and signature from file bytes only."""
    if not prefix:
        return None, None, None
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png", "png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg", "jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif", "gif"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "webp", "image/webp", "riff-webp"
    if prefix.startswith(b"%PDF-"):
        return "pdf", "application/pdf", "pdf"
    if prefix.startswith(b"PK\x03\x04") or prefix.startswith(b"PK\x05\x06") or prefix.startswith(b"PK\x07\x08"):
        return "zip", "application/zip", "zip"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE":
        return "wav", "audio/wav", "riff-wave"
    if prefix.startswith(b"ID3"):
        return "mp3", "audio/mpeg", "id3"
    if prefix.startswith(b"fLaC"):
        return "flac", "audio/flac", "flac"
    if prefix.startswith(b"OggS"):
        return "ogg", "application/ogg", "ogg"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "mp4", "video/mp4", "iso-base-media"
    return _detect_text(prefix)


def _detect_text(prefix: bytes) -> tuple[str | None, str | None, str | None]:
    if b"\x00" in prefix:
        return None, "application/octet-stream", None
    try:
        text = prefix.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "application/octet-stream", None
    stripped = text.lstrip()
    lowered = stripped.lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return "html", "text/html", "html-text"
    if stripped.startswith(("{", "[")):
        return "json", "application/json", "json-text"
    if "\n" in text and any(delimiter in text.splitlines()[0] for delimiter in (",", ";", "\t")):
        return "csv", "text/csv", "delimited-text"
    if text:
        return "txt", "text/plain", "utf8-text"
    return None, "application/octet-stream", None


def classify_family(
    member_path: str,
    extension: str | None,
    detected_extension: str | None,
    is_directory: bool,
) -> str:
    normalized = member_path.replace("\\", "/")
    base = PurePosixPath(normalized).name.casefold()
    if is_directory:
        return "directory"
    if extension == "zip" or detected_extension == "zip":
        return "archive_internal"
    if base.startswith("conversations-") and base.endswith(".json"):
        return "conversation_shard"
    if base == "conversations.json":
        return "conversation_monolith"
    if base == "conversation_asset_file_names.json":
        return "conversation_asset_filename_map"
    if base in {"library_files.json", "libraryfiles.json"}:
        return "library_files"
    if base in {"shared_conversations.json", "sharedconversations.json"}:
        return "shared_conversations"
    if base == "export_manifest.json":
        return "export_manifest"
    if base == "message_feedback.json":
        return "message_feedback"
    if base == "user.json":
        return "user_profile"
    if base == "user_settings.json":
        return "user_settings"
    if base in {"chat.html", "report.html"}:
        return "html_export"
    if normalized.startswith("personal/files/"):
        return "personal_file"
    if extension == "dat" and base.startswith(("file-", "file_")):
        return "file_payload"
    if detected_extension == "json" or extension == "json":
        return "json"
    if detected_extension == "html" or extension in {"html", "htm"}:
        return "html"
    if detected_extension == "csv" or extension == "csv":
        return "csv"
    return "other_file"


def classify_namespace(member_path: str, archive_chain: tuple[str, ...]) -> str | None:
    normalized = member_path.replace("\\", "/").casefold()
    archive_label = "::".join(archive_chain).casefold()
    if "/mnt/data/" in f"/{normalized}":
        return "mnt/data"
    if normalized.startswith("personal/files/"):
        return "personal/files"
    if PurePosixPath(normalized).name.startswith(("file-", "file_")):
        return "file"
    if "conversations__" in archive_label:
        return "conversations"
    if "files__" in archive_label:
        return "files"
    if "ads" in archive_label or normalized.startswith("personal/ads/"):
        return "ads"
    return None


def _looks_like_archive(entry: InventoryEntry) -> bool:
    return entry.extension == "zip" or entry.detected_extension == "zip"


def _extension(member_path: str) -> str | None:
    suffix = PurePosixPath(member_path.replace("\\", "/")).suffix.lower().lstrip(".")
    return suffix or None


def _hash_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(HASH_CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()
