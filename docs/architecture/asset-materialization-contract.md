# Asset Materialization Contract

Status: implemented.

## Purpose

Asset materialization creates the readable copy under `20_Files/` without
altering the payload. It is downstream of Inventory and Physical Resolution:
the physical archive path, original filename, signature, and evidence remain
available even when the readable copy receives a logical name or extension.

```text
Inventory + Physical Resolution + explicit naming evidence
  ↓
Materialized file identity
  ↓
20_Files/ readable copy + 90_Evidence/file_manifest.jsonl
```

## Naming and type priority

The writer chooses a readable filename and extension in this order:

1. exact OpenAI filename mapping from `conversation_asset_file_names.json`;
2. an explicit logical filename/path from a typed source reference;
3. byte signature reported by Inventory;
4. declared MIME type;
5. extension explicitly present in the reference;
6. original physical filename, including the `.dat` fallback.

The result is descriptive only. No conversion, decompression, recompression,
content rewrite, or change of payload hash is allowed.

`safe_filename_preserving_suffix` sanitizes a readable basename while keeping
the resolved extension. A name collision receives a deterministic suffix and
`materialization_status: collision_renamed`; it does not fall back to an
unrelated `.dat` name.

## Evidence and statuses

`file_manifest.jsonl` is the bridge between the readable output and forensic
facts. Relevant fields include:

- `physical_archive_path`, `physical_name`, and `content_sha256`;
- `logical_path`, `logical_basename`, and `declared_extension`;
- `detected_extension`, `detected_mime`, and `type_status`;
- `output_filename`, `copied_path`, `naming_basis`, and
  `materialization_status`;
- `duplicate_payload_of` and `duplicate_payload_copied_path` where applicable.

`materialization_status: fallback_dat` means no supported logical name or type
was demonstrated. It is an explicit unknown-binary state, not a conversion
failure. A logical extension that conflicts with the byte signature is kept as
an evidence-visible type conflict; the writer does not silently reinterpret
the payload.

## Duplicate payloads

Readable output is deduplicated by payload identity. When two physical export
members have the same payload, one copy is retained in `20_Files/`; the other
physical member remains represented in `file_manifest.jsonl` through
`duplicate_payload_of`. Its ZIP path and hash are never discarded.

## Folder and relation rule

Folder placement remains relation-based:

- `Attachments/`, `Knowledge/`, `Generated/`, and `Runtime/` require their
  explicit relation evidence;
- `Unlinked/` is used only when no conversation/context relation is
  demonstrated and `--copy-unlinked-assets` is enabled.

Changing a filename or extension does not create, remove, or upgrade a
conversation relation.
