# Reconstruction Pack Validation

This recipe validates the Parser's reconstruction behavior before any semantic,
NLP, clustering, scoring, or memory-oriented exploitation. The emitted pack
continues to declare the `openai-obsidian-pack-v1.4` schema.

## Normal Validation Path

Generate one new pack in an external output directory. The normal `parse`
command emits the complete downstream evidence chain; do not run the individual
layer commands again against this freshly generated pack.

```bash
PYTHONPATH=src python3 -m openai_export_obsidian parse \
  --input <read-only-export.zip> \
  --output <new-external-output> \
  --copy-assets \
  --emit-json \
  --emit-markdown \
  --markdown-profile readable_compact \
  --emit-bases \
  --emit-dataview
```

Copying unlinked physical payloads is a separate forensic opt-in. Add
`--copy-unlinked-assets` only when that expanded materialization is required.

Validate the resulting pack:

```bash
PYTHONPATH=src python3 -m openai_export_obsidian validate \
  --pack <new-external-output>
```

The public synthetic review matrix is
[`reconstruction-review-cases.jsonl`](reconstruction-review-cases.jsonl).

## Rebuilding Individual Evidence Layers

The `technical-events`, `logical-entities`, `candidate-edges`,
`context-profiles`, and `context-resource-usage` commands remain available for
targeted reconstruction work. Their JSONL emitters are append-only. Running
them after a normal parse can duplicate evidence rows and invalidate the pack.

Use those commands only on a prepared disposable copy whose relevant derived
outputs have been removed according to the layer contract, or in a separately
specified replacement workflow. Never use them as extra steps in the normal
`parse` then `validate` path.

## Public Synthetic Gate

Before a documentation or release checkpoint:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m openai_export_obsidian validate \
  --pack contracts/parser-pack/openai-obsidian-pack-v1.4/synthetic-example/pack
```

The synthetic pack is expected to validate with 2 conversations and 4 messages.
It contains no private export data.

## Manual Review Checklist

- The main transcript follows `current_node`, while alternate branches remain
  represented and linked.
- Missing files, homonym candidates, non-text parts, Canvas references, and
  textual claims without payload remain explicit without invented files.
- Exported Textdoc payloads have a copied path and matching content hash.
- Compact citations and evidence details use deterministic native footnotes;
  repeated explicit sources reuse one definition and unresolved markers stay
  unresolved.
- `readable_compact` changes only the Markdown projection; canonical JSONL and
  pack schema remain profile-independent.
- Unlinked physical payloads are recorded by default and copied only with the
  explicit opt-in.

## Private and Human Gates

A public synthetic pass does not authorize access to a private export or pack.
Private producer validation requires a supplied read-only input, a new external
output path, and separate authorization; report only safe aggregates. Human
visual review and downstream consumer approval are later, independent gates as
defined in the [publication pipeline](../PUBLICATION_PIPELINE.md).

## Acceptance Criteria

- The complete public test suite passes.
- The public synthetic pack validates without errors.
- The generated pack contains the required evidence layers without duplicate
  rows introduced by manual re-emission.
- Every review case has a public test or golden-fixture anchor.
- No private payload, title, identifier, absolute path, or historical private
  metric appears in the public validation material.
