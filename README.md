# OpenAI Export for Obsidian

OpenAI Export for Obsidian turns an official ChatGPT/OpenAI export ZIP into a
navigable Obsidian archive and a machine-readable evidence pack.

The parser runs locally. It does not upload the export or call an external API.
It treats the ZIP as the source of truth, preserves explicit evidence paths,
and keeps missing or unresolved assets visible instead of hiding them.

It produces:

- readable Markdown conversation notes;
- linked or copied exported files when their identity is proven;
- Obsidian navigation views and context hubs;
- deterministic JSONL evidence for validation and downstream tools;
- explicit warnings when a source or payload cannot be resolved safely.

This is an evidence-preserving technical archive, not a legal-forensics tool.
Real exports can contain sensitive personal data and must never be committed to
this repository. Public tests use synthetic fixtures only.

## Project status and future evolution

The current release checkpoint emits and validates the public
`openai-obsidian-pack-v1.4` contract. Its producer behavior and CPF contract
compatibility are covered by the vendored public contract and synthetic tests.
The current private-validation level and the remaining downstream review and
approval gates are tracked in the [roadmap](docs/ROADMAP.md); private evidence
is not a public release fixture.

The repository has three independent version axes:

| Axis | Current value | Meaning |
|---|---|---|
| Python package | `0.1.0` | Installable project version from `pyproject.toml` |
| Pack schema | `openai-obsidian-pack-v1.4` | Producer/consumer schema emitted in pack evidence |
| Contract bundle | `1.0.2` | Version of the vendored public contract description |

Historical milestone labels are not pack-schema versions. Operational commands
and validation documents use the profile and reconstruction behavior names
instead.

Planned work is documented without presenting it as implemented:

- [Roadmap](docs/ROADMAP.md)
- [GitHub publication and CPF compatibility pipeline](docs/PUBLICATION_PIPELINE.md)
- [Incremental update contract](docs/architecture/incremental-update-contract.md)
- [Faithful reconstruction contract](docs/architecture/faithful-reconstruction-contract.md)
- [Parse-run summary contract](docs/architecture/parse-run-summary-contract.md)
- [Historical DALL·E evidence contract](docs/architecture/historical-dalle-evidence-contract.md)
- [Reconstruction pack validation](docs/validation/reconstruction-pack-validation.md)

## What It Parses

- Top-level OpenAI export zips.
- A canonical recursive inventory of nested export zips, including the real `User Online Activity/Conversations__...part-000*.zip` and `Files__...zip` shape.
- Any recursively discovered `conversations-*.json` shard; the observed
  `conversations-000.json` through `conversations-020.json` range is not a
  parser limit.
- `conversation_asset_file_names.json`.
- `library_files.json` and `libraryfiles.json`.
- `shared_conversations.json` and `sharedconversations.json`.
- Physical `file-*.dat`, `file_*.dat`, and `personal/files/...` payloads.

## Install

No third-party runtime dependencies are required.

Use Python 3.11 or newer:

```bash
python3 --version
```

For local development, install the package in editable mode:

```bash
python3 -m pip install -e .
```

After that, use the console command:

```bash
openai-export-obsidian --help
```

Without installing, prefix commands with `PYTHONPATH=src` and use `python3 -m openai_export_obsidian`.

## Usage

Recommended full parse:

```bash
openai-export-obsidian parse \
  --input files/OpenAI-export.zip \
  --output output \
  --copy-assets \
  --emit-json \
  --emit-markdown \
  --markdown-profile readable_compact \
  --emit-bases \
  --emit-dataview
```

By default, `--copy-assets` copies only files linked to emitted conversations or contexts. To also copy unlinked physical payloads into `20_Files/Unlinked/`, opt in explicitly:

```bash
openai-export-obsidian parse \
  --input files/OpenAI-export.zip \
  --output output_with_unlinked \
  --copy-assets \
  --copy-unlinked-assets \
  --emit-json \
  --emit-markdown
```

