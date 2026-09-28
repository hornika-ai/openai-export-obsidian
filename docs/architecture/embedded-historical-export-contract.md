# Historical Embedded Export Evidence Contract

Status: implemented.

## Purpose

`embedded_historical_exports` indexes an older, complete OpenAI export found inside
a newer export archive.  It is an **auxiliary evidence layer**: it cannot silently
replace the primary `conversations-*.json` data, create current asset links, or
claim that an old payload is physically present elsewhere in the newer export.

The Inventory layer identifies `conversations.json` as `conversation_monolith`.
The historical ingestor only treats it as an embedded historical snapshot when it
is inside at least one archive member (`archive_chain` is non-empty).

## Inputs and separation

```
Inventory -> primary parser -> EmbeddedHistoricalExportIngestor -> reporting
```

The ingestor receives parsed primary `ConversationRecord` objects plus the canonical
inventory.  It scans matching embedded `conversations.json` arrays sequentially and
compares only identical `conversation_id` values.  No match by title, filename,
basename, date, or message order is permitted.

## Evidence outputs

The readable pack receives the following independent files under `90_Evidence/`:

- `historical_export_snapshots.jsonl` — nested archive provenance and source paths;
- `historical_conversation_comparisons.jsonl` — message/node counts by source;
- `historical_messages.jsonl` — historical nodes, labelled `historical_only` or
  `also_in_primary`;
- `historical_signals.jsonl` — pointer/tool clues with a source JSON path;
- `historical_export_summary.json` — deterministic aggregate counts and warnings.

Every physical occurrence remains in the snapshot inventory.  The ingestor calculates
`content_sha256` from each decompressed monolithic `conversations.json`; only byte-
identical payloads are deduplicated for messages/signals.  Later copies carry
`duplicate_of_snapshot_id`.  This avoids double-counting an export preserved both in
`Conversations` and `Files` while retaining its physical provenance.  ZIP CRC-32 and
size are evidence fields, never the sole duplicate criterion.

`value_summary` is a compact index aid, not a substitute for source data.  The exact
value remains in `historical_conversations_archive_path` at `proof_path`.

Signals include only observed structural clues: `asset_pointer`, `audio_asset_pointer`,
`image_gen`, `dalle`, `canmore`, `textdoc`, `/mnt/data/`, and a `recipient: python`
execution marker.  A signal does not establish asset ownership, generated-output
status, or physical-payload availability; Physical Resolution remains responsible
for those claims.

## Consumer rules

Future consumers must retain `snapshot_id`, `historical_conversations_archive_path`,
and `proof_path`.  They must surface the historical source distinctly and require an
explicit reconciliation policy before promoting any field into a primary record.
