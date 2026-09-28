# Logical Entity Construction Contract

Status: implemented.

## Scope

`logical_entities` is a deterministic, derived-evidence layer after Technical
Event Extraction and before any future provenance graph.  It reads only
`technical_events.jsonl`; it does not inspect ZIP members, use Inventory or
Physical Resolution, create candidate edges, or infer equivalence from names,
suffixes, folders, dates, ordering or proximity.

Each entity is a grouping of an exact source identifier or literal.  It does not
assert that a payload exists, that an operation succeeded, that a generation
produced an image, or that two identifiers designate the same object.

## Entity taxonomy v2

The entity type is selected exclusively from the source field that supplied the
value:

| Source field | Entity type | Subtype or scope |
|---|---|---|
| `asset_pointer` | `remote_asset_pointer` | `sediment`, `file_service`, or `other` namespace |
| `original_file_id` | `original_file_reference` | identifier syntax only |
| `mask_file_id` | `mask_file_reference` | identifier syntax only |
| `library_reference` | `library_file_reference` | `libfile` or `other` namespace |
| `/mnt/data` literal | `runtime_path_literal` | quality plus mandatory conversation scope |
| `original_gen_id` | `original_generation_reference` | `uuid`, `opaque`, `session_reference`, or `other` |
| persistent textdoc ID | `persistent_textdoc_reference` | `hex32` |
| `temp-td-user:<timestamp>` | `temporary_textdoc_handle` | `temp_td_user` |
| embedded `canmore://…` URI | `embedded_canmore_uri_reference` | `canmore_uri` |
| `gizmo_id` starting `g-p-` | `project_context_reference` | `g_p_namespace` |
| other `gizmo_id` values | `gizmo_context_reference` | namespace syntax only |

`g-p-*` is classified as a project context because the same exact namespace was
observed in `space_projects`; this does not create a conversation-project edge.

Runtime path literals are keyed by their exact literal **and conversation scope**.
They are not global file identities.  Their quality is deterministic:
`file_like`, `directory_like`, `template`, `malformed_or_truncated`, or `unknown`.
This is lexical classification only, not physical verification.

## Textdoc handling

Technical Event Extraction extracts `canmore://…` as an URI, never as the full
message body.  The complete source string and its JSON proof path are retained in
the event's `relevant_metadata.embedded_canmore_source_texts`.

Values such as `code/json`, `code/react`, and `document` remain in
`relevant_metadata.textdoc_non_identifier_values`.  They do not materialize a
Textdoc entity.  A technical event containing only such metadata is retained in
`logical_entity_unmaterialized_events.jsonl` with reason
`textdoc_non_identifier_metadata_only`.

## Determinism and provenance

`logical_entity_id`, `logical_entity_observation_id`, and `migration_id` are
pipeline identifiers, not OpenAI identifiers.  `explicit_identifier` preserves
the raw source value.  Each observation retains source export, archive path,
JSON proof path, conversation, node, message, and the new subtype/scope fields.
Primary and historical messages remain separate observations.

## Migration from entity taxonomy v1

The deprecated `logical_asset`, `generation`, `textdoc`, and `gizmo` types are
not emitted in new canonical rows.  When a pre-migration output exists, the
command writes `logical_entity_taxonomy_migration.jsonl`.  Each row maps a legacy
entity to its entity-taxonomy-v2 entity or records why it cannot materialize, preserving legacy
source event IDs.  A global legacy runtime literal may split into multiple
conversation-scoped runtime-path entities.

## Outputs

Under `90_Evidence/`:

- `logical_entities.jsonl` — entity-taxonomy-v2 groups;
- `logical_entity_observations.jsonl` — exact source occurrences;
- `logical_entity_unmaterialized_events.jsonl` — events without an entity-taxonomy-v2 identifier;
- `logical_entity_taxonomy_migration.jsonl` — entity-taxonomy-v1-to-v2 migration evidence;
- `logical_entity_summary.json` — counts, taxonomy distribution, migration and
  explicit no-edge/no-resolution contract.

## Standalone migration command

Normal readable `parse --emit-json` emits the current Logical Entity taxonomy.
The standalone commands below are only for a prepared disposable copy used to
migrate a pre-taxonomy pack. Before each command, the JSONL outputs owned by
that layer must be absent. Both emitters are append-only; do not run this
sequence after a normal parse or repeat it against the same outputs.

```bash
PYTHONPATH=src python3 -m openai_export_obsidian technical-events \
  --input files/OpenAI-export.zip \
  --pack /path/to/complete-pack

PYTHONPATH=src python3 -m openai_export_obsidian logical-entities \
  --pack /path/to/complete-pack
```

The Logical Entity command may retain pre-migration entity rows as its migration
baseline. Reproducibility applies to one prepared migration run, not to repeated
appends into an already populated pack.
