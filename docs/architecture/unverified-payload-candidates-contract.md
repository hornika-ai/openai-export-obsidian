# Unverified Payload Candidate Contract

Status: implemented.

## Purpose

`openai_export_obsidian.payload_candidates` records a narrow diagnostic case:
an exact OpenAI file ID is referenced by a message, has
`no_candidate_in_inventory`, and another already materialized payload has the
same logical basename.

```text
Asset reference with exact file ID
  ↓
Asset-resolution comparison: no_candidate_in_inventory
  +
Materialized payload with same logical basename
  ↓
Unverified payload candidate diagnostic
  ↓
Readable Markdown note
```

This is deliberately not a physical resolution. A shared filename does not
prove that two OpenAI IDs identify the same upload, version, or bytes.

## Inputs and boundary

The layer consumes only records already produced during the same parse:

- `asset_links.jsonl` semantics, before the file is written;
- `asset_resolution_comparisons.jsonl` semantics, to require the exact
  `no_candidate_in_inventory` state;
- materialized `file_manifest.jsonl` rows.

It does not reopen the ZIP, recalculate SHA-256, modify a physical resolution,
change payload disposition, copy a payload, or create a canonical relation.
References with another resolution state (external URL, unreadable inventory,
collision, inline payload, and so on) are not candidates.

## Outputs

Under `90_Evidence/` in readable packs:

- `unverified_payload_candidates.jsonl` — one row per exact missing reference
  and materialized same-name candidate;
- `unverified_payload_candidate_summary.json` — deterministic counts and the
  non-promotion contract.

Important fields include:

| Field | Meaning |
|---|---|
| `reference_file_id` | Exact OpenAI ID referenced by the message and absent from the candidate scope. |
| `candidate_file_id` | Different OpenAI ID of a materialized homonym. |
| `candidate_physical_archive_path` | Physical provenance of the candidate payload. |
| `candidate_copied_path` | Readable-pack location of that candidate; not a substitute for the missing reference. |
| `candidate_count_for_name` | Number of materialized candidates with the same case-folded basename. |
| `type_comparison` | `same_extension`, `extension_conflict`, or `extension_unavailable`; never a content claim. |
| `canonical` | Always `false`. |
| `non_promotion_reason` | Why filename equality cannot become a physical relation. |

All candidates are `status: unverified_candidate`, `confidence: low`, and use
`matching_rule: casefolded_logical_basename_equality`. Multiple homonyms remain
multiple rows with `multiple_same_name_payloads`; no winner is selected.

## Markdown projection

The renderer may show a nested diagnostic in the message that owns the exact
missing reference. It preserves the missing `file_id`, names each related
candidate ID, and points to the JSONL evidence. It must not replace the missing
reference with an Obsidian link to a homonym or count the candidate as a found
file for the message.

## Validation contract

Every candidate row must point to:

1. an existing `asset_links` row that is still `missing` for the stated exact
   `reference_file_id`;
2. an `asset_resolution_comparisons` row whose sole resolution status is
   `no_candidate_in_inventory`;
3. a materialized `file_manifest` row matching the candidate ID and physical
   archive path.

The summary asserts that raw ZIP data was not read and that no canonical
relation, resolution, disposition, or payload copy was changed.