The default Markdown profile is `readable_compact`, the recommended conversation-first projection. It emits the Obsidian-ready pack and declares the stable schema version `openai-obsidian-pack-v1.4` in conversation frontmatter, conversation evidence rows, and the pack manifest so downstream vault queries and validation scripts can distinguish compatible output shapes. Pass `--markdown-profile readable` explicitly when the fuller inspection projection is needed.

### Markdown profiles

- `readable` is the complete inspection projection with the reconstructed main transcript and message-level proof callouts.
- `readable_compact` is the default conversation-first projection. It keeps the same canonical JSONL, physical resolution, copied payloads, IDs, note paths, and pack schema as `readable`. Its YAML frontmatter is a fixed query surface; source identity, raw/resolved/unresolved counts, and explicit context links are shown separately without duplicating frontmatter fields. Its `Attached files · N` counts distinct visible file identities rather than raw reference rows or equivalent physical copies.
- `forensic` retains the original folder-per-conversation comparison shape.

`readable_compact` projects supported exported citation markers as deterministic
native footnotes. A proven local filename is preferred inside the definition;
the original marker remains canonical in `citation_links.jsonl`. Repeated
citations to one explicit URL or file source share one definition, while
unresolved citations state their status and proof path without inventing a URL.
Every displayed file, source, context, tool, retrieval, or textdoc evidence line
links to its footnote. Attached files use ordinary wikilinks followed by
`• *details*[^id]`; neither callouts nor footnote definitions embed payload
content with `![[...]]`. Detail rows use four-space continuation indentation so
they remain inside the native Obsidian footnote. Composite OpenAI citation
glyphs emit one footnote call per explicitly resolved source.

Compact conversation notes keep ordinary callouts open; warning callouts are
collapsed and contain only the essential status plus their footnote link. Their
full proven detail, including an available original-conversation URL, remains
in the native footnote. `space_projects` is a frontmatter list; the summary
presents `Chat URL:` as a human-readable line. Identity/source properties open
`## References`, and the technical-empty omission count is reported only in
`## Evidence register`.

When an exported citation, source, or retrieval row contains `snippet`, its
native footnote includes a separate `Snippet :` line. Whitespace is normalized,
identical snippets are deduplicated per definition, and the Markdown projection
is deterministically bounded to 280 characters. A truncated projection ends in
`...`; JSONL retains the full value.
The structured source of truth remains `90_Evidence/*.jsonl`. Contract:
[Citation Linking](docs/architecture/citation-linking-contract.md).

### Faithful reconstruction

The readable Markdown profile reconstructs the main exported conversation path and adds lightweight message-level callouts for proof events such as branches, sources, tool/retrieval rows, file references, and export gaps. Repetitive technical-only JSON records stay in evidence JSONL instead of cluttering the transcript. This is a reconstruction layer: the parser does not summarize, cluster, score, or semantically interpret conversations. Canonical evidence remains in `90_Evidence/*.jsonl`.

Legacy forensic Markdown is still available:

```bash
openai-export-obsidian parse \
  --input files/OpenAI-export.zip \
  --output forensic_output \
  --copy-assets \
  --emit-json \
  --emit-markdown \
  --markdown-profile forensic
```

Dry run:

```bash
openai-export-obsidian parse \
  --input files/OpenAI-export.zip \
  --output output_dry_run \
  --copy-assets \
  --emit-json \
  --emit-markdown \
  --dry-run
```

Generate one targeted sample conversation using a public placeholder ID:

```bash
openai-export-obsidian parse \
  --input files/OpenAI-export.zip \
  --output sample_output \
  --conversation-id <conversation-id> \
  --copy-assets \
  --emit-json \
  --emit-markdown \
  --emit-bases \
  --emit-dataview
```

Limit output size for inspection:

```bash
openai-export-obsidian parse \
  --input files/OpenAI-export.zip \
  --output sample_output_small \
  --max-conversations 5 \
  --emit-json \
  --emit-markdown \
  --emit-bases \
  --emit-dataview
```

## Output Layout

