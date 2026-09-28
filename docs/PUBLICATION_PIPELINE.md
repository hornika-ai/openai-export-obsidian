# GitHub Publication and CPF Compatibility Pipeline

Status: specified process. No remote, push, pull request, release, private-data
access, or downstream approval is authorized by this document.

## Goal

Publish a self-contained forensic Parser whose public source, synthetic
fixtures, `openai-obsidian-pack-v1.4` pack-schema contract, documentation, and tests can be reviewed without
including an OpenAI export, generated private pack, private identifiers, or
downstream CPF state.

## Validation Vocabulary

Use the narrowest completed claim:

- **Pack-schema producer validated**: the Parser emits a pack accepted by its validator
  and public contract suite.
- **CPF synthetic pack-schema compatibility validated**: CPF accepts the pinned
  `openai-obsidian-pack-v1.4` public contract and synthetic pack.
- **Fresh private producer validated**: a newly generated private pack passes
  Parser validation. This says nothing about CPF review or approval.
- **Private CPF ranking produced**: one authorized ranking exists for the
  validated pack. This does not imply that a preview was generated or reviewed.
- **Private CPF review approved**: the required inspection, classification,
  selection, approval, and UI gates have all completed under their own
  authorizations.

Do not shorten an intermediate claim to “CPF compatible”. Producer validation,
consumer acceptance, human review, and publication approval are distinct.

## Stage 1 — Public Producer Gate

Record the exact branch and commit, then run only public checks:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
find src tests -name '*.py' -print0 | xargs -0 python3 -m py_compile
PYTHONPATH=src python3 -m openai_export_obsidian validate \
  --pack contracts/parser-pack/openai-obsidian-pack-v1.4/synthetic-example/pack
git diff --check
```

The expected synthetic validation result is 2 conversations and 4 messages.
Confirm that the versioned `openai-obsidian-pack-v1.4` contract is unchanged
unless the checkpoint is an explicitly reviewed contract change.

## Stage 2 — Fresh Private Producer Gate

This stage requires separate authorization and an explicitly supplied
read-only export. Generate one new pack in a new external output directory,
then run `validate` before opening the pack in Obsidian or supplying it to CPF.

Private results may be reported only as safe aggregate counts and pass/fail
status. Payloads, titles, identifiers, snippets, mappings, and absolute paths
must not enter the public repository or release discussion. Passing this stage
does not authorize any downstream CPF action.

## Stage 3 — Human Obsidian Inspection Gate

With separate authorization, inspect representative notes covering citations,
files, TextDocs, contexts, tools, branches, missing payloads, homonym candidates,
and long footnote sets. Record only a pass/fail decision and safe aggregate
observations outside the public repository.

This inspection neither classifies CPF candidates nor approves publication.

## Stage 4 — CPF Review Gates

CPF consumes a validated `openai-obsidian-pack-v1.4` pack, never the source ZIP. Its private lane
requires the environment opt-in, lane selector, confirmation flag, new
non-overlapping external paths, and separate authorization.

Run the downstream gates in this order:

1. **Ranking production**: create one canonical ranking from the authorized
   validated pack.
2. **Preview generation**: render the review preview from that ranking without
   rescanning or regenerating the pack.
3. **Inspection**: review the preview and producer-owned note locators without
   recording classifications.
4. **Classification**: assign the allowed review labels; this does not select
   or approve candidates.
5. **Selection or rejection**: record the candidate decision. Selected does not
   mean approved.
6. **Approval**: explicitly approve the bounded downstream use.
7. **UI gate**: open or validate Streamlit only after the preceding approval
   when UI use is separately authorized.

Authorization for one gate never cascades to the next. A failed or incomplete
gate stops the lane without automatic retry, rescan, regeneration, selection,
approval, or UI launch.

## Stage 5 — Public Repository Hygiene Gate

There is no canonical private-data scanner command in this repository. Perform
the following explicit checks instead of claiming an undefined scan:

```bash
# Review every tracked path and every staged hunk.
git ls-files
git diff --cached --check
git diff --cached

# The tracked-path query should return no exports, generated outputs, secrets,
# caches, or local application state.
git ls-files | rg '(^|/)(files|output[^/]*|secrets|__pycache__|\.pytest_cache|\.DS_Store)(/|$)'

# The tracked-symlink query should return no entries.
git ls-files -s | awk '$1 == "120000" {print}'

# Review every match; block publication for a real credential or personal path.
git grep -n -I -E '(/Users/[^ <]+|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|sk-[A-Za-z0-9_-]{20,})' \
  -- . ':!docs/PUBLICATION_PIPELINE.md'
```

Also verify that `.gitignore` excludes exports, outputs, private validation
stores, secrets, caches, and local application state; documentation uses only
synthetic examples or placeholders; and a clean checkout builds using only
documented public inputs. Any non-empty query result requires human inspection,
not automatic deletion or sanitization.

## Stage 6 — GitHub Review

Remote actions require explicit authorization. When approved:

1. configure or verify the intended remote;
2. push a dedicated branch;
3. open a review pull request;
4. run public CI only;
5. resolve findings without importing private evidence;
6. merge only after the release claim matches the completed gates;
7. create Issues or a milestone only with separate authorization.

Private packs and private CPF runs must never be attached to an Issue, pull
request, CI artifact, release, or repository discussion.

## Release Acceptance

A GitHub release is ready when:

- the public checkout reproduces the synthetic bundle deterministically;
- the `openai-obsidian-pack-v1.4` contract and validator agree;
- README commands work in a clean environment;
- public tests, compilation, path inspection, content inspection, symlink
  inspection, and diff checks pass;
- release notes state the exact validation level reached;
- all unreleased roadmap items remain marked proposed, specified, planned, or
  deferred.
