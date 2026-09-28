# macOS `.DS_Store` hygiene

Status: implemented.

Parser packs use a closed layout. A file named exactly `.DS_Store` is not a
pack, navigation, evidence, or payload artifact.

## Production boundary

The producer excludes only physical archive payloads whose basename is exactly
`.DS_Store`; neighboring names such as `.DS_Store.backup` keep their normal
forensic treatment. Before a **new** output is returned, Parser performs a
bounded metadata-only sweep of that output and unlinks only regular files with
that exact name. It uses `lstat` semantics and fails if a directory, symlink, or
special entry uses the reserved name.

This protects the producer run. It cannot make a writable directory permanently
immune to Finder: macOS can create `.DS_Store` after generation. A later Finder
artifact remains an invalid pack and is reported by validation as the
machine-readable `macos_ds_store_contamination:<category>=<count>` diagnostic.

## Controlled maintenance boundary

Use the explicit maintenance command only after the pack has been generated:

```text
openai-export-obsidian quarantine-ds-store \
  --pack <existing-pack> \
  --quarantine <new-external-directory> \
  --confirm QUARANTINE_DS_STORE
```

`--dry-run` performs the same metadata-only inventory and does not create the
quarantine or mutate the pack. The mutating operation requires a new,
non-symlinked quarantine directory that is external to the pack, shares its
filesystem, and sits outside a Git worktree. The pack must already have private
permissions: no group or other bits on its root or any non-target entry. Exact
regular `.DS_Store` targets may temporarily have looser permissions because
they are the condition being repaired; Parser never chmods them in place.

Before mutation, the command inventories the entire tree and rejects every
non-regular `.DS_Store` target. It moves only exact regular `.DS_Store` files,
never opens or decodes their contents, assigns generic deterministic quarantine
names, and returns a sanitized JSON report containing counts and integrity
status rather than source paths. The quarantine directory is `0700`; moved files
are `0600`. Before mutation, Parser snapshots opaque device/inode/size/mtime
facts for non-target regular entries and for the targets. It verifies the former
before and after quarantine and verifies same-filesystem target identity after
the move; it does not claim a cryptographic byte comparison. Dry runs explicitly
report that no post-mutation identity comparison was performed. It neither
regenerates nor validates the pack, and it never invokes any downstream consumer.