The exact closed root and evidence-file layout is defined by
[`pack-layout.json`](contracts/parser-pack/openai-obsidian-pack-v1.4/pack-layout.json).
At a high level, a readable pack contains:

```text
output/
  00_Home.md
  10_Conversations/YYYY/MM/*.md
  20_Files/{Knowledge,Attachments,Generated,Textdocs,Unlinked}/
  30_Contexts/{GPTs,Projects,Knowledge,Observed}/
  40_Views/
  90_Evidence/
  logs/                 # readable_compact and forensic
    parse.log
  _logs/                # readable
    parse.log
```

The `forensic` profile keeps the original folder-per-conversation output shape for audit/comparison.

## Canonical Inventory

Every parse first builds a recursive, fail-soft inventory of the source ZIP before extracting references or resolving physical files. The inventory is the canonical description of what is physically present; it includes members that the current parser does not otherwise consume, such as HTML, account CSVs, `export_manifest.json`, feedback metadata, files without extensions, and nested ZIPs.

Each `inventory.jsonl` row records the source archive, internal archive chain, depth, full archive path, size, compressed size, CRC, declared extension, byte-detected extension, family, namespace, detected MIME type, signature, optional SHA-256, and status. `inventory_errors.jsonl` preserves unreadable or corrupt archive members; `inventory_summary.json` contains deterministic family, extension, depth, status, and basename-collision counts.

The normal `parse` command does not need a new option. It emits the inventory whenever JSON output is requested. Hashes are intentionally opt-in through the Python Inventory API so ordinary parses do not re-read every payload solely for hashing.

Architecture and consumer contract: [Inventory contract](docs/architecture/inventory-contract.md).

## Embedded Historical Exports

An export can itself contain an earlier complete OpenAI export.  When an embedded
archive has a monolithic `conversations.json`, the parser records it as auxiliary
historical evidence rather than silently merging it into the current
`conversations-*.json` shards.  It compares only exact `conversation_id` values and
emits snapshot provenance, historical-only nodes, and observed technical signals in
`historical_export_*.jsonl`, `historical_messages.jsonl`, and
`historical_signals.jsonl`.

This supports review of older Python nodes, `/mnt/data` paths, pointers, DALL·E,
image-generation, Canmore, and textdoc clues while preserving the distinction between
an older source record and a payload found in the current export.  Byte-identical
monolithic sources are deduplicated only after SHA-256 verification; both physical
copies remain visible as snapshots.  Contract:
[Embedded Historical Export Evidence](docs/architecture/embedded-historical-export-contract.md).

## Technical Event Extraction

`Technical Event Extraction` is an additive evidence layer for source-separated
technical observations: tool recipients, image operations, Canmore/textdoc clues,
asset pointers, image-edit identifiers, `/mnt/data` references, and unknown tool
recipients. It records the exact message source and compares primary/historical
events only by explicit conversation, node, message, family, and operation keys.

It does not create generations, textdocs, gizmos, candidate edges, or physical file
relations. Contract: [Technical Event Extraction](docs/architecture/technical-event-extraction-contract.md).

Normal readable `parse --emit-json` runs emit this layer as part of the complete
downstream evidence chain. The standalone command is a repair or migration tool
only. Run it on a disposable pack copy where its JSONL outputs are absent; its
emitters are append-only, so it must not be run after a normal parse or repeated
against the same outputs.

```bash
PYTHONPATH=src python3 -m openai_export_obsidian technical-events \
  --input files/OpenAI-export.zip \
  --pack /path/to/complete-pack
```

## Logical Entity Construction

`logical_entities.jsonl` is a conservative layer derived only from explicit
identifier values in `technical_events.jsonl`. Its taxonomy separates remote
asset pointers, original and mask file references, library-file references,
conversation-scoped `/mnt/data` literals, original-generation references,
Textdoc identifiers/handles/embedded Canmore URIs, and Gizmo versus project
contexts. It does not resolve a physical payload, infer a relation from names or
folders, construct candidate edges, or merge primary and historical messages.
Contract:
[Logical Entity Construction](docs/architecture/logical-entity-construction-contract.md).

