# Citation Linking Contract

Status: implemented.

## Purpose

`openai_export_obsidian.citation_links` preserves supported inline citation
markers from the OpenAI export as source-linked evidence. It does not search
the web, normalize a claim, or infer a missing source.

```text
Raw message marker + raw metadata
  ↓
CitationLinkRecord
  ↓
90_Evidence/citation_links.jsonl
  ↓
readable_compact Markdown navigation
```

## Scope and source families

The current parser recognises these export marker forms:

- web search: `citeturn0search0`;
- file retrieval: `fileciteturn1file0L3-L4`;
- tether/content-reference form: `【31†L27-L35】`.

Web search markers resolve only through explicit `search_result_groups` or
`content_references[].items[].refs` rows. File retrieval markers resolve only
through explicit citation metadata. Tether markers resolve through an exact
`content_references[].matched_text` row, using its source offset when present.

Some observed tether-v4 exports store offsets one-based while Python strings
are zero-based. The resolver accepts an exact match first, then that explicit
one-character convention. It never uses the displayed source ordinal (for
example `31`) as a source key.

## Evidence record

Each `citation_links.jsonl` row has a deterministic `citation_link_id` and
keeps:

- `conversation_id`, `message_id`, and `node_id`;
- the original marker and its character span;
- a deterministic occurrence-level `footnote_id`, retained for compatibility
  with existing evidence consumers;
- `citation_family` and `resolution_status`;
- title, URL, file ID, copied pack path, line range, snippet, and proof path
  when explicit in the export.

Allowed statuses are:

```text
resolved
unresolved_marker
```

`resolved` requires a raw-export `proof_path`. `unresolved_marker` preserves
the original marker in the transcript and evidence JSONL; it is not a claim
that the cited source is absent from the web or from another export surface.

## Rendering rule

Only `readable_compact` turns a resolved marker into an Obsidian same-note
block link. The visible label remains the original export marker, such as
`【31†L27-L35】`. The projection groups markers by explicit source: a safely
normalized URL for web sources, then a canonical file identity for file
sources. Repeated occurrences of one source therefore share one source note
under `## Citation notes`, while their individual line ranges, snippets, and
proof paths remain listed beneath that source. This Markdown-only source key
does not remove or merge any `citation_links.jsonl` rows.

The standard `readable` and `forensic` profiles do not depend on this Markdown
projection. `citation_links.jsonl` remains the machine-readable source of
truth for all readable outputs.

## Non-goals

- No web retrieval or URL validation.
- No citation created from prose alone.
- No matching by title, filename, or displayed ordinal.
- No replacement of raw evidence by Markdown navigation.
