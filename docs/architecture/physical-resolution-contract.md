# Physical Resolution Contract

Status: implemented.

## Purpose

`openai_export_obsidian.physical_resolution` is the layer between typed source references and reporting.

```text
Inventory
  ↓
Reference Extraction
  ↓
Physical Resolution
  ↓
Reporting
```

It receives `ReferenceRecord` plus the already-built `ExportInventory`. It does not parse conversation JSON and does not open ZIP files. Every conclusion is derived from Inventory metadata and a recorded comparison method.

## Candidate scope

The current candidate scope is intentionally explicit:

- every Inventory member marked `is_physical_payload`, including `personal/files/...` and `file-...dat` / `file_...dat`;
- `other_file`, so an unknown physical member remains eligible rather than disappearing from consideration.

The payload rule wins over the family: an Office document can have a ZIP signature and therefore be structurally classified `archive_internal`, while still being the physical exported DOCX/XLSX/PPTX payload.

Archive containers, conversation and metadata JSON, HTML and CSV export records are not payload candidates in this first increment. A `no_candidate_in_inventory` result therefore means *no candidate in this declared scope*, not that the source export universally proves absence.

## Output: `PhysicalResolutionRecord`

`physical_resolutions.jsonl` contains one row per `references.jsonl` row. Important fields are:

| Field | Meaning |
|---|---|
| `reference_id` | Original source occurrence; never overwritten |
| `resolution_status` | Result without collapsing distinct failure modes |
| `reason` | Deterministic explanation of the status |
| `candidates` | Every matching Inventory entry, never only the first basename match |
| `candidates[].match_methods` | Mechanical methods that found this candidate |
| `selected_archive_path` | Present only for `resolved_unique` |
| detected type, MIME, signature, hash | Facts copied from Inventory, not inferred from an extension |

Supported statuses:

```text
resolved_unique
collision
no_candidate_in_inventory
candidate_unreadable
external_non_exportable
inline_payload_not_archive_member
non_physical_reference
inventory_unavailable
indeterminate_inventory_error
```

## Matching methods

The resolver may compare an identifier or name against the Inventory archive path, member path, basename, `file-...` / `file_...` `.dat` convention, and `/mnt/data` path suffix. It records the exact method, for example `file_identifier_dat_basename` or `normalized_identifier_mnt_data_suffix`, on each resulting candidate.

It merges repeated methods for the same `archive_path`, but never removes a distinct candidate because another one is a closer or earlier match.

If the Inventory contains a local read error, a reference without candidates is marked `indeterminate_inventory_error`; the resolver does not claim that its payload is absent.

## Boundary and migration

This layer now supplies the effective physical path and status of every `AssetRecord`. `asset_resolution_comparisons.jsonl` contains one comparison per AssetRecord. A `resolved_unique` candidate is applied only when it belongs to the existing copyable member scope. A `collision` clears the selected path, sets `asset_status: collision`, and prevents copying; unresolved and out-of-scope cases are `missing`. `parse`, `validate`, and `query` keep their current user-facing behavior.

`build_asset_records`, `build_library_origin_asset_records`, and global Library/Knowledge copies no longer choose physical members by basename. Every file selected for copy now comes from a unique `PhysicalResolutionRecord`.
