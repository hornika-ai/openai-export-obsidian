# Readable pack invariants

This contract covers `parse --emit-json` with `readable` or `readable_compact`. The recommended consumer surface is `90_Evidence/`; Markdown and copied payloads are projections controlled by manifest flags.

## Identity and relations

- `conversations.jsonl.conversation_id` is the canonical conversation identity and must be unique.
- `messages.jsonl` contains every recovered mapping message, including non-linear branches. A non-null `message_id` is identified by `(conversation_id, message_id)` and must be unique within that conversation. `message_id` values may repeat across conversations.
- Every message node is identified by `(conversation_id, node_id)`, which must be unique within that conversation. `node_id` values may repeat across conversations and provide the fallback identity when `message_id` is null.
- Every message, asset, source, tool event, textdoc and context row must reference an emitted `conversation_id`. Rows carrying `message_id` must reference a known `(conversation_id, message_id)` pair.
- `ConversationRecord.message_count` is `len(all_messages)` and must equal the number of `messages.jsonl` rows for that conversation. `file_reference_count` equals the count of `asset_links.jsonl` rows for the conversation.

## Ordering and branches

- Conversations are sorted by `(create_time or "", conversation_id)` before filtering.
- Mapping nodes are parsed in sorted node-ID order. The readable transcript can follow `current_node`; JSONL message evidence remains all parsed mapping messages.
- If the main branch cannot be reconstructed, the parser keeps sorted messages and records a parse warning. It does not invent a branch.

## Timestamps, statuses and unknown values

- Numeric source timestamps are normalized by `utils.as_iso`; absent or unparseable values become null. Equality between conversation and message timestamps is allowed.
- The parser preserves a string role or content type rather than whitelisting it. An unfamiliar role/content type is valid evidence, not a rejected record.
- `raw_metadata`, `raw_reference`, non-text content and citation payloads intentionally admit arbitrary JSON: they preserve source evidence. Record-envelope fields are closed in the exported JSON Schemas so a new emitted field is a contract change.

## Files, hashes and validation

- Base readable evidence files are emitted by `emit_readable_outputs`; empty JSONL files are valid and still emitted.
- `00_Home.md` is an optional root-level Obsidian navigation artifact, emitted only with Markdown. It is not an evidence record and its bytes are excluded from the evidence fingerprint and consumer record hashes. Every other root entry remains closed by `pack-layout.json`.
- `40_Views/conversation_note_locators.jsonl` is a derived navigation relation emitted only with Markdown. Each closed record maps one canonical `conversation_id` to the exact POSIX-relative Markdown path emitted by the writer, and the validator checks that its target note's generated frontmatter has the same `conversation_id`. Locator paths are filesystem/navigation paths, not pre-rendered Wikilink syntax; consumers must escape or encode them for their own UI/Markdown syntax. The locator is excluded from the evidence fingerprint and consumer record hashes; `pack_manifest.json.navigation.conversation_note_locator` declares whether it is emitted.
- Files under `20_Files/`, views and Markdown are conditional on manifest flags. `forensic` is a historical layout variant and is excluded from this contract.
- Payload hashes in a real pack are SHA-256 of copied bytes where the relevant writer emits `content_sha256`; ordinary inventory hashes are opt-in. Contract `file_hashes` are SHA-256 of every bundle file other than `contract-manifest.json` (a self-hash would be impossible).
- `validate_pack` rejects missing base evidence, unknown conversation/message links, invalid asset/textdoc statuses, count mismatches, and missing copied payloads. It is intentionally fail-soft for malformed raw source records: such records can remain represented as warnings or empty evidence.

## Compatibility

A consumer must reject an unrecognized `pack_manifest.schema_version`, validate the envelope schemas in this bundle, then apply the inter-file rules above. It should treat unknown fields as incompatible with this contract, even though the current pack validator only explicitly rejects some obsolete fields.
