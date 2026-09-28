# Parser pack contract

This is a non-private, machine-readable contract for readable Parser packs. It contains no user export, personal record, source path, pseudonym map, or copied asset.

## Versions

- `parser_version` is the Python project version declared in `pyproject.toml`.
- `pack_schema` is `openai-obsidian-pack-v1.4`, the literal emitted by `runner.SCHEMA_VERSION`.
- `contract_version` / `contract_bundle_version` are `1.0.2`, the version of this independent description bundle.
- “V5” is a README/output-run reference label (for example an `output_v5` convention), not a pack-schema version. The source definitions inspected here do not serialize it into `pack_manifest.json`.

## Generate and verify

```bash
PYTHONPATH=src python3 -m openai_export_obsidian describe-pack-contract \
  --output contracts/parser-pack/openai-obsidian-pack-v1.4
PYTHONPATH=src python3 -m unittest tests.test_pack_contract -v
```

The command takes no export or pack argument. It creates a fresh synthetic ZIP in a temporary directory, invokes the real readable serializer, removes its non-contract log, and refuses to overwrite a different bundle. Verify the `file_hashes` in `contract-manifest.json` with SHA-256; the manifest itself is deliberately excluded from its own hash list.

## External consumer

Read `contract-manifest.json`, reject a different `pack_schema`, validate the closed root layout, then validate `90_Evidence/pack_manifest.json` and stream the closed conversation/message/asset envelopes. If `pack_manifest.json.navigation.conversation_note_locator.state` is `emitted`, use the exact producer-owned relation in `40_Views/conversation_note_locators.jsonl`; do not derive note names or scan Markdown frontmatter. Locator paths are filesystem paths, not pre-rendered Wikilink syntax: escape or encode them only at the consuming UI layer. Conversation Pattern Forge's later repin task is responsible for its own safe Markdown rendering. `00_Home.md`, when present, is only navigation and must not feed evidence fingerprints or record hashes. Apply `invariants.md` for relations and counts. Other evidence files are named in `pack-layout.json`; their authoritative producers are recorded there and in `field-provenance.md`.

This bundle is documentation for a potential consumer only. It does not authorize access to a personal Parser pack.
