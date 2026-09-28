# Incremental Update Contract

The readable parser supports an explicit `--update-existing-pack` mode. This
document defines its safety boundary.

Status: implemented with future transactional work.

The bounded regeneration behavior below is implemented. Transactional snapshot
reconciliation is specified as future work and is not implemented.

## Stable Identities

- Conversations: `conversation_id`
- Contexts: `context_id`
- Files: `file_id`, then `dedupe_key`, then `archive_path`
- Messages: `message_id`, falling back to `node_id` only when `message_id` is absent

## Manual Sections

Conversation notes preserve human edits only in:

- `## Manual Review`
- `### Summary`
- `### Restart Notes`
- `### Links`

Context notes preserve human edits only in:

- `## Manual Context`
- `### Description`
- `### System Instructions`
- `### Restart Notes`

Generated content is bounded by:

- `<!-- BEGIN GENERATED OPENAI CONVERSATION -->`
- `<!-- END GENERATED OPENAI CONVERSATION -->`
- `<!-- BEGIN GENERATED OPENAI CONTEXT -->`
- `<!-- END GENERATED OPENAI CONTEXT -->`

## Update Rule

`parse --update-existing-pack`:

1. matches conversation notes by `conversation_id` and context notes by
   `context_id`, not by filename;
2. preserves the contract-defined manual section verbatim;
3. replaces generated export content from the new ZIP;
4. recomputes `space_projects`, `gpts`, `knowledge_stores`,
   `observed_contexts`, counts, and evidence pointers from the export;
5. never deletes files from `20_Files/` automatically.

Frontmatter and content outside the defined manual section remain parser-owned.
The mode does not use manual annotations to alter forensic evidence or physical
resolution.

## Current Limitations

The current mode is a full regeneration from the newly supplied export, not a
delta-only import. Existing conversations are parsed again, generated files at
the same paths are replaced, and newly exported conversations are emitted.

The mode does not yet:

- classify conversations as unchanged, modified, added, or
  `absent_from_snapshot`;
- compare explicitly represented archive state, project membership, GPT
  association, or alternate-branch topology between snapshots;
- inventory export-schema drift before interpreting newly observed top-level
  files, object families, or fields;
- remove or quarantine generated notes whose path changed;
- remove stale context notes, views, or copied payloads;
- produce a preview of planned filesystem mutations;
- apply changes transactionally or roll them back after a failed validation.

For repeatable production runs, the safe default remains one new output
directory per complete OpenAI export.

## Future Transactional Snapshot Reconciliation

Status: proposed.

A future refresh must compare one validated prior pack with one complete new
export and emit a change plan before mutation. The plan must report:

- unchanged, modified, added, and `absent_from_snapshot` conversation
  identities;
- explicit conversation-state transitions when both snapshots carry the
  required evidence, including archived or unarchived state;
- explicit project/GPT/context association changes, without inferring a move
  from a missing association;
- alternate branches added, changed, or absent from the new snapshot, scoped by
  canonical conversation and message identity;
- added or removed export members and newly observed object families or fields
  that require schema review;
- note and context path changes;
- manual sections eligible for preservation;
- stale generated notes, views, and payload copies;
- files proposed for quarantine rather than deletion;
- expected canonical record and locator counts after application.

The workflow must remain explicitly staged:

1. `plan-update` reads both inputs and writes no pack mutation;
2. a human reviews the deterministic change plan;
3. `apply-update` requires explicit confirmation and writes through a
   recoverable staging area;
4. the complete pack validator runs before promotion;
5. failure leaves the prior validated pack usable and reports the rollback or
   quarantine location.

An identity absent from the new export is evidence only of absence from that
export. It must never be treated as proof that the user deleted the
conversation, and it must never be silently deleted from the prior pack.

## Snapshot-State Vocabulary

The comparison layer records what the two supplied snapshots prove. It does not
claim access to current ChatGPT account state.

- `unchanged`: the canonical identity and compared parser-owned projection are
  equivalent in both snapshots;
- `modified`: the identity exists in both snapshots and compared canonical
  content or explicit metadata differs;
- `added`: the identity is present only in the new snapshot;
- `absent_from_snapshot`: the identity is present only in the prior snapshot;
- `state_transition_observed`: both snapshots explicitly expose the relevant
  state and their values differ;
- `schema_drift_observed`: the new ZIP contains a previously uncontracted
  member, object family, or field.

`deleted`, `moved`, `archived`, `unarchived`, or `branch_removed` may be emitted
only as qualified observed transitions when the necessary before-and-after
evidence exists. They must not be inferred from `absent_from_snapshot`, a
changed note path, a current project listing, or an export-format change.

ChatGPT, Codex, and ChatGPT Work histories and memories may be stored or
exported through different product surfaces. Product release chronology can
trigger a schema-drift review, but it is not export evidence by itself.

## Non-Goals

- No automatic project/GPT naming.
- No automatic synthesis notes.
- No automatic conversion of conversations into durable knowledge.
- No automatic deletion based only on absence from a later export.