Normal readable `parse --emit-json` also emits this layer. The standalone
command is only for a prepared disposable copy that has Technical Event evidence
but none of the Logical Entity JSONL outputs. Do not run it after a normal parse
or repeat it against the same outputs because its emitters are append-only.

```bash
PYTHONPATH=src python3 -m openai_export_obsidian logical-entities \
  --pack /path/to/complete-pack
```

## Reference Extraction

After Inventory, the parser emits `references.jsonl`: typed source references found in raw conversation content, message metadata, inline namespaces, and Library metadata. Rows preserve the original identifier, a mechanical normalization when possible, namespace, proof path, message/node context, declared name/MIME/size, and the inventory source identity.

This layer does not contain `physical_archive_path`, candidate files, `found`/`missing`, or any other physical-resolution decision. Those belong to the current, separate Physical Resolution layer. Contract: [Reference Extraction](docs/architecture/reference-extraction-contract.md).

## Physical Resolution

`physical_resolutions.jsonl` consumes typed references and the canonical Inventory. It records every candidate member and the mechanical comparison that produced it; it never silently chooses the first file sharing a basename. Only a sole readable candidate receives `resolved_unique`; collisions, no candidate in the declared Inventory scope, external URLs, inline data URIs, logical identifiers, and unreadable candidates remain distinct statuses.

`asset_resolution_comparisons.jsonl` records the direct Physical Resolution decision used by `asset_links.jsonl`. A unique candidate in the existing copyable scope becomes the effective asset path. A collision becomes the explicit `collision` asset status with no selected path or copy; unresolved cases remain `missing`. Contract: [Physical Resolution](docs/architecture/physical-resolution-contract.md).

`unverified_payload_candidates.jsonl` is a separate, non-canonical diagnostic for an exact missing `file_id` that shares a logical basename with one or more payloads materialized under other IDs. It preserves every ambiguous homonym, never changes the missing status, and never substitutes a local link for the absent ID. Contract: [Unverified Payload Candidates](docs/architecture/unverified-payload-candidates-contract.md).

## Evidence Rules

The parser distinguishes these states:

- `explicit`: present directly in source JSON metadata.
- `reconstructed`: derived mechanically from explicit source data, such as `conversation_asset_file_names.json`.
- `found`: physically present in the archive.
- `missing`: referenced by a conversation but not physically present under a matching archive path/name.
- `unknown`: not provable from the export.

Asset records keep proof paths such as:

```text
mapping.<node_id>.message.metadata.attachments[0]
mapping.<node_id>.message.content.parts[0].asset_pointer
```

The readable Obsidian notes stay concise. Detailed proof rows live in `90_Evidence/*.jsonl`, joined by `conversation_id`, `message_id`, `file_id`, and context IDs.
`pack_manifest.json` records the pack schema version, generation timestamp, source input path, emission flags, active filters, and parsed/emitted conversation counts for the output run.
`parse_audit.json` is intentionally summary-level. Full orphan rows live in `unlinked_assets.jsonl`; full unresolved file references live in `asset_links.jsonl`.

`messages.jsonl`, `message_sources.jsonl`, `context_links.jsonl`, `tool_events.jsonl`, and `textdocs.jsonl` are forensic evidence layers and include all message nodes recovered from the conversation mapping, including non-linear branches. The Markdown transcript remains the reconstructed main branch for readability.

`citation_links.jsonl` records every supported inline citation marker encountered in readable output, its message and node context, source family, resolution status, source metadata, and proof path. The compact Markdown profile uses these rows only as a readable navigation projection; it does not replace the source evidence.

`textdocs.jsonl` indexes canvas/textdoc evidence found in the export. When `metadata.canvas.user_created_textdocs[]` contains a text payload, the raw content is materialized under `20_Files/Textdocs/YYYY/MM/` without added frontmatter. Reference-only canvas views and `canmore://...` URIs are indexed without inventing a file.

Tool-use evidence is written to `90_Evidence/tool_events.jsonl` only when explicitly present in the export. Code blocks, `commentary` channels, and loose `safe_urls` are not promoted to tool events by themselves.

