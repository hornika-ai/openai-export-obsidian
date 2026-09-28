from __future__ import annotations

import re
from hashlib import sha256
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import AssetRecord, ConversationRecord, MessageRecord


CITATION_MARKER_RE = re.compile(
    r"(?P<kind>cite|filecite)(?P<targets>(?:turn\d+(?:search|file)\d+)+)"
    r"(?P<lines>L\d+(?:-L\d+)?)?"
)
CITATION_TARGET_RE = re.compile(r"turn(?P<turn>\d+)(?P<target>search|file)(?P<index>\d+)")
BRACKET_CITATION_RE = re.compile(r"【\d+†L(?P<start>\d+)-L(?P<end>\d+)】")
LINE_RANGE_RE = re.compile(r"L(?P<start>\d+)(?:-L(?P<end>\d+))?")


@dataclass(frozen=True)
class CitationLinkRecord:
    citation_link_id: str
    conversation_id: str
    message_id: str | None
    node_id: str
    marker_original: str
    marker_start: int
    marker_end: int
    footnote_id: str
    citation_family: str
    resolution_status: str
    title: str | None
    url: str | None
    file_id: str | None
    copied_pack_path: str | None
    line_start: int | None
    line_end: int | None
    snippet: str | None
    proof_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_citation_links(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
) -> list[CitationLinkRecord]:
    asset_paths = {
        asset.raw_file_id: asset.copied_pack_path
        for asset in assets
        if asset.raw_file_id and asset.copied_pack_path
    }
    records: list[CitationLinkRecord] = []
    for message in conversation.all_messages or conversation.messages:
        web_by_key = _web_entries(message)
        file_by_key = _file_citations(message)
        bracket_references = _bracket_content_references(message)
        marker_matches = sorted(
            [
                *(("openai", match) for match in CITATION_MARKER_RE.finditer(message.text or "")),
                *(("bracket", match) for match in BRACKET_CITATION_RE.finditer(message.text or "")),
            ],
            key=lambda item: item[1].start(),
        )
        ordinal = 0
        for marker_kind, match in marker_matches:
            marker = match.group(0)
            if marker_kind == "bracket":
                line_start = int(match.group("start"))
                line_end = int(match.group("end"))
                source = _select_bracket_reference(bracket_references, marker, match.start(), match.end())
                targets = [(source, "bracket_content_reference", line_start, line_end)]
            else:
                line_start, line_end = _line_range(match.group("lines"))
                targets = []
                for target_match in CITATION_TARGET_RE.finditer(match.group("targets")):
                    turn = int(target_match.group("turn"))
                    index = int(target_match.group("index"))
                    if target_match.group("target") == "search":
                        source = web_by_key.get((turn, index))
                        family = "web_search_result"
                    else:
                        source = file_by_key.get((turn, index, line_start, line_end)) or file_by_key.get(
                            (turn, index, None, None)
                        )
                        family = "file_retrieval"
                    targets.append((source, family, line_start, line_end))
            for source, family, target_line_start, target_line_end in targets:
                ordinal += 1
                footnote_id = f"cite-{(message.message_id or message.node_id)[:8]}-{ordinal:02d}"
                source = source or {}
                file_id = source.get("file_id")
                records.append(
                    CitationLinkRecord(
                        citation_link_id=f"{conversation.conversation_id}:{message.message_id or message.node_id}:citation-{ordinal:03d}",
                        conversation_id=conversation.conversation_id,
                        message_id=message.message_id,
                        node_id=message.node_id,
                        marker_original=marker,
                        marker_start=match.start(),
                        marker_end=match.end(),
                        footnote_id=footnote_id,
                        citation_family=family,
                        resolution_status="resolved" if source else "unresolved_marker",
                        title=source.get("title"),
                        url=source.get("url"),
                        file_id=file_id,
                        copied_pack_path=asset_paths.get(file_id),
                        line_start=target_line_start,
                        line_end=target_line_end,
                        snippet=source.get("snippet"),
                        proof_path=source.get("proof_path"),
                    )
                )
    return records


def replace_resolved_citation_markers(text: str, records: list[CitationLinkRecord]) -> str:
    """Keep the export marker visible while linking it to its source note.

    Obsidian's native footnotes replace a meaningful marker with an opaque
    superscript number.  A same-note block link retains ``【n†Lx-Ly】`` at the
    point of the claim and leads to the detailed source record below.
    """
    result = text
    grouped: dict[tuple[int, int, str], list[CitationLinkRecord]] = {}
    for record in records:
        if record.resolution_status == "resolved":
            grouped.setdefault((record.marker_start, record.marker_end, record.marker_original), []).append(record)
    for (marker_start, marker_end, marker_original), occurrences in sorted(
        grouped.items(),
        key=lambda item: item[0][0],
        reverse=True,
    ):
        if result[marker_start:marker_end] != marker_original:
            continue
        links = "".join(
            f"[[#^{citation_source_footnote_id(record)}|{record.marker_original}]]"
            for record in occurrences
        )
        result = f"{result[:marker_start]}{links}{result[marker_end:]}"
    return result


def citation_source_key(record: CitationLinkRecord) -> str:
    """Return a display-only source identity without discarding raw evidence.

    Every occurrence retains its own CitationLinkRecord.  This key is only for
    Markdown navigation, so repeated markers to an explicitly identical source
    point to one note instead of producing duplicate footnotes.
    """
    if record.url:
        return f"url:{_normalized_url_key(record.url)}"
    if record.file_id:
        return f"file_id:{record.file_id}"
    if record.copied_pack_path:
        return f"copied_path:{record.copied_pack_path}"
    return f"occurrence:{record.citation_link_id}"


