# Canonical Export Inventory Contract

Status: implemented.

## Purpose

`openai_export_obsidian.inventory` is the physical source-of-truth layer for one OpenAI/ChatGPT export ZIP. It recursively records every ZIP member encountered, including directories, nested archive members, and members that later stages deliberately do not use.

The inventory has one responsibility only: establish what exists physically and describe it without inferring conversation or asset relationships.

```text
Inventory
  ↓
Reference extraction
  ↓
Physical resolution
  ↓
Reporting and Obsidian rendering
```

## Non-goals

- Do not parse conversations.
- Do not infer a file's conversation, message, user, assistant, or tool origin.
- Do not choose one physical candidate among colliding basenames.
- Do not hide files because a current parser stage does not support them.
- Do not write reporting files itself.

## Core structures

`ExportInventory.scan(path, forensic_hashes=False)` returns an `ExportInventory`.

### `InventoryEntry`

One entry is produced for every member encountered. Its stable fields are:

| Field | Meaning |
|---|---|
| `outer_archive` | Path of the export ZIP supplied by the caller |
| `archive_internal` | Internal ZIP chain, or `null` for a top-level member |
| `archive_chain` | Ordered internal ZIP member names |
| `depth` | ZIP nesting depth; top-level members are depth `0` |
| `member_path` | Path inside its immediate archive |
| `archive_path` | Canonical full path joined with `::` |
| `size`, `compressed_size`, `crc` | Values reported by ZIP metadata |
| `extension` | Declared filename extension, if any |
| `detected_extension`, `mime_type`, `signature` | Byte-based detection; never copied from `extension` |
| `family` | Structural category such as `conversation_shard`, `export_manifest`, `personal_file`, `file_payload`, `html_export`, or `other_file` |
| `namespace` | Observed storage namespace such as `personal/files`, `mnt/data`, `file`, `conversations`, or `files` |
| `sha256` | Optional hash when forensic hashing is requested |
| `status` | `inventoried`, `directory`, `unreadable`, `corrupt_nested_archive`, or `unreadable_nested_archive` |

`extension` and `detected_extension` are intentionally separate. A `.dat` file with a PNG signature remains `extension: dat` and receives `detected_extension: png`.

### `InventoryError`

Errors are local evidence rows. A corrupt nested archive does not cause its parent export inventory to disappear; it produces an entry plus an `InventoryError` with archive path and depth.

### `ExportInventory`

The inventory exposes:

- `entries` for exhaustive member inspection;
- `errors` for fail-soft diagnostics;
- `physical_entries()` for the physical-payload selection rule;
- `basename_collisions()` for future resolution logic;
- `summary_dict()` for deterministic reporting.

## Ownership boundary

### Inventory

Owns ZIP traversal, recursive discovery, ZIP metadata, byte signatures, optional hashes, family classification, namespaces, and local read errors.

### Reference extraction

Consumes conversations and the already-built inventory. It may create typed references, but it must not reopen ZIP files to discover paths or resolve a physical payload. Contract: [Reference Extraction](reference-extraction-contract.md).

### Physical resolution

Consumes references plus inventory candidates. It preserves all candidates, resolution method, and collision state rather than selecting a basename silently. Contract: [Physical Resolution](physical-resolution-contract.md).

### Reporting

Consumes the inventory and later-stage records. The current parser writes:

```text
90_Evidence/inventory.jsonl
90_Evidence/inventory_errors.jsonl
90_Evidence/inventory_summary.json
```

The legacy forensic profile writes the same files at its output root, beside its existing JSONL files.

## Compatibility adapter

`ExportArchive` is now an adapter over `ExportInventory`. Existing calls to:

```python
archive.iter_members()
archive.find_members(...)
archive.read_bytes(member)
archive.copy_member(member, destination)
archive.physical_asset_members()
```

remain available. This preserves `parse`, `validate`, and `query`, while ensuring that discovery itself happens only in Inventory.

New modules must not add independent `zipfile.ZipFile` walks. They should receive `ExportInventory`, `InventoryEntry`, or an `ExportArchive` whose `.inventory` is already built.

## Hash mode

`forensic_hashes=False` is the default because full hashing re-reads every payload. Future forensic commands may construct:

```python
inventory = ExportInventory.scan("files/OpenAI-export.zip", forensic_hashes=True)
```

The parser currently keeps its existing CLI behavior and uses the default non-hashing mode.

## Payload disposition and validation

Normal `parse --emit-json` classifies every physical inventory payload exactly
once in `payload_dispositions.jsonl` and summarizes the result in
`payload_disposition_summary.json`. The implemented statuses are:

- `referenced_payload`;
- `duplicate_of_referenced_payload`;
- `duplicate_of_unreferenced_payload`;
- `unlinked_payload`;
- `unhashable_payload`.

`validate` rejects duplicate physical archive paths, unknown statuses, invalid
content-equivalence group IDs, incomplete disposition evidence, and any mismatch
between the set of physical inventory payloads and the disposition rows.
Materialization validation separately checks that copied outputs use the
canonical archive member selected for their content-equivalence group.
