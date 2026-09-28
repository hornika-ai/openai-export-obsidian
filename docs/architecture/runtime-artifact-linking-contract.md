# Runtime Artifact Linking Contract

Status: implemented.

## Scope

This layer handles physical files produced by a ChatGPT runtime, especially
Python, that are stored in the export with this observed path grammar:

```text
personal/files/<conversation-id>/<execution-message-id>/mnt/data/<payload>
```

It is separate from `Reference Extraction` and `Physical Resolution` for
`file-...` identifiers. A runtime artifact has an explicit physical path; it
does not need a synthetic `file_id` to be useful evidence.

## Inputs

- `ExportInventory` physical members, including their archive path and detected
  content facts;
- selected parsed conversation IDs;
- `chat.html` members containing the official `var jsonData = [...]` payload,
  when present.

The linker first proves the conversation relation from the path. It then looks
up the intermediate execution ID in the `mapping` of an auxiliary `chat.html`.
The HTML is an auxiliary source: it may retain Python/code nodes absent from a
conversation shard JSON.

## Output

`90_Evidence/runtime_artifacts.jsonl` contains one row per physical payload:

| Field | Meaning |
| --- | --- |
| `conversation_id` | Explicit path segment after `personal/files/` |
| `execution_message_id` | Explicit path segment immediately before `mnt/data` |
| `runtime_path` | Runtime-local `/mnt/data/...` path reconstructed from the archive member path |
| `physical_archive_path` | Canonical Inventory path for the copied source member |
| `relation_status` | `confirmed_by_chat_html` or `conversation_scoped_unconfirmed` |
| `chat_html_*` | Exact auxiliary proof when a mapping node is found |

## Status policy

- `confirmed_by_chat_html`: the execution-folder ID is a node in the matching
  conversation's `chat.html` mapping. The node facts (role, recipient and
  content type) are preserved.
- `conversation_scoped_unconfirmed`: the conversation ID is explicit in the
  path, but no auxiliary `chat.html` mapping node was found. The payload remains
  visible and is never silently promoted to a message-level relation.

The linker never joins a payload to a message from filename similarity,
chronology, ZIP member order, or a first matching basename.

## Reporting and copy policy

Runtime artifacts are not added to `asset_links.jsonl`, which remains the index
of references found in conversation data. They are reported separately, removed
from the orphan list for their emitted conversation, and copied with
`--copy-assets` to `20_Files/Generated/<year>/<month>/`.

Readable notes render one Obsidian-hidden proof comment per artifact:

```markdown
%% openai_message_id: <execution-message-id> %%
> [!info] Python artifact · confirmed by auxiliary chat.html
```

This comment identifies the execution message. It does not claim that this node
is a user-visible natural-language response in the primary conversation shard.