def citation_source_footnote_id(record: CitationLinkRecord) -> str:
    digest = sha256(citation_source_key(record).encode("utf-8")).hexdigest()[:12]
    return f"cite-source-{digest}"


def _normalized_url_key(value: str) -> str:
    """Normalize only URL casing that cannot change the explicit resource."""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, parsed.fragment))


def _web_entries(message: MessageRecord) -> dict[tuple[int, int], dict[str, Any]]:
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for group_index, group in enumerate(metadata.get("search_result_groups") or []):
        if not isinstance(group, dict):
            continue
        for entry_index, entry in enumerate(group.get("entries") or []):
            if not isinstance(entry, dict) or not isinstance(entry.get("ref_id"), dict):
                continue
            ref_id = entry["ref_id"]
            turn = ref_id.get("turn_index")
            index = ref_id.get("ref_index")
            if not isinstance(turn, int) or not isinstance(index, int):
                continue
            result[(turn, index)] = {
                "title": entry.get("title") or group.get("domain"),
                "url": entry.get("url"),
                "snippet": entry.get("snippet"),
                "proof_path": f"mapping.{message.node_id}.message.metadata.search_result_groups[{group_index}].entries[{entry_index}]",
            }
    for reference_index, reference in enumerate(message.content_references):
        if not isinstance(reference, dict):
            continue
        for item_index, item in enumerate(reference.get("items") or []):
            if not isinstance(item, dict):
                continue
            for ref in item.get("refs") or []:
                if not isinstance(ref, dict):
                    continue
                turn = ref.get("turn_index")
                index = ref.get("ref_index")
                if not isinstance(turn, int) or not isinstance(index, int):
                    continue
                result[(turn, index)] = {
                    "title": item.get("title") or item.get("attribution"),
                    "url": item.get("url"),
                    "snippet": item.get("snippet"),
                    "proof_path": f"mapping.{message.node_id}.message.metadata.content_references[{reference_index}].items[{item_index}]",
                }
    return result


def _file_citations(message: MessageRecord) -> dict[tuple[int, int, int | None, int | None], dict[str, Any]]:
    result: dict[tuple[int, int, int | None, int | None], dict[str, Any]] = {}
    for citation_index, citation in enumerate(message.citations):
        if not isinstance(citation, dict) or not isinstance(citation.get("metadata"), dict):
            continue
        metadata = citation["metadata"]
        extra = metadata.get("extra") if isinstance(metadata.get("extra"), dict) else {}
        turn = extra.get("retrieval_turn")
        index = extra.get("retrieval_file_index")
        line_range = extra.get("line_range")
        if not isinstance(turn, int) or not isinstance(index, int):
            continue
        start = line_range[0] if isinstance(line_range, list) and len(line_range) == 2 and isinstance(line_range[0], int) else None
        end = line_range[1] if isinstance(line_range, list) and len(line_range) == 2 and isinstance(line_range[1], int) else None
        source = {
            "title": metadata.get("name") or metadata.get("title") or metadata.get("id"),
            "file_id": metadata.get("id"),
            "snippet": metadata.get("text"),
            "proof_path": f"mapping.{message.node_id}.message.metadata.citations[{citation_index}].metadata",
        }
        result[(turn, index, start, end)] = source
        result.setdefault((turn, index, None, None), source)
    return result


def _bracket_content_references(message: MessageRecord) -> list[dict[str, Any]]:
    """Return exact source records for tether-v4 markers (``【n†Lx-Ly】``).

    These markers use a local ordinal that has no stable relationship to a
    search turn. The export nevertheless supplies the exact rendered marker
    and source fields in ``content_references``, so this joins those rows
    rather than inferring a source from the ordinal.
    """
    result: list[dict[str, Any]] = []
    for reference_index, reference in enumerate(message.content_references):
        if not isinstance(reference, dict):
            continue
        marker = reference.get("matched_text")
        if not isinstance(marker, str) or not BRACKET_CITATION_RE.fullmatch(marker):
            continue
        url = reference.get("url")
        file_id = _file_id_from_reference_url(url)
        result.append(
            {
                "marker": marker,
                "marker_start": reference.get("start_idx"),
                "marker_end": reference.get("end_idx"),
                "title": reference.get("title") or reference.get("attribution"),
                "url": None if file_id else url,
                "file_id": file_id,
                "snippet": reference.get("snippet"),
                "proof_path": f"mapping.{message.node_id}.message.metadata.content_references[{reference_index}]",
            }
        )
    return result


def _select_bracket_reference(
    references: list[dict[str, Any]],
    marker: str,
    marker_start: int,
    marker_end: int,
) -> dict[str, Any] | None:
    exact = [
        reference
        for reference in references
        if reference["marker"] == marker
        and reference.get("marker_start") == marker_start
        and reference.get("marker_end") == marker_end
    ]
    if exact:
        return exact[0]
    one_based = [
        reference
        for reference in references
        if reference["marker"] == marker
        and reference.get("marker_start") == marker_start - 1
        and reference.get("marker_end") == marker_end - 1
    ]
    if one_based:
        return one_based[0]
    # Some legacy exports omit or misalign character offsets. Do not infer
    # from the bracket number; a unique exact marker is still defensible.
    candidates = [reference for reference in references if reference["marker"] == marker]
    return candidates[0] if len(candidates) == 1 else None


def _file_id_from_reference_url(url: Any) -> str | None:
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    file_id = url.removeprefix("file://").split("#", 1)[0]
    return file_id or None


def _line_range(value: str | None) -> tuple[int | None, int | None]:
    match = LINE_RANGE_RE.fullmatch(value or "")
    if not match:
        return None, None
    start = int(match.group("start"))
    return start, int(match.group("end") or start)
