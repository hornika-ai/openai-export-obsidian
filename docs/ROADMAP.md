# Parser Roadmap

This roadmap records future work without presenting it as implemented. The
current package release, the `openai-obsidian-pack-v1.4` pack schema, the
`1.0.2` contract bundle, released behavior, and proposed evolution remain
separate.

## Status Vocabulary

- **Proposed**: requirement captured; implementation is not approved.
- **Specified**: implementation contract written and reviewed.
- **Planned**: approved for a dedicated future checkpoint.
- **In progress**: implementation active on an identified branch.
- **Validated**: implementation and tests complete, not yet integrated.
- **Released**: integrated into the reference release.
- **Deferred**: intentionally postponed.

## Release Baseline

- Renderer checkpoint: `842082c06dd2ab0f6611952dd80c3bc5c90566d5`
- Python package: `0.1.0`
- Pack schema: `openai-obsidian-pack-v1.4`
- Contract bundle: `1.0.2`
- Markdown default: `readable_compact`
- Validation status: public contract and synthetic CPF compatibility validated;
  a fresh private pack under the current pack schema completed generation with
  exit code `0`, then passed the guarded CPF reader and entered the separately
  authorized downstream review lane. A formal Parser `validate --pack` was not
  run on that pack. Consumer-side human review and approval remain distinct from
  Parser release evidence.
- Publication readiness is assessed with
  [the publication pipeline](PUBLICATION_PIPELINE.md); repository visibility is
  operational state, not part of the parser contract.

## Planned Evolution

| Feature | Status | Contract | Pack-schema boundary |
|---|---|---|---|
| Transactional snapshot reconciliation | Specified | [Incremental update](architecture/incremental-update-contract.md) | Compare explicit snapshot evidence; absence is not deletion |
| Consolidated parse-run summary | Specified | [Parse-run summary](architecture/parse-run-summary-contract.md) | Derive aggregates without adding canonical evidence |
| Historical DALL·E evidence enrichment | Specified | [Historical DALL·E evidence](architecture/historical-dalle-evidence-contract.md) | No inferred historical-to-physical join |
| Private CPF Streamlit review | Proposed downstream | Local private handoff, intentionally outside this repository | Consumer-side; Parser never reads UI state |

## Transactional Snapshot Reconciliation

The implemented `--update-existing-pack` mode regenerates a complete export and
preserves contract-defined manual sections. Future work must add a deterministic
change preview, explicit snapshot-state comparisons, stale-output quarantine,
recoverable application, validation, and rollback. The comparison must cover
conversations, alternate branches, `space_projects`, GPTs, knowledge stores,
and other explicitly represented contexts. It may report an observed state
transition only when both snapshots provide enough evidence; absence from a ZIP
must remain `absent_from_snapshot`, never `deleted`.

Product evolution is also part of the input boundary. ChatGPT Work was
[introduced on July 9, 2026](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)
and the merged ChatGPT desktop app became globally available that day. This is
a chronology marker, not proof that a particular export ZIP contains Work or
Codex history. Every newly downloaded export family therefore needs an
inventory and schema-drift comparison before its new fields or files are
promoted into the Parser contract. Until transactional reconciliation exists,
production refreshes use a new output directory per complete export.

## Consolidated Parse-Run Summary

The Parser already emits `parse_audit.json`, `pack_manifest.json`, and typed
`*_summary.json` evidence. Future work will consolidate their proven aggregate
counts into the existing parse log and console completion output. The first
implementation does not need to add a new JSON file or a `run_summary` object:
it can render directly from the in-memory aggregate objects that already
produce the audit and typed summaries. A later machine projection may extend
`parse_audit.json` if a concrete downstream consumer requires it. Neither form
may introduce a competing evidence source or expose private labels, titles,
IDs, or content.

## Historical DALL·E Evidence

Future work may improve visibility of explicit historical DALL·E facts already
present in exported evidence. It must not invent generation identities, resolve
historical records to current files without proof, or change canonical records
under `openai-obsidian-pack-v1.4` merely to improve presentation.

## GitHub Work Planning

After a remote repository is explicitly approved and configured, each roadmap
feature should become a separate GitHub Issue. Related issues may be grouped in
a milestone such as `Post-current-pack-schema evolution`. Issue creation, remote configuration,
pushes, Projects, milestones, and releases are external mutations and require
separate authorization.

Suggested future issues:

1. Specify and implement transactional snapshot reconciliation.
2. Add consolidated parse-run statistics.
3. Enrich explicit historical DALL·E evidence.
4. Complete the downstream private CPF review and approval gates.
5. Implement the downstream private CPF ranking-review UI.

## Promotion Rule

A roadmap item moves to **Released** only when its implementation, tests,
contract impact, documentation, and public/private boundary have been reviewed
on a dedicated checkpoint. Discussion, a local experiment, or a private run is
not release evidence by itself.