## What Is Provable

The parser can prove:

- Which shard a conversation came from.
- The raw conversation ID, title, timestamps, and available conversation metadata.
- The full node/message index and a mechanically reconstructed main branch.
- Message roles, message IDs, parent/child links, timestamps, status, channel, model slug metadata, and text content when present.
- Attachment references that appear in message metadata.
- Asset pointer references that appear in multimodal content parts.
- Whether a referenced asset has a matching physical payload in the archive.
- Which copied file came from which original `.dat` or `personal/files/...` archive member.
- Which physical files were not linked to the emitted conversations.

## What Is Not Assumed

The parser does not invent:

- Project names.
- Custom GPT names.
- Provenance for assets when the export does not expose it.
- User/assistant origin for assets without an explicit message role.
- A canonical system prompt when one is not directly present.
- Completeness claims beyond the parsed export files.
- Process/project semantics when they are not explicit or cautiously marked observed.

For custom GPT URLs, the parser only reconstructs `https://chatgpt.com/g/<id>` when an explicit `g-...` ID is present.

## Obsidian Pack Data Model

Conversation notes are the main queryable objects for Bases and Dataview. Frontmatter is kept as a compact query surface; forensic exactness stays in `90_Evidence/*.jsonl`.

```yaml
type: openai_conversation
schema_version: openai-obsidian-pack-v1.4
status: parsed
reviewed: false
title: URL Analysis Request
created_at: 2024-08-29T23:54
updated_at: 2024-08-30T02:27
conversation_id: ...
message_count: 28
source_evidence_count: 12
tool_evidence_count: 3
default_model: gpt-4o
gpts:
  - "[[GPT - g-BpkfnxCrA]]"
projects: []
space_projects:
  - "[[Project - g-p-explicit-chatgpt-project]]"
knowledge_stores: []
observed_contexts: []
file_reference_count: 5
unique_file_count: 3
resolved_file_count: 2
unresolved_file_count: 1
has_unresolved_files: true
```

`created_at` and `updated_at` are emitted as unquoted Obsidian date-time values at minute precision. The full exported timestamps, including seconds and timezone when present, remain in the generated body and in `90_Evidence/conversations.jsonl`.

`projects` is reserved for the user's own vault project links. ChatGPT/Perplexity-style project contexts recovered from the export go in `space_projects`.

Count fields are intentionally named by their granularity:

- `file_reference_count`: raw file mentions recovered in the conversation evidence.
- `unique_file_count`: deduplicated files referenced by the conversation.
- `resolved_file_count`: deduplicated files whose payload was found and copied into `20_Files/`.
- `unresolved_file_count`: deduplicated file references without an exported payload.
- `source_evidence_count`: rows in `90_Evidence/message_sources.jsonl` for this conversation.
- `tool_evidence_count`: rows in `90_Evidence/tool_events.jsonl`; this is retrieval/tool metadata evidence, not a count of distinct tools.

Readable conversation and context notes include manual edit zones before generated content:

```md
## Manual Review

### Summary
### Restart Notes
### Links

<!-- BEGIN GENERATED OPENAI CONVERSATION -->
...
<!-- END GENERATED OPENAI CONVERSATION -->
```

Generated sections can be overwritten by a fresh parse. Manual sections are intended for human summaries, aliases, restart notes, and curation.

Bounded update behavior is implemented through `--update-existing-pack`: it
regenerates parser-owned content while preserving contract-defined manual
sections. Transactional snapshot reconciliation, stale-output quarantine,
preview, and rollback remain future work documented in
[`incremental-update-contract.md`](docs/architecture/incremental-update-contract.md).

Assets are not sidecar Markdown notes. They are copied once into `20_Files/` by role:

- `Knowledge/` for explicit library/KB files.
- `Attachments/YYYY/MM/` for conversation attachments.
- `Generated/YYYY/MM/` only when assistant/generated origin is explicit.
- `Unlinked/` for physical files with no resolved link, only when `--copy-unlinked-assets` is used.

