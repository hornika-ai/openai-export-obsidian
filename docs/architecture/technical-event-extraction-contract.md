# Technical Event Extraction Contract

Status: implemented.

## Scope

`technical_events` indexes explicit technical observations from primary 2026 message
JSON, embedded historical monolithic `conversations.json`, and selected `chat.html`
fallback nodes.  It is an evidence layer between message parsing and any future
provenance graph.

It does **not** call Physical Resolution, create `Generation` / `Textdoc` / `Gizmo`
entities, resolve payload files, or merge historical messages into primary messages.

## Records

`TechnicalEventRecord.technical_event_id` is a deterministic pipeline identifier
derived from source provenance and proof path.  It is not an OpenAI identifier.

Every event carries its source export kind, archive path, proof path, structural
message context, observed recipient and selected explicit fields.  The status is
always `observed_no_physical_resolution`: a technical event cannot establish a
physical-payload relation by itself.

Observed families currently include DALL·E and image metadata, Canmore calls,
Python/container execution, known tool recipients, asset pointers, textdoc clues,
`/mnt/data` references, gizmo context, and `unknown_recipient`.

## Source and comparison policy

- `primary_2026_json` and `historical_embedded_json` remain separate source kinds.
- Byte-identical historical monoliths are deduplicated by SHA-256 for extraction;
  their physical copies remain visible through the historical-export evidence layer.
- `chat_html_auxiliary` emits Python fallback events only if the corresponding
  JSON-derived Python node was not observed.
- Comparisons use only the exact tuple: `conversation_id`, `node_id`, `message_id`,
  event family, and operation.  No comparison by title, date, filename, folder or
  sequence is allowed.
- A differing explicit value is reported as `contradiction`; no source wins.

## Outputs

Under `90_Evidence/`:

- `technical_events.jsonl` — all normalized observations;
- `technical_event_comparisons.jsonl` — exact-key source comparisons;
- `technical_event_unknowns.jsonl` — the subset with an unknown recipient;
- `technical_event_summary.json` — reproducible counts and explicit no-resolution
  contract.

## Standalone repair or migration command

Normal readable `parse --emit-json` emits Technical Event evidence. The
standalone command below scans primary shards one at a time and exists only for
a prepared disposable pack copy where the four outputs listed above are absent.
Its JSONL emitters are append-only: running it after a normal parse or repeating
it against the same outputs duplicates rows and can invalidate the pack. It
changes neither the message parser nor the physical-resolution contract.

```bash
PYTHONPATH=src python3 -m openai_export_obsidian technical-events \
  --input files/OpenAI-export.zip \
  --pack /path/to/complete-pack
```

The command obtains its selected conversation IDs from
`90_Evidence/conversations.jsonl`, emits the four technical-event evidence files,
and records elapsed time plus process peak RSS in the summary.  Event and comparison
JSONL remain deterministic; runtime metrics are intentionally not byte-stable.
