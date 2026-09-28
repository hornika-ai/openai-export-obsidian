# Context Resource Usage Graph Contract

Status: implemented.

## Role

The observation-only `context-resource-usage` graph is an implemented analytical
layer after `context-profiles`. Normal readable `parse --emit-json` emits it from
already structured observations:

```text
explicit GPT/project context
  -> observed conversation
  -> message containing a source record
  -> observed resource reference
```

An optional, deliberately supplied owner-confirmation JSONL can enrich that
graph. It can assert that a named resource belongs to a context, but it never
resolves a payload or turns an export reference into a physical-file identity.

## Inputs

The command reads only pack evidence:

- `gizmo_context_profiles.jsonl` and `project_context_profiles.jsonl`;
- `context_profile_observations.jsonl`;
- `context_file_memberships.jsonl`;
- `message_sources.jsonl`.

It optionally reads a JSONL passed with `--owner-confirmations`, or the
existing `90_Evidence/context_resource_owner_confirmations.jsonl` if present.
It does **not** scrape human Markdown, inspect a ZIP, call Physical Resolution,
or consume candidate edges as canonical facts.

## Owner confirmation input

Every input row requires these fields:

```json
{
  "explicit_context_id": "g-...",
  "resource_name": "knowledge-file.md",
  "proof_path": "30_Contexts/GPTs/GPT - Example.md:164-187"
}
```

Optional `owner_confirmation_id`, `resource_identifier`, `statement`, and
`confirmation_scope` are preserved.  `status`, if supplied, must be
`owner_confirmed`.  The result records the source as
`external_owner_confirmation` and `owner_annotation`; it never presents the
internal deterministic confirmation ID as an OpenAI identifier.

## Identity and corroboration rules

Observed resources use the first explicit key available:

1. `file_id`;
2. `strict_library_file_id`;
3. normalized title label;
4. source record fallback.

A title-only key is a **reference label**, not a global file identity.  An
owner declaration may be corroborated by an observed export reference only
when the filename equality is exact after whitespace normalization.  This
creates `owner_declaration_exact_name_corroborated_by_export_reference`, whose
status is `corroborated_name_reference`; it does not assert a matching physical
payload.

The usage metric statuses are intentionally distinct:

- `confirmed_by_owner_and_export_corroborated`;
- `confirmed_by_owner_not_seen_in_export`;
- `observed_context_resource_usage`.

Repeated usage, cooccurrence, common conversation membership, and title
similarity never create an owner confirmation.

## Outputs

The standalone `context-resource-usage --pack <pack>` command is a repair or
owner-confirmation enrichment tool for a disposable pack copy where the graph
JSONL outputs are absent. Its emitters are append-only, so it must not follow a
normal parse or be repeated against the same outputs. The layer writes under
`90_Evidence/`:

- `context_resource_nodes.jsonl` — context, conversation, message, observed
  reference, and owner-declared resource nodes;
- `context_resource_edges.jsonl` — typed provenance/usage edges;
- `context_resource_owner_claims.jsonl` — normalized external owner claims;
- `context_resource_usage_metrics.jsonl` — per context/resource occurrence,
  source kind/namespace (for example `my_files`), conversation/message
  frequency, other-context appearance, and status;
- `context_resource_cooccurrences.jsonl` — separate same-conversation and
  same-message cooccurrence counters;
- `context_resource_usage_summary.json` — counts, integrity checks, and the
  non-promotion contract.

`relative_conversation_frequency` uses only direct
`conversation_context_projection` or `legacy_context_link_projection`
conversations for the same context and source-export scope.  This keeps the
GPT/project -> conversation denominator distinct from a broader context value
observed only in technical-event metadata.  The latter remains visible through
`observed_context_conversation_denominator` and
`relative_observed_context_frequency`. Neither ratio measures importance or
membership strength.

## Contract

```json
{
  "raw_zip_reparsed": false,
  "physical_resolution_called": false,
  "candidate_edges_created": false,
  "canonical_context_memberships_created": false,
  "historical_messages_merged": false,
  "owner_confirmation_is_external_input": true,
  "name_equality_is_not_physical_identity": true
}
```

Historical and primary evidence are never merged.  The current source records
are primary-export observations; any future historical resource source must be
emitted with its own `source_export` scope before this layer can measure it.
