from __future__ import annotations

"""Controlled bridge from legacy ``AssetRecord`` rows to Physical Resolution.

Only a unique readable resolution, or a deterministic representative of an
exact-content-equivalence group, may update an AssetRecord. Every other case
retains legacy behaviour for compatibility and is emitted as explicit evidence.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable

from .models import AssetRecord
from .physical_resolution import PhysicalResolutionRecord, PhysicalResolutionResult


@dataclass
class AssetResolutionComparison:
    asset_ref_id: str
    conversation_id: str
    message_id: str | None
    proof_path: str
    raw_file_id: str | None
    legacy_asset_status: str
    legacy_physical_archive_path: str | None
    matching_reference_ids: list[str]
    matching_resolution_ids: list[str]
    matching_resolution_statuses: list[str]
    unique_resolved_archive_paths: list[str]
    migration_status: str
    effective_asset_status: str
    effective_physical_archive_path: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class AssetResolutionMigrationResult:
    comparisons: list[AssetResolutionComparison]

    def summary_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "assets": len(self.comparisons),
                "applied_unique": sum(
                    comparison.migration_status.startswith("applied_unique")
                    or comparison.migration_status == "applied_equivalent_candidates"
                    for comparison in self.comparisons
                ),
                "legacy_fallback": sum(
                    comparison.migration_status.startswith("legacy_fallback")
                    for comparison in self.comparisons
                ),
                "applied_collision": sum(
                    comparison.migration_status == "applied_collision"
                    for comparison in self.comparisons
                ),
            },
            "migration_statuses": dict(
                sorted(Counter(comparison.migration_status for comparison in self.comparisons).items())
            ),
        }


class AssetResolutionMigrator:
    """Apply only safe unique resolutions while preserving comparison evidence."""

    def __init__(self, resolutions: PhysicalResolutionResult, *, copyable_archive_paths: Iterable[str]):
        self.resolutions = sorted(resolutions.records, key=lambda row: row.resolution_id)
        self.copyable_archive_paths = set(copyable_archive_paths)
        self.by_conversation: dict[str, list[PhysicalResolutionRecord]] = defaultdict(list)
        for resolution in self.resolutions:
            if resolution.conversation_id:
                self.by_conversation[resolution.conversation_id].append(resolution)

    def apply(self, assets: Iterable[AssetRecord]) -> AssetResolutionMigrationResult:
        return AssetResolutionMigrationResult(comparisons=[self._apply_one(asset) for asset in assets])

    def _apply_one(self, asset: AssetRecord) -> AssetResolutionComparison:
        legacy_status = asset.asset_status
        legacy_path = asset.physical_archive_path
        matches = [
            resolution
            for resolution in self.by_conversation.get(asset.conversation_id, [])
            if _matches_asset(asset, resolution)
        ]
        selected_paths = sorted(
            {
                resolution.selected_archive_path
                for resolution in matches
                if resolution.resolution_status in {"resolved_unique", "resolved_equivalent_candidates"}
                and resolution.selected_archive_path
            }
        )

        if len(selected_paths) == 1 and selected_paths[0] in self.copyable_archive_paths:
            selected = selected_paths[0]
            asset.physical_archive_path = selected
            asset.asset_status = "found"
            if any(row.resolution_status == "resolved_equivalent_candidates" for row in matches):
                migration_status = "applied_equivalent_candidates"
            elif legacy_path == selected and legacy_status == "found":
                migration_status = "applied_unique_equivalent"
            elif legacy_path:
                migration_status = "applied_unique_replaced_legacy"
            else:
                migration_status = "applied_unique_new"
        elif len(selected_paths) == 1:
            migration_status = "legacy_fallback_unique_outside_copyable_scope"
        elif len(selected_paths) > 1:
            migration_status = "legacy_fallback_conflicting_unique"
        elif any(resolution.resolution_status == "collision" for resolution in matches):
            # A collision is an explicit non-resolution.  Do not retain the
            # path selected by the old first-basename rule.
            asset.asset_status = "collision"
            asset.physical_archive_path = None
            migration_status = "applied_collision"
        elif matches:
            migration_status = "legacy_fallback_nonunique_or_unresolved"
        else:
            migration_status = "legacy_fallback_no_linked_resolution"

        return AssetResolutionComparison(
            asset_ref_id=asset.asset_ref_id,
            conversation_id=asset.conversation_id,
            message_id=asset.message_id,
            proof_path=asset.proof_path,
            raw_file_id=asset.raw_file_id,
            legacy_asset_status=legacy_status,
            legacy_physical_archive_path=legacy_path,
            matching_reference_ids=[resolution.reference_id for resolution in matches],
            matching_resolution_ids=[resolution.resolution_id for resolution in matches],
            matching_resolution_statuses=sorted({resolution.resolution_status for resolution in matches}),
            unique_resolved_archive_paths=selected_paths,
            migration_status=migration_status,
            effective_asset_status=asset.asset_status,
            effective_physical_archive_path=asset.physical_archive_path,
        )


def _matches_asset(asset: AssetRecord, resolution: PhysicalResolutionRecord) -> bool:
    if asset.conversation_id != (resolution.conversation_id or ""):
        return False
    if (
        asset.message_id
        and resolution.message_id
        and asset.message_id != resolution.message_id
        and "library_files" not in asset.proof_path
    ):
        return False
    if not _proof_paths_match(asset.proof_path, resolution.proof_path):
        return False
    primary_identifiers = _asset_primary_identifiers(asset)
    resolution_primary_identifiers = _resolution_primary_identifiers(resolution)
    if primary_identifiers:
        # A concrete file ID outranks a filename hint.  Otherwise a unique name
        # could incorrectly bypass a collision on the file ID itself.
        return bool(primary_identifiers & resolution_primary_identifiers)
    return bool(_asset_identifiers(asset) & _resolution_identifiers(resolution))


def _proof_paths_match(asset_path: str, resolution_path: str) -> bool:
    asset_tail = asset_path.split("::")[-1]
    resolution_tail = resolution_path.split("::")[-1]
    return (
        resolution_tail == asset_tail
        or resolution_tail.startswith(f"{asset_tail}.")
        or resolution_tail.startswith(f"{asset_tail}[")
    )


def _asset_identifiers(asset: AssetRecord) -> set[str]:
    values = {asset.raw_file_id, asset.raw_dat_filename, asset.reconstructed_filename}
    return _identifier_variants(value for value in values if value)


def _asset_primary_identifiers(asset: AssetRecord) -> set[str]:
    values = {asset.raw_file_id, asset.raw_dat_filename}
    return _identifier_variants(value for value in values if value)


def _resolution_identifiers(resolution: PhysicalResolutionRecord) -> set[str]:
    values = {resolution.raw_identifier, resolution.normalized_identifier}
    return _identifier_variants(value for value in values if value)


def _resolution_primary_identifiers(resolution: PhysicalResolutionRecord) -> set[str]:
    values = {resolution.raw_identifier, resolution.normalized_identifier}
    return _identifier_variants(value for value in values if value)


def _identifier_variants(values: Iterable[str]) -> set[str]:
    variants: set[str] = set()
    for value in values:
        normalized = value.replace("\\", "/").strip()
        if not normalized:
            continue
        variants.add(normalized.casefold())
        basename = PurePosixPath(normalized).name.casefold()
        if basename:
            variants.add(basename)
            if basename.endswith(".dat"):
                variants.add(basename[:-4])
    return variants