`20_Files/Knowledge/` and `30_Contexts/Knowledge/` are different layers:

- `20_Files/Knowledge/` contains copied physical payloads from the export, deduplicated by file identity where possible.
- `30_Contexts/Knowledge/` contains hub notes for knowledge stores such as `iks_...`.
- `90_Evidence/file_manifest.jsonl` is the bridge between a copied file, its `file_id`, and any explicit `knowledge_store_id`.

Unlinked files are listed in `90_Evidence/unlinked_assets.jsonl` by default but are not copied into `20_Files/Unlinked/` unless both `--copy-assets` and `--copy-unlinked-assets` are used.

`file_manifest.jsonl` records copied files, so it is populated when `--copy-assets` is used. Asset references remain available in `asset_links.jsonl` even without copying.

The readable file copy may use a proven logical filename and extension, while its physical archive identity remains preserved in `file_manifest.jsonl` and Inventory evidence. The parser never converts or recompresses payload bytes. Materialization, conflict, collision, and duplicate-payload rules are documented in the [Asset Materialization contract](docs/architecture/asset-materialization-contract.md).

Context hubs are separate notes under `30_Contexts/` for GPTs, explicit projects, knowledge stores, and cautiously observed contexts. The current parser does not emit `process` hubs.

The hubs remain a navigation projection. Their linked-file section is explicitly
labelled **Fichiers observés dans les conversations du contexte**: a file observed
while a conversation uses a GPT or project is not thereby an attached GPT/project
knowledge file. The normal readable `parse` command emits the full downstream
evidence chain in the same pack: technical events, logical entities, candidate
edges, context profiles, and the observation-only context resource usage graph.
The individual command below is a repair or migration tool for a disposable pack
copy where the Context Profile JSONL outputs are absent. It is not an extra
normal-parse step and must not be repeated against the same append-only outputs:

```bash
PYTHONPATH=src python3 -m openai_export_obsidian context-profiles --pack /path/to/complete-pack
```

Its contract and JSONL outputs are documented in
[`docs/architecture/gizmo-project-context-reconstruction-contract.md`](docs/architecture/gizmo-project-context-reconstruction-contract.md).

To re-extract into an existing readable pack while preserving contract-defined
manual sections of conversation and context notes, opt in explicitly:

```bash
PYTHONPATH=src python3 -m openai_export_obsidian parse \
  --input files/OpenAI-export.zip \
  --output existing_pack \
  --emit-json --emit-markdown \
  --update-existing-pack
```

This mode matches notes by `conversation_id` or `context_id`, restores only the
manual block before the generated markers, and replaces generated export
content. It does not delete copied files or use manual annotations as parser
input.

### Context resource usage graph

Normal readable `parse --emit-json` emits the observation-only graph describing
how an explicitly observed GPT/project context, conversation, message, and
source reference connect. The optional part is an explicitly supplied owner
confirmation input. The standalone command below is reserved for a disposable
pack copy where the graph JSONL outputs are absent. Its emitters are append-only,
so do not run it after a normal parse or repeat it against the same outputs:

```bash
PYTHONPATH=src python3 -m openai_export_obsidian context-resource-usage \
  --pack /path/to/complete-pack \
  --owner-confirmations /path/to/context_resource_owner_confirmations.jsonl
```

The owner-confirmation file is a deliberate external input with a proof path;
the command never infers it from Markdown, recurrence, or `my_files` usage.
It emits typed nodes, usage edges, per-resource conversation/message metrics,
and distinct conversation-level/message-level cooccurrences in `90_Evidence/`.
Exact filename corroboration is not presented as physical identity or payload
resolution. See the [Context Resource Usage Graph contract](docs/architecture/context-resource-usage-graph-contract.md).

Context hub frontmatter starts with source IDs and an empty alias list. The parser does not invent names:

```yaml
type: openai_context
kind: gpt
title: GPT - g-BpkfnxCrA
aliases: []
context_id: g-BpkfnxCrA
name_evidence: source_id
```

