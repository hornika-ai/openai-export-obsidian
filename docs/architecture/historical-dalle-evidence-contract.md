# Historical DALL·E Evidence Enrichment Contract

Status: specified future work; not implemented.

## Goal

Improve the visibility and auditability of explicit historical DALL·E evidence
already present in OpenAI exports while preserving the
`openai-obsidian-pack-v1.4` pack-schema contract and
forensic uncertainty.

## Allowed Evidence

Future implementation may consume only explicit scalar values and existing
typed evidence envelopes with proof paths. It may project:

- an explicitly named image-generation family or tool;
- explicit generation or asset identifiers;
- explicit timestamps, prompts, statuses, URLs, or file references when present;
- the source family and exact proof path;
- typed unknown, missing, ambiguous, or unresolved states.

## Prohibited Inference

The implementation must not:

- invent a DALL·E generation identity;
- infer that a historical generation and current physical payload are the same
  from title, basename, visual similarity, timing, or proximity alone;
- promote a candidate relation into canonical physical resolution;
- merge historical and current identifiers;
- invent a URL, local destination, prompt, model, or status;
- expose private content in aggregate reports or public fixtures.

## Pack-schema boundary

- Reuse existing historical evidence envelopes and proof paths where possible.
- Do not add canonical conversation, message, asset, or locator fields.
- Do not change evidence identities or counters merely for Markdown display.
- Any new diagnostic projection must be explicitly non-canonical and must not
  affect physical resolution.
- Versioned public contract files remain byte-stable unless a separately
  reviewed contract version is intentionally introduced.

## Presentation

Markdown may make proven historical DALL·E facts easier to read, but must retain
the original marker or source relation, attach deterministic native footnotes,
and label unresolved or candidate-only relations without a fabricated link.

## Validation

Required tests:

- explicit historical record with no payload;
- explicit historical record with an independently proven payload;
- same-name or same-time candidate that remains unverified;
- multiple ambiguous candidates;
- absent optional fields;
- deterministic ordering and proof paths;
- byte comparisons for canonical `pack_manifest.json`,
  `conversations.jsonl`, `messages.jsonl`, and `asset_links.jsonl`;
- no change to CPF consumer records or note locators under
  `openai-obsidian-pack-v1.4`.

## Non-Goals

- No image recognition or similarity matching.
- No reconstruction of missing prompts.
- No automatic asset recovery or download.
- No change to the current renderer checkpoint.
- No coupling to the private CPF review UI.
