# Gizmo and Project Context Reconstruction Contract

Status: implemented.

## Scope

`context-profiles` is a downstream, forensic reconstruction layer. It reads
only structured evidence already emitted under `90_Evidence/`; it never opens an
OpenAI export ZIP, invokes Physical Resolution, changes logical entities, or
promotes a candidate edge.

```text
technical events + logical entities + candidate edges + conversation evidence
  ↓
Gizmo and Project Context Reconstruction
  ↓
forensic context profiles JSONL
  ↓
optional Obsidian projection
```

## Identity rule

Only an explicit identifier creates a profile:

| Explicit identifier | Profile type |
| --- | --- |
| `g-*` excluding `g-p-*` | `GizmoContextProfile` |
| `g-p-*` | `ProjectContextProfile` |

The profile ID is a deterministic pipeline ID. It is not an OpenAI ID. A
conversation title, a slug, a basename, a date, or folder placement never
creates a context identity.

## Evidence inputs

The command reads structured pack outputs only:

- conversation/context projections;
- technical events and logical-entity observations;
- candidate context edges and their evidence sets;
- message source records, Textdocs, references and existing optional evidence.

Primary and embedded historical observations remain separate. A candidate edge
can list a context observation, but always remains `candidate_not_promoted`.

## File relation rule

The layer never upgrades a conversation-level file into a GPT/project attachment.
Current parser hubs aggregate these files for navigation; this layer emits them
as `observed_in_context_conversation`, `mentioned_or_cited_in_context_conversation`,
`candidate_context_membership`, or `unresolved` unless a dedicated explicit
context field is available.

No current structured input is sufficient to emit
`explicit_context_attachment` or `explicit_context_library_reference`; those
categories remain reserved for future explicit source fields.

## Instructions rule

`explicit_context_instruction` requires a dedicated context-scoped metadata
field and a matching explicit context ID in the same technical event.
Instruction-like metadata and instruction-titled Textdocs found in a context
conversation are preserved as `candidate_context_instruction`. Message prose,
the existing empty Markdown `System Instructions` heading, and human-maintained
Description fields are not extracted as evidence.

## Outputs

Normal readable `parse --emit-json` constructs and emits these profiles. The
standalone `context-profiles --pack <pack>` command is a repair or migration
tool for a disposable pack copy where the outputs below are absent. Its JSONL
emitters are append-only, so it must not follow a normal parse or be repeated
against the same outputs.

The layer emits under `90_Evidence/`:

- `gizmo_context_profiles.jsonl`;
- `project_context_profiles.jsonl`;
- `context_profile_observations.jsonl`;
- `context_instruction_candidates.jsonl`;
- `context_file_memberships.jsonl`;
- `context_profile_contradictions.jsonl`;
- `context_profile_summary.json`.

With `--emit-obsidian`, the command writes a separate inspection projection under
`30_Contexts/Forensic Profiles/`. It does not overwrite the existing GPT/project
hubs or their manual sections.

The summary records this mandatory contract:

```json
{
  "raw_zip_reparsed": false,
  "candidate_edges_promoted_automatically": false,
  "physical_resolution_called": false,
  "historical_messages_merged": false,
  "context_file_membership_inferred_from_conversation_only": false
}
```

## Existing Obsidian hubs

The existing `30_Contexts/` hubs remain a compatible projection. Their file
section is labelled **Fichiers observés dans les conversations du contexte** to
avoid representing conversation-level files as context attachments. A later,
explicitly approved projection may render the profile classifications in separate
sections without replacing manual Context, Description, System Instructions, or
Restart Notes areas.
