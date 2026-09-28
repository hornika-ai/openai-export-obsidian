from __future__ import annotations

"""Physical candidate resolution over canonical inventory entries.

This module consumes typed ``ReferenceRecord`` rows and ``ExportInventory``.
It never parses conversations or opens a ZIP archive.  A resolution records every
candidate found by transparent mechanical comparisons; a physical payload is only
marked as resolved when exactly one readable candidate remains.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable, TYPE_CHECKING

from .inventory import ExportInventory, InventoryEntry
from .reference_extraction import ReferenceRecord

if TYPE_CHECKING:
    from .payload_classification import ContentEquivalenceIndex


RESOLUTION_STATUSES = frozenset(
    {
        "resolved_unique",
        "resolved_equivalent_candidates",
        "collision",
        "no_candidate_in_inventory",
        "candidate_unreadable",
        "external_non_exportable",
        "inline_payload_not_archive_member",
        "non_physical_reference",
        "inventory_unavailable",
        "indeterminate_inventory_error",
    }
)

NON_PHYSICAL_REFERENCE_KINDS = frozenset(
    {
        "generation_identifier",
        "image_generation_identifier",
        "image_send_identifier",
        "image_prompt_identifier",
    }
)


@dataclass
class ResolutionCandidate:
    """One Inventory member that mechanically matched a source reference."""

    archive_path: str
    member_path: str
    archive_chain: list[str]
    family: str
    namespace: str | None
    size: int
    extension: str | None
    detected_extension: str | None
    mime_type: str | None
    signature: str | None
    sha256: str | None
    content_equivalence_group_id: str | None
    inventory_status: str
    match_methods: list[str]
    declared_size_matches: bool | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class PhysicalResolutionRecord:
    """Resolution outcome for exactly one ``ReferenceRecord`` occurrence."""

    resolution_id: str
    reference_id: str
    conversation_id: str | None
    message_id: str | None
    node_id: str | None
    source_shard: str | None
    proof_path: str
    reference_kind: str
    value_kind: str
    raw_identifier: str
    normalized_identifier: str | None
    namespace: str | None
    resolution_status: str
    reason: str
    candidate_count: int
    candidates: list[ResolutionCandidate]
    selected_archive_path: str | None
    inventory_source_archive: str
    inventory_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass
class PhysicalResolutionResult:
    records: list[PhysicalResolutionRecord]

    def summary_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "resolutions": len(self.records),
                "resolved_unique": sum(row.resolution_status == "resolved_unique" for row in self.records),
                "resolved_equivalent_candidates": sum(
                    row.resolution_status == "resolved_equivalent_candidates" for row in self.records
                ),
                "collisions": sum(row.resolution_status == "collision" for row in self.records),
                "no_candidate_in_inventory": sum(
                    row.resolution_status == "no_candidate_in_inventory" for row in self.records
                ),
                "external_non_exportable": sum(
                    row.resolution_status == "external_non_exportable" for row in self.records
                ),
            },
            "resolution_statuses": dict(sorted(Counter(row.resolution_status for row in self.records).items())),
            "match_methods": dict(
                sorted(
                    Counter(
                        method
                        for row in self.records
                        for candidate in row.candidates
                        for method in candidate.match_methods
                    ).items()
                )
            ),
        }


class LibraryResolutionIndex:
    """Resolve global Library/Knowledge copies from their direct file-ID evidence.

    A library row may also contain a filename hint.  The direct file-ID row carries
    that hint into Physical Resolution, so the index uses only that row and never
    allows a separate filename-only row to bypass an ID collision.
    """

    def __init__(self, result: PhysicalResolutionResult):
        self.by_file_id: dict[str, list[PhysicalResolutionRecord]] = defaultdict(list)
        for record in result.records:
            file_id = _library_file_id_for_resolution(record)
            if file_id:
                self.by_file_id[file_id].append(record)

    def selected_archive_path(self, file_id: str) -> str | None:
        records = self.by_file_id.get(file_id, [])
        if not records or any(record.resolution_status == "collision" for record in records):
            return None
        selected_paths = {
            record.selected_archive_path
            for record in records
            if record.resolution_status in {"resolved_unique", "resolved_equivalent_candidates"}
            and record.selected_archive_path
        }
        return next(iter(selected_paths)) if len(selected_paths) == 1 else None


class PhysicalResolver:
    """Resolve typed references to Inventory candidates without silent selection."""

    def __init__(self, inventory: ExportInventory, *, content_equivalence: "ContentEquivalenceIndex | None" = None):
        self.inventory = inventory
        self.content_equivalence = content_equivalence
        self.candidate_entries = sorted(_candidate_entries(inventory.entries), key=lambda entry: entry.archive_path)
        self.by_basename: dict[str, list[InventoryEntry]] = defaultdict(list)
        self.by_member_path: dict[str, list[InventoryEntry]] = defaultdict(list)
        self.by_archive_path: dict[str, list[InventoryEntry]] = defaultdict(list)
        for entry in self.candidate_entries:
            self.by_basename[_casefold(entry.basename)].append(entry)
            self.by_member_path[_casefold(entry.member_path)].append(entry)
            self.by_archive_path[_casefold(entry.archive_path)].append(entry)

    def resolve_all(self, references: Iterable[ReferenceRecord]) -> PhysicalResolutionResult:
        return PhysicalResolutionResult(records=[self.resolve(reference) for reference in references])

    def resolve(self, reference: ReferenceRecord) -> PhysicalResolutionRecord:
        if self.inventory.source_status != "inventoried":
            return self._record(reference, "inventory_unavailable", "inventory source is not readable", {})
        non_physical = _non_physical_status(reference)
        if non_physical:
            status, reason = non_physical
            return self._record(reference, status, reason, {})

        matches: dict[str, tuple[InventoryEntry, set[str]]] = {}
        self._collect_name_matches(matches, reference.raw_identifier, "raw_identifier")
        self._collect_name_matches(matches, reference.normalized_identifier, "normalized_identifier")
        self._collect_name_matches(matches, reference.logical_name, "logical_name")
        self._collect_file_id_matches(matches, reference.normalized_identifier)
        self._collect_path_suffix_matches(matches, reference)

        candidates = self._candidates(matches, reference.declared_size)
        if not candidates:
            if self.inventory.errors:
                return self._record(
                    reference,
                    "indeterminate_inventory_error",
                    "Inventory recorded read errors, so absence of a candidate is not conclusive",
                    matches,
                )
            return self._record(
                reference,
                "no_candidate_in_inventory",
                "no physical candidate matched the reference in the Inventory candidate scope",
                matches,
            )
        if len(candidates) > 1:
            if self.content_equivalence and self.content_equivalence.equivalent_paths(
                candidate.archive_path for candidate in candidates
            ):
                selected = self.content_equivalence.canonical_candidate_path(
                    (candidate.archive_path for candidate in candidates),
                    {path: methods for path, (_entry, methods) in matches.items()},
                )
                return self._record(
                    reference,
                    "resolved_equivalent_candidates",
                    "multiple physical candidates matched, but their SHA-256 payloads are identical; selected deterministic canonical candidate",
                    matches,
                    selected_archive_path=selected,
                )
            return self._record(
                reference,
                "collision",
                "multiple physical candidates matched; no candidate was selected",
                matches,
            )
        candidate = candidates[0]
        if candidate.inventory_status != "inventoried":
            return self._record(
                reference,
                "candidate_unreadable",
                "the sole matching Inventory candidate is not readable",
                matches,
            )
        return self._record(
            reference,
            "resolved_unique",
            "exactly one readable physical candidate matched",
            matches,
            selected_archive_path=candidate.archive_path,
        )

    def _collect_name_matches(
        self,
        matches: dict[str, tuple[InventoryEntry, set[str]]],
        value: str | None,
        label: str,
    ) -> None:
        if not value:
            return
        normalized = value.replace("\\", "/").strip()
        if not normalized:
            return
        if "/" in normalized or "::" in normalized:
            self._add_entries(matches, self.by_archive_path.get(_casefold(normalized), []), f"{label}_archive_path")
            self._add_entries(matches, self.by_member_path.get(_casefold(normalized.lstrip("/")), []), f"{label}_member_path")
        basename = PurePosixPath(normalized).name
        if basename:
            self._add_entries(matches, self.by_basename.get(_casefold(basename), []), f"{label}_basename")

    def _collect_file_id_matches(
        self,
        matches: dict[str, tuple[InventoryEntry, set[str]]],
        normalized_identifier: str | None,
    ) -> None:
        if not _is_file_identifier(normalized_identifier):
            return
        assert normalized_identifier is not None
        self._add_entries(
            matches,
            self.by_basename.get(_casefold(normalized_identifier), []),
            "file_identifier_basename",
        )
        self._add_entries(
            matches,
            self.by_basename.get(_casefold(f"{normalized_identifier}.dat"), []),
            "file_identifier_dat_basename",
        )

    def _collect_path_suffix_matches(
        self,
        matches: dict[str, tuple[InventoryEntry, set[str]]],
        reference: ReferenceRecord,
    ) -> None:
        for value, label in (
            (reference.raw_identifier, "raw_identifier"),
            (reference.normalized_identifier, "normalized_identifier"),
        ):
            if not value or "/mnt/data/" not in value.replace("\\", "/"):
                continue
            suffix = value.replace("\\", "/")
            if suffix.startswith("sandbox:"):
                suffix = suffix[len("sandbox:") :]
            if not suffix.startswith("/"):
                suffix = f"/{suffix}"
            for entry in self.candidate_entries:
                if _casefold(entry.member_path).endswith(_casefold(suffix)):
                    self._add_entries(matches, [entry], f"{label}_mnt_data_suffix")

    @staticmethod
    def _add_entries(
        matches: dict[str, tuple[InventoryEntry, set[str]]],
        entries: Iterable[InventoryEntry],
        method: str,
    ) -> None:
        for entry in entries:
            existing = matches.get(entry.archive_path)
            if existing is None:
                matches[entry.archive_path] = (entry, {method})
            else:
                existing[1].add(method)

    def _candidates(
        self,
        matches: dict[str, tuple[InventoryEntry, set[str]]],
        declared_size: int | None,
    ) -> list[ResolutionCandidate]:
        return [
            ResolutionCandidate(
                archive_path=entry.archive_path,
                member_path=entry.member_path,
                archive_chain=list(entry.archive_chain),
                family=entry.family,
                namespace=entry.namespace,
                size=entry.size,
                extension=entry.extension,
                detected_extension=entry.detected_extension,
                mime_type=entry.mime_type,
                signature=entry.signature,
                sha256=(self.content_equivalence.sha256_for(entry.archive_path) if self.content_equivalence else entry.sha256),
                content_equivalence_group_id=(
                    self.content_equivalence.group_id_for(entry.archive_path) if self.content_equivalence else None
                ),
                inventory_status=entry.status,
                match_methods=sorted(methods),
                declared_size_matches=entry.size == declared_size if declared_size is not None else None,
            )
            for _, (entry, methods) in sorted(matches.items())
        ]

    def _record(
        self,
        reference: ReferenceRecord,
        status: str,
        reason: str,
        matches: dict[str, tuple[InventoryEntry, set[str]]],
        *,
        selected_archive_path: str | None = None,
    ) -> PhysicalResolutionRecord:
        candidates = self._candidates(matches, reference.declared_size)
        return PhysicalResolutionRecord(
            resolution_id=f"resolution:{reference.reference_id}",
            reference_id=reference.reference_id,
            conversation_id=reference.conversation_id,
            message_id=reference.message_id,
            node_id=reference.node_id,
            source_shard=reference.source_shard,
            proof_path=reference.proof_path,
            reference_kind=reference.reference_kind,
            value_kind=reference.value_kind,
            raw_identifier=reference.raw_identifier,
            normalized_identifier=reference.normalized_identifier,
            namespace=reference.namespace,
            resolution_status=status,
            reason=reason,
            candidate_count=len(candidates),
            candidates=candidates,
            selected_archive_path=selected_archive_path,
            inventory_source_archive=self.inventory.source_archive,
            inventory_status=self.inventory.source_status,
        )


def _candidate_entries(entries: Iterable[InventoryEntry]) -> list[InventoryEntry]:
    """Keep all declared physical payloads plus ``other_file`` unknowns.

    Office documents are valid ZIP containers and may therefore have
    ``family == archive_internal`` while still being a physical member under
    ``personal/files``.  ``is_physical_payload`` deliberately outranks that
    structural family classification here.
    """
    return [
        entry
        for entry in entries
        if not entry.is_directory
        and (entry.is_physical_payload or entry.family == "other_file")
    ]


def _library_file_id_for_resolution(record: PhysicalResolutionRecord) -> str | None:
    if "[file_id=" not in record.proof_path:
        return None
    try:
        file_id = record.proof_path.rsplit("[file_id=", 1)[1].split("]", 1)[0]
    except IndexError:
        return None
    if not _is_file_identifier(file_id):
        return None
    if record.raw_identifier == file_id or record.normalized_identifier == file_id:
        return file_id
    return None


def _non_physical_status(reference: ReferenceRecord) -> tuple[str, str] | None:
    if reference.namespace == "external-url" or reference.reference_kind == "external_url":
        return "external_non_exportable", "external URL is not an archive member reference"
    if reference.namespace == "data-uri":
        return "inline_payload_not_archive_member", "data URI is inline source content, not an archive member"
    if reference.namespace == "canmore":
        return "non_physical_reference", "canmore URI is a logical textdoc reference"
    if reference.reference_kind in NON_PHYSICAL_REFERENCE_KINDS:
        return "non_physical_reference", "identifier does not name a physical archive member by itself"
    return None


def _is_file_identifier(value: str | None) -> bool:
    return bool(value and value.startswith(("file-", "file_")))


def _casefold(value: str) -> str:
    return value.casefold()
