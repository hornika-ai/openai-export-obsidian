# Parse-Run Summary Contract

Status: specified future work; not implemented.

## Goal

Provide one concise human-readable end-of-run summary without creating a second
forensic canon. A machine-readable projection is optional and must be justified
by a concrete downstream consumer.

## Existing Sources

The implementation must aggregate values already proven by:

- `pack_manifest.json`;
- `parse_audit.json`;
- canonical JSONL evidence;
- typed `*_summary.json` files.

It must not reparse Markdown or infer statistics from filenames.

## Output

Required human projection:

- append exactly one bounded `BEGIN PARSE SUMMARY` / `END PARSE SUMMARY` block
  per parse invocation to the existing operational log, generated directly
  from the in-memory aggregate objects that already feed the audit and typed
  summaries;
- print a shorter aggregate completion line to the console;
- point readers to the existing detailed evidence files.

The operational log may contain blocks from several invocations. A new run
must not rewrite or silently replace an earlier block. Each block must identify
its invocation without exposing private labels, content, or absolute paths in a
shareable projection.

Optional machine projection:

- may extend `90_Evidence/parse_audit.json` with a `run_summary` object when a
  declared downstream consumer needs one;
- must not add an undeclared `openai-obsidian-pack-v1.4` evidence file;
- is not a prerequisite for implementing or releasing the human summary.

## Required Aggregates

- conversation shards;
- parsed and emitted conversations;
- emitted messages, including alternate branches represented in
  `messages.jsonl`;
- explicit or mechanically reconstructed project context profiles;
- GPT context profiles;
- knowledge stores and observed contexts when explicitly represented;
- file references by found, missing, collision, and other typed status;
- distinct physical payloads materialized;
- observed and copied unlinked payloads;
- TextDocs, citations, message sources, and tool events;
- warning and unknown counts.

Every counter must document its evidence source and unit. For example,
file-reference rows and distinct copied payloads are different units.

## Privacy and Determinism

- Aggregate counts only: no titles, prompts, snippets, source IDs, mappings, or
  private labels.
- No absolute path in a shareable summary; the local log may identify its
  output only according to the existing logging policy.
- Same canonical evidence produces the same aggregate object.
- Determinism applies to the aggregate object and the summary block for one
  invocation, not to byte identity of the complete append-only operational log.
- Wall-clock duration and timestamps may appear in the operational log but not
  in deterministic comparisons.
- `readable` and `readable_compact` must produce identical evidence-derived
  statistics.

## Validation

Tests must independently recompute each displayed aggregate from its declared
source and reject mismatches. If the optional machine projection is emitted,
`validate` must verify it too. Tests must cover empty packs, filtered runs, more
than 20 conversation shards, all asset statuses, unlinked-copy opt-in and
opt-out, alternate branches, missing optional evidence, and
profile-independent output. Logging tests must also verify one delimited summary
block per invocation and unmodified earlier blocks across repeated runs.

## Non-Goals

- No semantic project discovery.
- No ranking, clustering, scoring, or conversation summary.
- No user-content excerpts.
- No replacement for detailed JSONL or existing typed summaries.
- No expansion of `openai-obsidian-pack-v1.4` solely for presentation.