Linked conversations and linked files are rendered inside the generated context section so each GPT, project, or knowledge store can be used as a restart surface.

When `--emit-bases` is enabled, `40_Views/` includes actionable review Bases for curation passes:

- `Needs Context Naming.base` surfaces context hubs in `30_Contexts/` whose aliases are still empty.
- `Needs File Review.base` surfaces conversation notes with unresolved file references or unusually high file-reference counts.
- `Source Heavy Conversations.base` surfaces conversation notes with source or tool/retrieval evidence rows.

Generated Bases and Dataview fallbacks filter on stable frontmatter properties such as `type`, not on `file.inFolder(...)` or `FROM "10_Conversations"`, so the pack can be nested inside another vault folder without breaking the views.

Conversation-level flags such as `memory_scope`, `voice`, `is_archived`, `is_starred`, and `is_do_not_remember` are rendered in the note body under `Export Metadata`, not in conversation frontmatter. Missing or `null` values are not promoted into frontmatter as facts.

## Sample Output

Generate a targeted sample from an explicitly supplied export with
`--conversation-id` as shown above. A local `sample_output/` directory is not
committed as a canonical fixture. A representative source shard may have a path
such as:

```text
User Online Activity/Conversations__...part-0001.zip::conversations-000.json
```

It demonstrates:

- 1 readable conversation note with complete transcript.
- deduplicated files under `20_Files/`.
- context hubs under `30_Contexts/`.
- Bases/Dataview views under `40_Views/`.
- machine proof JSONL under `90_Evidence/`.
- Textdoc/canvas payloads explicitly present in JSON, copied to `20_Files/Textdocs/` and indexed in `textdocs.jsonl`.

## Query Evidence

Use the mini explorer to inspect one conversation without opening every JSONL file manually:

```bash
openai-export-obsidian query conversation \
  --pack sample_output \
  <conversation-id>
```

It prints the conversation, optional pack manifest summary, contexts, counts, sources used, assets, textdocs/canvas evidence, tool events, and evidence pointers. Labels are in English; raw exported content is not translated. Older packs without `90_Evidence/pack_manifest.json` remain queryable.

For a validated, privacy-bounded analytical projection, stream conversations as deterministic JSONL:

```bash
openai-export-obsidian query conversations \
  --pack sample_output \
  --month 2026-06 \
  --needs-review
```

This command validates the complete pack before writing stdout, reads
`conversations.jsonl` as a stream, and uses the producer-owned locator only when
the pack manifest declares it emitted. It never derives a note path from a
title. Its closed output excludes titles, message content, chat URLs, source
URLs/snippets, raw metadata, and payload data.

## Parsing Data Explorer

The separate analytical application consumes a validated pack without writing
to it. The pack and output must be distinct directories below one Obsidian
vault root:

```bash
openai-export-obsidian analytics inspect --pack /path/to/pack --output /path/to/Analytics/pack
openai-export-obsidian analytics install --pack /path/to/pack --output /path/to/Analytics/pack
openai-export-obsidian analytics refresh --pack /path/to/pack --output /path/to/Analytics/pack
```

`inspect` is read-only. `install` installs the versioned Markdown, DataviewJS,
and CSS application and creates human review state only when absent. `refresh`
requires a compatible installation and transactionally replaces only `cache/`;
it preserves `app/`, `state/`, and the source pack. The local cockpit uses no
CDN or additional chart plugin.

## Validate A Pack

Run the pack validator after generating any sample or full output:

```bash
openai-export-obsidian validate --pack sample_output
```

It checks JSONL syntax, conversation/message and conversation/asset count alignment, missing proof paths, invalid asset statuses, copied file paths, extracted textdoc hashes, citation-link foreign keys and proof paths, contradictions between found assets and `unlinked_assets.jsonl`, and obsolete or ambiguous Obsidian property contracts such as `vault_projects`, non-list project fields, space project links in `projects`, and timezone-bearing `created_at` frontmatter.

## Verification

Run the unit tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Run syntax checks:

```bash
find src tests -name '*.py' -print0 | xargs -0 python3 -m py_compile
```
