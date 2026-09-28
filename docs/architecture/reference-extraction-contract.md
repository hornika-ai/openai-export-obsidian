# Reference Extraction Contract

Status: implemented.

## Purpose

`openai_export_obsidian.reference_extraction` is the layer between canonical Inventory and the current, separate Physical Resolution layer.

```text
Inventory
  ↓
Reference Extraction
  ↓
Physical Resolution
  ↓
Reporting
```

It receives the raw conversation structures and an already-built `ExportInventory`. It normalizes source references; it does not inspect ZIP members or select physical files.

## Inputs

- `ExportInventory`, already scanned by Inventory;
- normalized `ConversationRecord`, only for stable conversation/message context;
- raw conversation mapping, for exact proof paths and un-compacted metadata;
- `library_files.json` rows already loaded by the existing parser.

No function in this module opens a ZIP archive or imports the asset resolver.

## Output: `ReferenceRecord`

Each row in `references.jsonl` has:

| Field | Meaning |
|---|---|
| `reference_id` | Deterministic occurrence identifier scoped by extraction origin |
| `scope` | `conversation` or `library_metadata` |
| `reference_kind` | Typed form, for example `attachment_file_identifier`, `image_asset_pointer`, `realtime_audio_asset_pointer`, `original_file_identifier`, or `external_url` |
| `value_kind` | `identifier`, `path`, `url`, `data_uri`, `uri`, or `name_hint` |
| `conversation_id`, `message_id`, `node_id` | Source relation when exported |
| `source_shard`, `proof_path`, `metadata_key` | Reproducible source location |
| `raw_identifier` | Unchanged source identifier or path |
| `normalized_identifier` | Mechanical normalization only; never a physical match |
| `namespace` | `file-service`, `sediment`, `sandbox`, `mnt/data`, `data-uri`, `canmore`, `external-url`, `file`, or `null` |
| `logical_name`, `declared_mime_type`, `declared_size` | Source hints, not detected physical facts |
| `inventory_source_archive`, `inventory_status` | Inventory instance on which the extraction was performed |

The output intentionally excludes:

```text
physical_archive_path
candidate_archive_paths
resolution_method
resolution_status
asset_status
```

`validate` rejects these fields in `references.jsonl`, preserving the boundary with Physical Resolution.

## Covered source forms

The extractor currently emits typed rows for:

- attachment IDs and names;
- top-level `image_asset_pointer` and `audio_asset_pointer` values;
- nested `real_time_user_audio_video_asset_pointer` audio, video, and frame pointers;
- `file_id`, `original_file_id`, `mask_file_id` and file-like `id` values;
- DALL-E/generation identifiers such as `original_gen_id`, `gen_id`, and `image_gen_generation_id`;
- image send and prompt identifiers;
- `canmore://` URIs;
- `file-service://`, `sediment://`, `sandbox:/mnt/data`, `/mnt/data`, bare `file-...` / `file_...` identifiers, data URIs, and HTTP(S) URLs present in strings;
- `library_files.json` file and generation metadata.

The recursive walker preserves the proof path of each occurrence. It only removes an exact duplicate detected at the same proof path with the same raw value.

## Consumer rules

### Physical Resolution

`PhysicalResolver` now receives `ReferenceRecord` plus `ExportInventory`. It retains every candidate, records each comparison method, distinguishes unique payloads from collisions and non-exportable values, and never overwrites `raw_identifier` or `normalized_identifier`. Contract: [Physical Resolution](physical-resolution-contract.md).

### Reporting

The current runner writes:

```text
90_Evidence/references.jsonl
90_Evidence/reference_summary.json
90_Evidence/physical_resolutions.jsonl
90_Evidence/physical_resolution_summary.json
```

The forensic profile writes them at its output root. The current bridge is:

```text
ReferenceRecord
  -> PhysicalResolver
  -> AssetResolutionMigrator
  -> effective AssetRecord fields
  -> asset_links.jsonl
```

Only a unique copyable resolution, or a deterministic representative of an
exact-content-equivalence group, may update an `AssetRecord`. A collision clears
the selected physical path and remains explicit. `asset_resolution_comparisons.jsonl`
records one migration decision per asset reference.

## Deliberate limitations

Reference Extraction does not decide whether a generic string is semantically a file reference, a generated payload, or an external URL that policy/reporting should hide. It records the source occurrence and type; Physical Resolution makes only the inventory-candidate decision.
