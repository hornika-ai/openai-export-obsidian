# Faithful Reconstruction Contract

Status: implemented.

This layer reconstructs what is explicitly present in the OpenAI export. It is
bounded to faithful, inspectable projection. Semantic exploitation such as
summarization, NLP interpretation, clustering, scoring, and memory synthesis is
outside the current Parser boundary.

## Goals

- Render the main conversation path ending at `current_node`.
- Preserve awareness that the export is a graph, not a simple list.
- Expose branches and alternate continuations.
- Show message-level evidence for sources, files, tools, retrieval, branches, and export gaps.
- Keep repetitive technical-only records out of the readable transcript when they do not add inspectable context.
- Keep JSONL/raw JSON as canonical proof.

## Non-Goals

- Summarization.
- Semantic interpretation or NLP enrichment.
- Clustering.
- Scoring.
- Memory synthesis.
- Invented project or GPT names.

## Markdown Rule

The main transcript remains the primary reading layer.

Lightweight collapsed callouts may appear directly below a message when the raw export contains relevant evidence:

- branch
- source
- tool/retrieval
- file
- generated artifact mention
- export gap

Technical-only JSON records remain in `90_Evidence/messages.jsonl`; the readable transcript should not repeat identical low-signal callouts.

## Proof Rule

Markdown is an inspection layer. JSONL and raw JSON remain canonical.
