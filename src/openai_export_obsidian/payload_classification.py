from __future__ import annotations

"""Physical content-equivalence and disposition classification.

This module is deliberately upstream of materialization and Markdown rendering.
It establishes only physical facts: where a payload was found, its SHA-256
equivalence group, and whether that group has a demonstrated reference.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

from .archive import ExportArchive
from .inventory import ExportInventory
from .models import ArchiveMember
from .physical_resolution import PhysicalResolutionResult


SELECTED_RESOLUTION_STATUSES = frozenset({"resolved_unique", "resolved_equivalent_candidates"})
DISPOSITION_STATUSES = frozenset(
    {
        "referenced_payload",
        "duplicate_of_referenced_payload",
        "duplicate_of_unreferenced_payload",
        "unlinked_payload",
        "unhashable_payload",
    }
)


@dataclass(frozen=True)
class ContentEquivalenceRecord:
    physical_archive_path: str
    physical_origin: str
    size: int
    content_sha256: str | None
    hash_status: str
    content_equivalence_group_id: str | None
    equivalent_archive_paths: list[str]
    canonical_archive_path: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ContentEquivalenceIndex:
    """Hashes every declared physical payload once and groups exact byte matches."""

    def __init__(self, records: Iterable[ContentEquivalenceRecord]):
        self.records = sorted(records, key=lambda row: row.physical_archive_path)
        self.by_archive_path = {row.physical_archive_path: row for row in self.records}
        self.paths_by_sha256: dict[str, tuple[str, ...]] = {}
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in self.records:
            if row.content_sha256:
                grouped[row.content_sha256].append(row.physical_archive_path)
        for digest, paths in grouped.items():
            self.paths_by_sha256[digest] = tuple(sorted(paths))

    @classmethod
    def build(cls, archive: ExportArchive, inventory: ExportInventory) -> "ContentEquivalenceIndex":
        members = {member.archive_path: member for member in archive.physical_asset_members()}
        hashed: list[tuple[str, str, int, str | None, str]] = []
        for archive_path, member in sorted(members.items()):
            entry = inventory.entry_for_archive_path(archive_path)
            try:
                digest = archive.hash_member(member)
                hash_status = "hashed"
            except (OSError, RuntimeError, ValueError):
                digest = None
                hash_status = "unreadable"
            hashed.append((archive_path, _physical_origin(member, entry.namespace if entry else None), member.size, digest, hash_status))

        paths_by_hash: dict[str, list[str]] = defaultdict(list)
        origins_by_path: dict[str, str] = {}
        for path, origin, _size, digest, _status in hashed:
            origins_by_path[path] = origin
            if digest:
                paths_by_hash[digest].append(path)

        records: list[ContentEquivalenceRecord] = []
        for path, origin, size, digest, hash_status in hashed:
            equivalents = sorted(paths_by_hash.get(digest, [path])) if digest else [path]
            canonical = _canonical_path(equivalents, origins_by_path) if digest else None
            records.append(
                ContentEquivalenceRecord(
                    physical_archive_path=path,
                    physical_origin=origin,
                    size=size,
                    content_sha256=digest,
                    hash_status=hash_status,
                    content_equivalence_group_id=f"sha256:{digest}" if digest else None,
                    equivalent_archive_paths=equivalents,
                    canonical_archive_path=canonical,
                )
            )
        return cls(records)

    def sha256_for(self, archive_path: str) -> str | None:
        row = self.by_archive_path.get(archive_path)
        return row.content_sha256 if row else None

    def group_id_for(self, archive_path: str) -> str | None:
        row = self.by_archive_path.get(archive_path)
        return row.content_equivalence_group_id if row else None

    def equivalent_paths(self, archive_paths: Iterable[str]) -> bool:
        hashes = {self.sha256_for(path) for path in archive_paths}
        return bool(hashes) and None not in hashes and len(hashes) == 1

    def canonical_candidate_path(self, archive_paths: Iterable[str], match_methods_by_path: dict[str, set[str]]) -> str:
        return min(
            archive_paths,
            key=lambda path: (
                _match_priority(match_methods_by_path.get(path, set())),
                _origin_priority(self.by_archive_path[path].physical_origin),
                path,
            ),
        )

    def canonical_path(self, archive_paths: Iterable[str]) -> str | None:
        """Return the deterministic physical representative for known paths."""
        paths = [path for path in archive_paths if path in self.by_archive_path]
        if not paths:
            return None
        return min(
            paths,
            key=lambda path: (_origin_priority(self.by_archive_path[path].physical_origin), path),
        )

    def summary_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "physical_payloads": len(self.records),
                "hashed_payloads": sum(row.hash_status == "hashed" for row in self.records),
                "unhashable_payloads": sum(row.hash_status != "hashed" for row in self.records),
                "equivalence_groups": len(self.paths_by_sha256),
                "duplicate_payloads": sum(len(paths) - 1 for paths in self.paths_by_sha256.values()),
            },
            "physical_origins": dict(sorted(Counter(row.physical_origin for row in self.records).items())),
        }


@dataclass(frozen=True)
class PayloadDispositionRecord:
    physical_archive_path: str
    physical_origin: str
    size: int
    content_sha256: str | None
    content_equivalence_group_id: str | None
    equivalent_archive_paths: list[str]
    canonical_archive_path: str | None
    referenced_archive_paths: list[str]
    disposition_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class PayloadDispositionResult:
    records: list[PayloadDispositionRecord]

    def __post_init__(self) -> None:
        self.records.sort(key=lambda row: row.physical_archive_path)
        self.by_archive_path = {row.physical_archive_path: row for row in self.records}

    def disposition_for(self, archive_path: str) -> PayloadDispositionRecord | None:
        return self.by_archive_path.get(archive_path)

    def is_unlinked_candidate(self, archive_path: str) -> bool:
        row = self.disposition_for(archive_path)
        return row is not None and row.disposition_status in {"unlinked_payload", "duplicate_of_unreferenced_payload", "unhashable_payload"}

    def summary_dict(self) -> dict[str, object]:
        return {"disposition_statuses": dict(sorted(Counter(row.disposition_status for row in self.records).items()))}


class PayloadDispositionClassifier:
    """Classify payload disposition after canonical reference resolution."""

    def __init__(self, equivalence: ContentEquivalenceIndex):
        self.equivalence = equivalence

    def classify(
        self,
        resolutions: PhysicalResolutionResult,
        *,
        direct_referenced_archive_paths: Iterable[str] = (),
    ) -> PayloadDispositionResult:
        referenced = {
            row.selected_archive_path
            for row in resolutions.records
            if row.resolution_status in SELECTED_RESOLUTION_STATUSES and row.selected_archive_path
        }
        referenced.update(direct_referenced_archive_paths)
        records: list[PayloadDispositionRecord] = []
        for row in self.equivalence.records:
            equivalent = row.equivalent_archive_paths
            group_referenced = sorted(path for path in equivalent if path in referenced)
            if row.hash_status != "hashed":
                status = "unhashable_payload"
            elif row.physical_archive_path in referenced:
                status = "referenced_payload"
            elif group_referenced:
                status = "duplicate_of_referenced_payload"
            elif len(equivalent) > 1:
                status = "duplicate_of_unreferenced_payload"
            else:
                status = "unlinked_payload"
            # Once a group has a demonstrated reference, its canonical copy must
            # be chosen among those referenced paths.  This avoids promoting an
            # unrelated personal/runtime duplicate merely because it sorts first.
            canonical = self.equivalence.canonical_path(group_referenced) if group_referenced else row.canonical_archive_path
            records.append(
                PayloadDispositionRecord(
                    physical_archive_path=row.physical_archive_path,
                    physical_origin=row.physical_origin,
                    size=row.size,
                    content_sha256=row.content_sha256,
                    content_equivalence_group_id=row.content_equivalence_group_id,
                    equivalent_archive_paths=list(equivalent),
                    canonical_archive_path=canonical,
                    referenced_archive_paths=group_referenced,
                    disposition_status=status,
                )
            )
        return PayloadDispositionResult(records)


def _physical_origin(member: ArchiveMember, namespace: str | None) -> str:
    path = (member.inner_name or member.name).replace("\\", "/")
    if namespace == "personal/files" or path.startswith("personal/files/"):
        return "runtime" if "/mnt/data/" in path else "personal_files"
    if member.basename.startswith(("file-", "file_")) and member.basename.endswith(".dat"):
        return "conversation_payload"
    return "other_physical_payload"


def _origin_priority(origin: str) -> int:
    return {"conversation_payload": 0, "personal_files": 1, "runtime": 2}.get(origin, 3)


def _canonical_path(paths: Iterable[str], origins_by_path: dict[str, str]) -> str:
    return min(paths, key=lambda path: (_origin_priority(origins_by_path[path]), path))


def _match_priority(methods: set[str]) -> int:
    if any(method.startswith("file_identifier_") for method in methods):
        return 0
    if any(method.endswith("_archive_path") or method.endswith("_member_path") for method in methods):
        return 1
    if any(method.startswith("raw_identifier") or method.startswith("normalized_identifier") for method in methods):
        return 2
    return 3
