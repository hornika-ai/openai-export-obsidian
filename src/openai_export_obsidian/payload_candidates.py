from __future__ import annotations

"""Non-canonical same-name payload diagnostics.

This layer intentionally runs after physical resolution and materialization.  It
does not inspect archive bytes, resolve an asset, or promote a relationship: a
matching logical basename is only a reading aid for a payload whose exact
OpenAI file ID has no physical candidate in the export.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any
import unicodedata
from urllib.parse import unquote

from .models import AssetRecord
from .utils import append_jsonl, write_json


MATCHING_RULE = "url_decoded_casefolded_logical_basename_equality"
STATUS = "unverified_candidate"


@dataclass(frozen=True)
class UnverifiedPayloadCandidateRecord:
    candidate_id: str
    status: str
    canonical: bool
    confidence: str
    matching_rule: str
    non_promotion_reason: str
    asset_ref_id: str
    conversation_id: str
    message_id: str | None
    reference_file_id: str
    reference_logical_basename: str
    reference_normalized_extension: str | None
    reference_proof_path: str
    reference_resolution_statuses: list[str]
    candidate_file_id: str
    candidate_logical_basename: str
    candidate_physical_archive_path: str
    candidate_copied_path: str | None
    reference_declared_size: int | None
    candidate_size: int | None
    candidate_payload_disposition_status: str | None
    candidate_declared_extension: str | None
    candidate_detected_extension: str | None
    candidate_detected_mime: str | None
    type_comparison: str
    candidate_count_for_name: int
    ambiguities: list[str]
    proof_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnverifiedPayloadCandidateResult:
    records: list[UnverifiedPayloadCandidateRecord]
    eligible_reference_count: int
    source_asset_count: int

    def by_asset_ref_id(self) -> dict[str, list[UnverifiedPayloadCandidateRecord]]:
        grouped: dict[str, list[UnverifiedPayloadCandidateRecord]] = defaultdict(list)
        for record in self.records:
            grouped[record.asset_ref_id].append(record)
        return {
            asset_ref_id: sorted(rows, key=lambda row: (row.candidate_file_id, row.candidate_physical_archive_path))
            for asset_ref_id, rows in grouped.items()
        }

    def summary_dict(self) -> dict[str, Any]:
        by_source = {record.asset_ref_id for record in self.records}
        ambiguity_sources = {
            record.asset_ref_id
            for record in self.records
            if record.candidate_count_for_name > 1
        }
        return {
            "counts": {
                "eligible_references": self.eligible_reference_count,
                "references_with_candidates": len(by_source),
                "candidate_rows": len(self.records),
                "ambiguous_references": len(ambiguity_sources),
            },
            "matching_rule_counts": dict(sorted(Counter(record.matching_rule for record in self.records).items())),
            "type_comparison_counts": dict(sorted(Counter(record.type_comparison for record in self.records).items())),
            "contract": {
                "raw_zip_reparsed": False,
                "physical_resolution_modified": False,
                "payload_dispositions_modified": False,
                "payloads_copied": False,
                "canonical_relations_created": False,
                "name_equality_is_not_physical_identity": True,
            },
        }


class UnverifiedPayloadCandidateAnalyzer:
    """Compare unresolved exact file references with materialized homonyms."""

    def construct(
        self,
        assets: list[AssetRecord],
        asset_resolution_comparisons: list[dict[str, Any]],
        file_manifest: list[dict[str, Any]],
    ) -> UnverifiedPayloadCandidateResult:
        comparison_by_asset_ref_id = {
            str(row.get("asset_ref_id")): row
            for row in asset_resolution_comparisons
            if isinstance(row.get("asset_ref_id"), str)
        }
        candidates_by_name = self._payload_candidates_by_name(file_manifest)
        records: list[UnverifiedPayloadCandidateRecord] = []
        eligible_reference_count = 0

        for asset in sorted(assets, key=lambda row: row.asset_ref_id):
            if not self._is_eligible_reference(asset, comparison_by_asset_ref_id.get(asset.asset_ref_id)):
                continue
            eligible_reference_count += 1
            logical_basename = asset.reconstructed_filename or ""
            candidates = [
                row
                for row in candidates_by_name.get(normalized_logical_basename(logical_basename), [])
                if row["candidate_file_id"] != asset.raw_file_id
            ]
            for candidate in candidates:
                records.append(self._record(asset, comparison_by_asset_ref_id[asset.asset_ref_id], candidate, len(candidates)))

        unique = {record.candidate_id: record for record in records}
        return UnverifiedPayloadCandidateResult(
            records=sorted(unique.values(), key=lambda row: row.candidate_id),
            eligible_reference_count=eligible_reference_count,
            source_asset_count=len(assets),
        )

    @staticmethod
    def _is_eligible_reference(asset: AssetRecord, comparison: dict[str, Any] | None) -> bool:
        if asset.asset_status != "missing" or not asset.raw_file_id or not asset.reconstructed_filename:
            return False
        if not comparison or comparison.get("effective_asset_status") != "missing":
            return False
        statuses = comparison.get("matching_resolution_statuses")
        return isinstance(statuses, list) and set(statuses) == {"no_candidate_in_inventory"}

    @staticmethod
    def _payload_candidates_by_name(file_manifest: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen: set[tuple[str, str, str]] = set()
        for row in file_manifest:
            if row.get("materialization_status") != "materialized":
                continue
            file_id = row.get("file_id")
            logical_basename = row.get("logical_basename") or row.get("filename")
            copied_path = row.get("copied_path") or row.get("duplicate_payload_copied_path")
            archive_path = row.get("physical_archive_path") or row.get("archive_path")
            if not all(isinstance(value, str) and value for value in (file_id, logical_basename, archive_path)):
                continue
            key = (file_id, archive_path, copied_path)
            if key in seen:
                continue
            seen.add(key)
            grouped[normalized_logical_basename(logical_basename)].append(
                {
                    "candidate_file_id": file_id,
                    "candidate_logical_basename": logical_basename,
                    "candidate_physical_archive_path": archive_path,
                    "candidate_copied_path": copied_path,
                    "candidate_size": row.get("size"),
                    "candidate_payload_disposition_status": row.get("payload_disposition_status"),
                    "candidate_declared_extension": string_or_none(row.get("declared_extension")),
                    "candidate_detected_extension": string_or_none(row.get("detected_extension")),
                    "candidate_detected_mime": string_or_none(row.get("detected_mime")),
                }
            )
        return {
            name: sorted(rows, key=lambda row: (str(row["candidate_file_id"]), str(row["candidate_physical_archive_path"])))
            for name, rows in grouped.items()
        }

    @staticmethod
    def _record(
        asset: AssetRecord,
        comparison: dict[str, Any],
        candidate: dict[str, str | None],
        candidate_count: int,
    ) -> UnverifiedPayloadCandidateRecord:
        reference_extension = string_or_none(asset.normalized_extension)
        candidate_extension = candidate["candidate_detected_extension"] or candidate["candidate_declared_extension"]
        type_comparison = compare_extensions(reference_extension, candidate_extension)
        seed = "|".join(
            (
                asset.asset_ref_id,
                str(candidate["candidate_file_id"]),
                str(candidate["candidate_physical_archive_path"]),
                MATCHING_RULE,
            )
        )
        proof_paths = sorted(
            {
                asset.proof_path,
                "90_Evidence/asset_resolution_comparisons.jsonl",
                "90_Evidence/file_manifest.jsonl",
            }
        )
        return UnverifiedPayloadCandidateRecord(
            candidate_id="unverified-payload-candidate:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
            status=STATUS,
            canonical=False,
            confidence="low",
            matching_rule=MATCHING_RULE,
            non_promotion_reason="same logical basename does not demonstrate identical OpenAI identity, version, or bytes",
            asset_ref_id=asset.asset_ref_id,
            conversation_id=asset.conversation_id,
            message_id=asset.message_id,
            reference_file_id=asset.raw_file_id or "",
            reference_logical_basename=asset.reconstructed_filename or "",
            reference_normalized_extension=reference_extension,
            reference_proof_path=asset.proof_path,
            reference_resolution_statuses=sorted(str(value) for value in comparison.get("matching_resolution_statuses") or []),
            candidate_file_id=str(candidate["candidate_file_id"]),
            candidate_logical_basename=str(candidate["candidate_logical_basename"]),
            candidate_physical_archive_path=str(candidate["candidate_physical_archive_path"]),
            candidate_copied_path=string_or_none(candidate["candidate_copied_path"]),
            reference_declared_size=asset.size,
            candidate_size=candidate["candidate_size"] if isinstance(candidate["candidate_size"], int) else None,
            candidate_payload_disposition_status=string_or_none(candidate["candidate_payload_disposition_status"]),
            candidate_declared_extension=candidate["candidate_declared_extension"],
            candidate_detected_extension=candidate["candidate_detected_extension"],
            candidate_detected_mime=candidate["candidate_detected_mime"],
            type_comparison=type_comparison,
            candidate_count_for_name=candidate_count,
            ambiguities=["multiple_same_name_payloads"] if candidate_count > 1 else [],
            proof_paths=proof_paths,
        )


def compare_extensions(reference_extension: str | None, candidate_extension: str | None) -> str:
    if reference_extension and candidate_extension:
        return "same_extension" if reference_extension.casefold() == candidate_extension.casefold() else "extension_conflict"
    return "extension_unavailable"


def normalized_logical_basename(value: str) -> str:
    """Match human filenames after URL decoding without asserting file identity."""
    return unicodedata.normalize("NFC", unquote(Path(value).name)).casefold()


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def emit_unverified_payload_candidate_evidence(output_dir: Path, result: UnverifiedPayloadCandidateResult) -> None:
    append_jsonl(output_dir / "unverified_payload_candidates.jsonl", [record.to_dict() for record in result.records])
    write_json(output_dir / "unverified_payload_candidate_summary.json", result.summary_dict())
