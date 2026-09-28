from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable


METRICS = (
    "message_count",
    "file_reference_count",
    "unresolved_file_count",
    "source_evidence_count",
    "tool_evidence_count",
)


def build_dashboard(source: dict[str, Any], conversations: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    total = len(conversations)
    message_total = sum(row["message_count"] for row in conversations)
    file_total = sum(row["file_reference_count"] for row in conversations)
    resolved_total = sum(row["resolved_file_count"] for row in conversations)
    source_total = sum(row["source_evidence_count"] for row in conversations)
    tool_total = sum(row["tool_evidence_count"] for row in conversations)
    known_context = sum(row["context_evidence"] != "unknown" for row in conversations)
    locator_count = sum(row["note_path"] is not None for row in conversations)
    monthly_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in conversations:
        monthly_buckets[row["month_key"]].append(row)

    monthly = []
    for month_key in sorted(monthly_buckets):
        rows = monthly_buckets[month_key]
        monthly.append(
            {
                "month_key": month_key,
                "conversation_count": len(rows),
                "message_count": sum(row["message_count"] for row in rows),
                "needs_review_count": sum(row["needs_review"] for row in rows),
                "unresolved_file_count": sum(row["unresolved_file_count"] for row in rows),
                "source_evidence_count": sum(row["source_evidence_count"] for row in rows),
                "tool_evidence_count": sum(row["tool_evidence_count"] for row in rows),
            }
        )

    return {
        "source": source,
        "quality": {
            "conversation_count": total,
            "message_count": message_total,
            "file_reference_count": file_total,
            "source_evidence_count": source_total,
            "tool_evidence_count": tool_total,
            "needs_review_count": sum(row["needs_review"] for row in conversations),
            "has_warnings_count": sum(row["has_warnings"] for row in conversations),
            "unknown_context_count": total - known_context,
            "unresolved_conversation_count": sum(row["has_unresolved_files"] for row in conversations),
            "unresolved_file_count": sum(row["unresolved_file_count"] for row in conversations),
            "locator_unavailable_count": total - locator_count,
            "file_density": round(file_total / total, 6) if total else 0.0,
            "source_density": round(source_total / total, 6) if total else 0.0,
            "tool_density": round(tool_total / total, 6) if total else 0.0,
            "diagnostics": sorted(set(warnings)),
        },
        "coverage": {
            "context_known_ratio": ratio(known_context, total),
            "files_resolved_ratio": ratio(resolved_total, sum(row["unique_file_count"] for row in conversations)),
            "conversations_with_sources_ratio": ratio(sum(row["source_evidence_count"] > 0 for row in conversations), total),
            "conversations_with_tools_ratio": ratio(sum(row["tool_evidence_count"] > 0 for row in conversations), total),
            "locator_ratio": ratio(locator_count, total),
            "models": counter_values(conversations, "models_seen"),
            "projects": counter_values(conversations, "space_projects"),
            "gpts": counter_values(conversations, "gpts"),
            "knowledge_stores": counter_values(conversations, "knowledge_stores"),
            "archive": dict(sorted(Counter(str(row["is_archived"]).lower() for row in conversations).items())),
        },
        "monthly": monthly,
        "distributions": {metric: distribution(row[metric] for row in conversations) for metric in METRICS},
        "conversations": conversations,
    }


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def counter_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(value for row in rows for value in row[key])
    return dict(sorted(counts.items()))


def distribution(values: Iterable[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": 0, "max": 0, "mean": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0}
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 6),
        "p25": quantile(ordered, 0.25),
        "p50": quantile(ordered, 0.50),
        "p75": quantile(ordered, 0.75),
        "p90": quantile(ordered, 0.90),
        "p95": quantile(ordered, 0.95),
    }


def quantile(ordered: list[int], probability: float) -> float:
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 6)
