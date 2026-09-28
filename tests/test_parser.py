import json
import io
import os
import subprocess
import sys
import unittest
import zipfile
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CLI_ENV = os.environ.copy()
CLI_ENV["PYTHONPATH"] = str(SRC) + (os.pathsep + CLI_ENV["PYTHONPATH"] if CLI_ENV.get("PYTHONPATH") else "")

from openai_export_obsidian.archive import ExportArchive
from openai_export_obsidian.conversations import parse_conversation
from openai_export_obsidian.citation_links import CitationLinkRecord, replace_resolved_citation_markers
from openai_export_obsidian.markdown import (
    compact_snippet,
    render_citation_notes,
    render_compact_evidence_register,
    render_compact_message,
    render_native_footnotes,
    render_readable_message,
    rewrite_sandbox_references,
)
from openai_export_obsidian.models import AssetRecord, MessageRecord
from openai_export_obsidian.payload_candidates import UnverifiedPayloadCandidateAnalyzer
from openai_export_obsidian.reconstruction import MessageAnnotation, build_message_annotations, branch_infos
from openai_export_obsidian.runner import context_asset_rows, manifest_context_file_row, run_parse
from openai_export_obsidian.utils import as_iso, json_dumps, unique_filename, unique_note_name
from openai_export_obsidian.validation import validate_pack


def write_json(zf: zipfile.ZipFile, name: str, value) -> None:
    zf.writestr(name, json.dumps(value, ensure_ascii=False))


def build_fixture_export(
    path: Path,
    include_second: bool = False,
    include_snorlax_project: bool = False,
    include_side_branch: bool = False,
    include_message_gizmo_project: bool = False,
    include_generation_gap: bool = False,
    include_textdoc_gap: bool = False,
    include_textdoc_payload: bool = False,
    include_canvas_reference: bool = False,
    include_canmore_uri: bool = False,
    include_textdoc_proposal: bool = False,
    include_json_only_activity: bool = False,
    include_picture_v2_gap: bool = False,
    include_citation_footnotes: bool = False,
    include_many_tool_rows: bool = False,
    include_generated_knowledge_file: bool = False,
    include_runtime_artifact: bool = False,
    include_embedded_historical_export: bool = False,
    include_sandbox_reference: bool = False,
    include_same_name_payload_candidate: bool = False,
) -> None:
    conversation = {
        "id": "conv-1234567890abcdef",
        "title": "Forensic Parser Test",
        "create_time": 1_700_000_000.0,
        "update_time": 1_700_000_060.0,
        "conversation_template_id": "g-p-project-explicit",
        "gizmo_id": "g-custom-explicit",
        "default_model_slug": "gpt-4o",
        "voice": "cove",
        "is_archived": False,
        "is_starred": True,
        "is_do_not_remember": False,
        "current_node": "assistant-node",
        "mapping": {
            "root": {
                "id": "root",
                "message": None,
                "parent": None,
                "children": ["user-node"],
            },
            "user-node": {
                "id": "user-node",
                "parent": "root",
                "children": ["assistant-node"],
                "message": {
                    "id": "msg-user",
                    "author": {"role": "user", "metadata": {}},
                    "create_time": 1_700_000_001.0,
                    "update_time": None,
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [
                            {
                                "content_type": "image_asset_pointer",
                                "asset_pointer": "file-service://file-found",
                                "height": 10,
                                "width": 20,
                                "size_bytes": 99,
                            },
                            "Please inspect this.",
                        ],
                    },
                    "metadata": {
                        "attachments": [
                            {
                                "id": "file-found",
                                "name": "Original Found.png",
                                "mime_type": "image/png",
                                "size": 99,
                                "width": 20,
                                "height": 10,
                            },
                            {
                                "id": "file-missing",
                                "name": "Logical Found.png" if include_same_name_payload_candidate else "Missing Source.pdf",
                                "mime_type": "image/png" if include_same_name_payload_candidate else "application/pdf",
                            },
                        ]
                    },
                    "status": "finished_successfully",
                    "end_turn": None,
                },
            },
            "assistant-node": {
                "id": "assistant-node",
                "parent": "user-node",
                "children": [],
                "message": {
                    "id": "msg-assistant",
                    "author": {"role": "assistant", "metadata": {}},
                    "create_time": 1_700_000_002.0,
                    "content": {"content_type": "text", "parts": ["Done."]},
                    "metadata": {
                        "model_slug": "gpt-4o-mini",
                        "code_blocks": {
                            "0": {"id": "code-1", "previewable": False},
                        },
                        "search_result_groups": [
                            {
                                "domain": "example.com",
                                "entries": [
                                    {
                                        "title": "Example Search Result",
                                        "url": "https://example.com/result",
                                        "snippet": "Search result snippet.",
                                        "type": "search_result",
                                    }
                                ],
                            }
                        ],
                        "content_references": [
                            {
                                "type": "grouped_webpages",
                                "items": [
                                    {
                                        "title": "Example Reference",
                                        "url": "https://example.com/reference",
                                        "snippet": "Reference snippet.",
                                    }
                                ],
                            }
                        ],
                    },
                    "status": "finished_successfully",
                    "end_turn": True,
                },
            },
        },
    }
    if include_generation_gap:
        conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
            "Done. Ça a généré un triptyque avec les 3 directions côte à côte."
        ]
        conversation["mapping"]["assistant-node"]["message"]["metadata"]["image_results"] = []
        conversation["mapping"]["assistant-node"]["message"]["metadata"]["image_gen_async"] = False
    if include_textdoc_gap:
        conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
            "C’est fait 🌿 Le fichier **`TECH_TRACE_2025-07-13_TheorieActivationJardin.md`** est ouvert dans le Jardin vivant."
        ]
    if include_textdoc_payload:
        conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
            "C’est fait 🌿 Le fichier **`TECH_TRACE_2025-07-13_TheorieActivationJardin.md`** est ouvert dans le Jardin vivant."
        ]
        conversation["mapping"]["assistant-node"]["message"]["metadata"]["canvas"] = {
            "user_created_textdocs": [
                {
                    "content": "# TheorieActivationJardin\n\nPayload exported by ChatGPT.",
                    "title": "TECH_TRACE_2025-07-13_TheorieActivationJardin.md",
                    "textdoc_id": "td-123",
                    "type": "document",
                }
            ]
        }
    if include_canvas_reference:
        if not include_textdoc_gap and not include_textdoc_payload:
            conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
                "Le canevas est ouvert dans l’éditeur."
            ]
        conversation["mapping"]["assistant-node"]["message"]["metadata"]["open_in_canvas_view"] = {
            "id": "td-ref-456",
            "type": "canvas_textdoc",
        }
    if include_canmore_uri:
        conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
            "Voici le document actif : [TECH TRACE](canmore://textdoc/canmore-789)"
        ]
    if include_textdoc_proposal:
        conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
            "Tu veux que je l’ouvre maintenant dans un canevas avec ce nom précis ? `TECH_TRACE_2025-07-13_TheorieActivationJardin.md`"
        ]
    if include_sandbox_reference:
        conversation["mapping"]["assistant-node"]["message"]["content"]["parts"] = [
            "[Download generated result](sandbox:/mnt/data/result.png)"
        ]
    if include_json_only_activity:
        conversation["mapping"]["assistant-node"]["message"]["metadata"]["thoughts"] = [
            {"summary": "Internal reasoning recap preserved by export."}
        ]
    if include_many_tool_rows:
        conversation["mapping"]["assistant-node"]["message"]["metadata"]["search_result_groups"] = [
            {
                "domain": f"example{i}.com",
                "entries": [
                    {
                        "title": f"Example Search Result {i}",
                        "url": f"https://example{i}.com/result",
                        "snippet": f"Search result snippet {i}.",
                        "type": "search_result",
                    }
                ],
            }
            for i in range(7)
        ]
    if include_picture_v2_gap:
        conversation["mapping"]["user-node"]["message"]["metadata"].update(
            {
                "image_gen_async": False,
                "image_results": [],
                "image_send_uuid": "image-send-123",
                "system_hints": ["picture_v2"],
                "trigger_async_ux": False,
            }
        )
    if include_citation_footnotes:
        assistant = conversation["mapping"]["assistant-node"]["message"]
        citation_text = (
            "Market evidence. citeturn0search0\n"
            "File evidence. fileciteturn1file0L3-L4\n"
            "Research evidence. 【31†L27-L35】\n"
            "Composite evidence. citeturn2search0turn2search1\n"
            "Unresolved evidence. citeturn9search9"
        )
        assistant["content"]["parts"] = [citation_text]
        assistant["metadata"]["search_result_groups"] = [
            {
                "domain": "example.com",
                "entries": [
                    {
                        "ref_id": {"turn_index": 0, "ref_index": 0, "ref_type": "search"},
                        "title": "Fixture web result",
                        "url": "https://example.com/search-result",
                        "snippet": "Fixture web evidence.",
                        "type": "search_result",
                    },
                    {
                        "ref_id": {"turn_index": 2, "ref_index": 0, "ref_type": "search"},
                        "title": "Composite source A",
                        "url": "https://example.com/composite-a",
                        "snippet": "Composite evidence A.",
                        "type": "search_result",
                    },
                    {
                        "ref_id": {"turn_index": 2, "ref_index": 1, "ref_type": "search"},
                        "title": "Composite source B",
                        "url": "https://example.com/composite-b",
                        "snippet": "Composite evidence B.",
                        "type": "search_result",
                    },
                ],
            }
        ]
        assistant["metadata"]["citations"] = [
            {
                "citation_format_type": "berry_file_search",
                "start_ix": 0,
                "end_ix": 0,
                "metadata": {
                    "id": "file-missing",
                    "name": "Missing Source.pdf",
                    "text": "Fixture file evidence.",
                    "type": "file",
                    "extra": {"retrieval_turn": 1, "retrieval_file_index": 0, "line_range": [3, 4]},
                },
            }
        ]
        assistant["metadata"]["content_references"] = [
            {
                "matched_text": "【31†L27-L35】",
                # The real tether-v4 export uses one-based offsets.
                "start_idx": citation_text.index("【31†L27-L35】") - 1,
                "end_idx": citation_text.index("【31†L27-L35】") + len("【31†L27-L35】") - 1,
                "title": "Fixture tether result",
                "url": "https://example.com/tether-result",
                "snippet": "Fixture tether evidence.",
                "type": "webpage_extended",
            }
        ]
    if include_side_branch:
        conversation["mapping"]["user-node"]["children"].append("side-assistant-node")
        conversation["mapping"]["side-assistant-node"] = {
            "id": "side-assistant-node",
            "parent": "user-node",
            "children": [],
            "message": {
                "id": "msg-side-assistant",
                "author": {"role": "assistant", "metadata": {}},
                "create_time": 1_700_000_003.0,
                "content": {"content_type": "text", "parts": ["Side branch answer."]},
                "metadata": {
                    "model_slug": "gpt-4o-side",
                    "tool_result": {"name": "side-tool", "status": "ok"},
                    "search_result_groups": [
                        {
                            "domain": "side.example",
                            "entries": [
                                {
                                    "title": "Side Search Result",
                                    "url": "https://side.example/result",
                                    "snippet": "Side branch result.",
                                }
                            ],
                        }
                    ],
                    "conversation_context_citation_metadata": [
                        {
                            "citation_uuid": "side-citation",
                            "citation": {
                                "attribution": "Past chat",
                                "conversation_context_type": "past_conversation",
                                "conversation_title": "Side Past Chat",
                                "snippet": "Side branch memory.",
                                "url": "/c/conv-side-past",
                            },
                        }
                    ],
                },
                "status": "finished_successfully",
                "end_turn": True,
            },
        }
    conversations = [conversation]
    if include_second:
        second = dict(conversation)
        second["id"] = "conv-second"
        second["title"] = "Second Conversation"
        second["mapping"] = {
            "node": {
                "id": "node",
                "message": {
                    "id": "msg-second",
                    "author": {"role": "user", "metadata": {}},
                    "content": {"content_type": "text", "parts": ["Second."]},
                    "metadata": {},
                },
                "parent": None,
                "children": [],
            }
        }
        second["current_node"] = "node"
        conversations.append(second)
    if include_snorlax_project:
        project = dict(conversation)
        project["id"] = "conv-snorlax-project"
        project["conversation_id"] = "conv-snorlax-project"
        project["title"] = "Snorlax Project Conversation"
        project["create_time"] = 1_700_000_120.0
        project["update_time"] = 1_700_000_180.0
        project["conversation_template_id"] = "g-p-project-context"
        project["gizmo_type"] = "snorlax"
        project.pop("gizmo_id", None)
        project["mapping"] = {
            "project-user": {
                "id": "project-user",
                "message": {
                    "id": "msg-project-user",
                    "author": {"role": "user", "metadata": {}},
                    "create_time": 1_700_000_121.0,
                    "content": {"content_type": "text", "parts": ["Project question."]},
                    "metadata": {},
                },
                "parent": None,
                "children": ["project-assistant"],
            },
            "project-assistant": {
                "id": "project-assistant",
                "message": {
                    "id": "msg-project-assistant",
                    "author": {"role": "assistant", "metadata": {}},
                    "create_time": 1_700_000_122.0,
                    "content": {"content_type": "text", "parts": ["Project answer."]},
                    "metadata": {
                        "gizmo_id": "g-p-project-context",
                        "model_slug": "gpt-5-3-instant",
                        "conversation_context_citation_metadata": [
                            {
                                "citation_uuid": "citation-past-conv",
                                "citation": {
                                    "attribution": "Past chat",
                                    "conversation_context_type": "past_conversation",
                                    "conversation_title": "Second Conversation",
                                    "snippet": "A useful prior detail.",
                                    "url": "/c/conv-second",
                                    "pub_date": 1_700_000_060.0,
                                },
                            }
                        ],
                    },
                },
                "parent": "project-user",
                "children": [],
            },
        }
        project["current_node"] = "project-assistant"
        conversations.append(project)
    if include_message_gizmo_project:
        message_project = dict(conversation)
        message_project["id"] = "conv-message-gizmo-project"
        message_project["conversation_id"] = "conv-message-gizmo-project"
        message_project["title"] = "Message Gizmo Project Conversation"
        message_project.pop("conversation_template_id", None)
        message_project.pop("gizmo_id", None)
        message_project.pop("gizmo_type", None)
        message_project["mapping"] = {
            "project-user": {
                "id": "project-user",
                "message": {
                    "id": "msg-message-project-user",
                    "author": {"role": "user", "metadata": {}},
                    "create_time": 1_700_000_181.0,
                    "content": {"content_type": "text", "parts": ["Project through message metadata."]},
                    "metadata": {},
                },
                "parent": None,
                "children": ["project-assistant"],
            },
            "project-assistant": {
                "id": "project-assistant",
                "message": {
                    "id": "msg-message-project-assistant",
                    "author": {"role": "assistant", "metadata": {}},
                    "create_time": 1_700_000_182.0,
                    "content": {"content_type": "text", "parts": ["Project answer."]},
                    "metadata": {
                        "gizmo_id": "g-p-message-project",
                        "model_slug": "gpt-5-3-instant",
                    },
                },
                "parent": "project-user",
                "children": [],
            },
        }
        message_project["current_node"] = "project-assistant"
        conversations.append(message_project)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
        if include_runtime_artifact:
            execution_id = "runtime-execution-node"
            outer.writestr(
                "chat.html",
                "<html><script>var jsonData = "
                + json.dumps(
                    [
                        {
                            "id": "conv-1234567890abcdef",
                            "conversation_id": "conv-1234567890abcdef",
                            "mapping": {
                                execution_id: {
                                    "id": execution_id,
                                    "parent": "assistant-node",
                                    "message": {
                                        "id": execution_id,
                                        "author": {"role": "assistant"},
                                        "recipient": "python",
                                        "content": {"content_type": "code", "text": "result.to_csv('runtime.csv')"},
                                    },
                                }
                            },
                        }
                    ]
                )
                + ";</script></html>",
            )
        nested_path = path.parent / "conversation-inner.zip"
        with zipfile.ZipFile(nested_path, "w", zipfile.ZIP_DEFLATED) as inner:
            write_json(inner, "conversation_asset_file_names.json", {"file-found.dat": "Logical Found.png"})
            library_files = [
                    {
                        "file_id": "file-found",
                        "file_name": "Library Found.png",
                        "mime_type": "image/png",
                        "file_size_bytes": 99,
                        "origination_thread_id": "conv-1234567890abcdef",
                        "origination_message_id": "msg-user",
                    },
                    {
                        "file_id": "file_00000000kbsource",
                        "file_name": "KB Guide.md",
                        "mime_type": "text/markdown",
                        "file_size_bytes": 42,
                        "knowledge_store_id": {"id": "iks_testknowledge", "kind": "id"},
                        "gizmo_id": "g-custom-explicit",
                    },
                    {
                        "file_id": "file_00000000globalkb",
                        "file_name": "Global KB.md",
                        "mime_type": "text/markdown",
                        "file_size_bytes": 50,
                        "knowledge_store_id": {"id": "iks_global", "kind": "id"},
                        "gizmo_id": "g-other-explicit",
                    },
                ]
            if include_generated_knowledge_file:
                library_files.append({
                        "file_id": "file-generated-library",
                        "file_name": "Generated Knowledge Image.png",
                        "mime_type": "image/png",
                        "file_size_bytes": 21,
                        "knowledge_store_id": {"id": "iks_testknowledge", "kind": "id"},
                        "image_gen_generation_id": "s_generated_fixture",
                        "origination_thread_id": "conv-1234567890abcdef",
                        "origination_message_id": "msg-assistant",
                        "created_at": "2023-11-14T22:15:01+00:00",
                        "id": {"id": "libfile_generated_fixture"},
                    })
            write_json(inner, "library_files.json", library_files)
            write_json(inner, "shared_conversations.json", [{"conversation_id": "conv-1234567890abcdef", "id": "share-1"}])
            write_json(inner, "conversations-000.json", conversations)
            inner.writestr("file-found.dat", b"asset-bytes")
            inner.writestr("file_00000000kbsource.dat", b"# KB\nknowledge")
            inner.writestr("file_00000000globalkb.dat", b"# Global KB\nknowledge")
            if include_generated_knowledge_file:
                inner.writestr("personal/files/Generated Knowledge Image.png", b"generated-image-bytes")
            if include_runtime_artifact:
                inner.writestr(
                    "personal/files/conv-1234567890abcdef/runtime-execution-node/mnt/data/runtime.csv",
                    b"column\nvalue\n",
                )
            if include_sandbox_reference:
                inner.writestr("personal/files/session/mnt/data/result.png", b"sandbox-image-bytes")
            if include_embedded_historical_export:
                historical_bytes = io.BytesIO()
                with zipfile.ZipFile(historical_bytes, "w", zipfile.ZIP_DEFLATED) as historical:
                    historic = json.loads(json.dumps(conversation))
                    historic["mapping"]["historic-python-node"] = {
                        "id": "historic-python-node",
                        "parent": "assistant-node",
                        "children": [],
                        "message": {
                            "id": "historic-python-node",
                            "author": {"role": "assistant"},
                            "recipient": "python",
                            "content": {"content_type": "code", "text": "open('/mnt/data/old.csv')"},
                            "metadata": {"image_gen_generation_id": "gen-historic-fixture", "canmore_id": "textdoc-historic"},
                        },
                    }
                    historical.writestr("conversations.json", json.dumps([historic]))
                    historical.writestr("chat.html", "<html>historical fixture</html>")
                inner.writestr("file-historical.dat", historical_bytes.getvalue())
            inner.writestr("file-orphan.dat", b"orphan-bytes")
            inner.writestr("personal/files/table-export.csv", b"col\nvalue\n")
        outer.write(nested_path, "User Online Activity/Conversations__fixture-part-0001.zip")
        nested_path.unlink()


def build_materialization_fixture_export(path: Path) -> dict[str, bytes]:
    """Create one parse fixture covering linked and unlinked payload naming."""
    png = b"\x89PNG\r\n\x1a\nfixture-png"
    jpeg = b"\xff\xd8\xfffixture-jpeg"
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24
    payloads = {
        "file-linked.dat": png + b"-linked",
        "file-linked-duplicate.dat": png + b"-linked",
        "file-unlinked.dat": png + b"-unlinked",
        "file-wav.dat": wav,
        "file-signature.dat": jpeg,
        "personal/files/000000123456789": png + b"-personal-extensionless",
        "personal/files/file-noextension": png + b"-linked-extensionless",
        "file-collision-a.dat": png + b"-collision-a",
        "file-collision-b.dat": png + b"-collision-b",
        "file-conflict.dat": jpeg + b"-conflict",
        "file-long.dat": png + b"-long",
        "file-unknown.dat": b"\x00\x01\x02unknown",
        "personal/files/linked-duplicate.png": png + b"-linked",
    }
    conversation = {
        "id": "conv-materialization",
        "title": "Materialization fixture",
        "create_time": 1_700_000_000.0,
        "update_time": 1_700_000_001.0,
        "current_node": "user-node",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["user-node"]},
            "user-node": {
                "id": "user-node",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "msg-materialization",
                    "author": {"role": "user", "metadata": {}},
                    "create_time": 1_700_000_001.0,
                    "content": {"content_type": "text", "parts": ["payloads"]},
                    "metadata": {
                        "attachments": [
                            {"id": "file-linked", "name": "Linked Source.png", "mime_type": "image/png"},
                            {"id": "file-linked-duplicate", "name": "Linked duplicate source.png", "mime_type": "image/png"},
                            {"id": "file-noextension", "name": "Linked no extension.png", "mime_type": "image/png"},
                        ]
                    },
                },
            },
        },
    }
    filename_map = {
        "file-linked.dat": "Linked logical.png",
        "file-unlinked.dat": "Unlinked logical.png",
        "file-wav.dat": "conversation/audio/logical-audio.wav",
        "file-noextension": "Linked no extension.png",
        "file-collision-a.dat": "same logical.png",
        "file-collision-b.dat": "same logical.png",
        "file-conflict.dat": "mapping-wins.png",
        "file-long.dat": f"{'a' * 220}.png",
    }
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as inner:
        write_json(inner, "conversation_asset_file_names.json", filename_map)
        write_json(inner, "conversations-000.json", [conversation])
        for member_path, payload in payloads.items():
            if not member_path.startswith("personal/files/"):
                inner.writestr(member_path, payload)
    files_nested = io.BytesIO()
    with zipfile.ZipFile(files_nested, "w", zipfile.ZIP_DEFLATED) as inner:
        for member_path, payload in payloads.items():
            if member_path.startswith("personal/files/"):
                inner.writestr(member_path, payload)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("User Online Activity/Conversations__materialization.zip", nested.getvalue())
        outer.writestr("User Online Activity/Files__materialization.zip", files_nested.getvalue())
    return payloads


class ParserTests(unittest.TestCase):
    def test_project_uses_src_package_cli_layout(self):
        package_dir = ROOT / "src" / "openai_export_obsidian"
        self.assertTrue((ROOT / "pyproject.toml").exists())
        self.assertTrue(package_dir.exists())
        self.assertTrue((package_dir / "__main__.py").exists())
        self.assertTrue((package_dir / "cli.py").exists())
        self.assertFalse((ROOT / "parse_export.py").exists())
        self.assertFalse((ROOT / "query_evidence.py").exists())
        self.assertFalse((ROOT / "validate_pack.py").exists())

        completed = subprocess.run(
            [sys.executable, "-m", "openai_export_obsidian", "--help"],
            cwd=ROOT,
            env=CLI_ENV,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("parse", completed.stdout)
        self.assertIn("validate", completed.stdout)
        self.assertIn("query", completed.stdout)

    def test_millisecond_timestamps_are_normalized_without_dropping_records(self):
        self.assertEqual(as_iso(1_700_000_000_000), "2023-11-14T22:13:20+00:00")

    def test_jsonl_dumps_escapes_unicode_line_separators(self):
        dumped = json_dumps({"text": "one\u2028two\u2029three\x85four"})
        self.assertEqual(len(dumped.splitlines()), 1)
        self.assertIn("\\u2028", dumped)
        self.assertIn("\\u2029", dumped)
        self.assertIn("\\u0085", dumped)
        self.assertEqual(json.loads(dumped)["text"], "one\u2028two\u2029three\x85four")

    def test_unique_names_reserve_case_insensitive_filesystem_variants(self):
        note_names: set[str] = set()
        self.assertEqual(unique_note_name("Stabilisation du vault.md", note_names), "Stabilisation du vault.md")
        self.assertEqual(unique_note_name("Stabilisation du Vault.md", note_names), "Stabilisation du Vault (2).md")

        file_names: set[str] = set()
        self.assertEqual(unique_filename("Image.PNG", file_names), "Image.PNG")
        self.assertEqual(unique_filename("image.png", file_names), "image--2.png")

    def test_archive_reads_nested_conversation_shards_and_assets(self):
        with TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "export.zip"
            build_fixture_export(export_path, include_generated_knowledge_file=True)

            archive = ExportArchive(export_path)
            shard_names = [member.name for member in archive.find_members(lambda m: m.name == "conversations-000.json")]
            asset_names = [member.basename for member in archive.physical_asset_members()]

            self.assertEqual(shard_names, ["conversations-000.json"])
            self.assertIn("file-found.dat", asset_names)
            self.assertIn("file-orphan.dat", asset_names)
            self.assertIn("table-export.csv", asset_names)

    def test_run_parse_preserves_asset_proofs_and_missing_assets(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            result = run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                dry_run=False,
                markdown_profile="forensic",
            )

            self.assertEqual(result["conversation_count"], 1)
            assets = [json.loads(line) for line in (output_path / "assets_index.jsonl").read_text(encoding="utf-8").splitlines()]
            statuses = {asset["raw_file_id"]: asset["asset_status"] for asset in assets}
            self.assertEqual(statuses["file-found"], "found")
            self.assertEqual(statuses["file-missing"], "missing")
            found_asset = next(asset for asset in assets if asset["raw_file_id"] == "file-found")
            self.assertEqual(found_asset["proof_path"], "mapping.user-node.message.metadata.attachments[0]")
            self.assertEqual(found_asset["origin_classification"], "user")
            self.assertEqual(found_asset["origin_confidence"], "explicit")
            self.assertTrue(found_asset["copied_relative_path"].endswith("Logical Found.png"))
            self.assertTrue((output_path / found_asset["copied_relative_path"]).exists())

            audit = json.loads((output_path / "parse_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["counts"]["missing_file_references"], 1)
            orphan_rows = [
                json.loads(line)
                for line in (output_path / "orphan_assets.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(audit["orphan_asset_count"], len(orphan_rows))
            self.assertIn("file-orphan.dat", {item["basename"] for item in orphan_rows})

            conv_dirs = list((output_path / "conversations").iterdir())
            self.assertEqual(len(conv_dirs), 1)
            note = (conv_dirs[0] / "conversation.md").read_text(encoding="utf-8")
            self.assertIn("Forensic Parser Test", note)
            self.assertIn("mapping.user-node.message.metadata.attachments[0]", note)
            self.assertIn("(assets/Logical Found.png)", note)

    def test_runtime_artifact_is_confirmed_copied_and_rendered_from_structural_path(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_runtime_artifact=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")

            evidence = output_path / "90_Evidence"
            runtime_rows = [
                json.loads(line)
                for line in (evidence / "runtime_artifacts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(runtime_rows), 1)
            runtime = runtime_rows[0]
            self.assertEqual(runtime["relation_status"], "confirmed_by_chat_html")
            self.assertEqual(runtime["execution_message_id"], "runtime-execution-node")
            self.assertEqual(runtime["execution_recipient"], "python")
            self.assertTrue((output_path / runtime["copied_pack_path"]).exists())

            note = (
                output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Runtime artifacts: 1", note)
            self.assertIn("## Python Runtime Artifacts", note)
            self.assertIn("%% openai_message_id: runtime-execution-node %%", note)
            self.assertIn("`/mnt/data/runtime.csv`", note)

            unlinked = (evidence / "unlinked_assets.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("runtime-execution-node/mnt/data/runtime.csv", unlinked)
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_embedded_historical_export_emits_separate_evidence_without_primary_merge(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_embedded_historical_export=True)

            run_parse(export_path, output_path, emit_json=True, emit_markdown=True, markdown_profile="readable")

            evidence = output_path / "90_Evidence"
            comparisons = [json.loads(line) for line in (evidence / "historical_conversation_comparisons.jsonl").read_text(encoding="utf-8").splitlines()]
            messages = [json.loads(line) for line in (evidence / "historical_messages.jsonl").read_text(encoding="utf-8").splitlines()]
            signals = [json.loads(line) for line in (evidence / "historical_signals.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(comparisons), 1)
            self.assertEqual(comparisons[0]["historical_only_message_count"], 1)
            self.assertIn("historic-python-node", {row["node_id"] for row in messages})
            self.assertIn("image_gen", {row["signal_kind"] for row in signals})
            primary_messages = (evidence / "messages.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("historic-python-node", primary_messages)
            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(encoding="utf-8")
            self.assertIn("## Historical Export Evidence", note)
            self.assertIn("not merged", note)
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_technical_events_are_emitted_without_merging_or_resolving_payloads(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_embedded_historical_export=True)

            run_parse(export_path, output_path, emit_json=True, emit_markdown=True, markdown_profile="readable")

            evidence = output_path / "90_Evidence"
            events = [json.loads(line) for line in (evidence / "technical_events.jsonl").read_text(encoding="utf-8").splitlines()]
            comparisons = [json.loads(line) for line in (evidence / "technical_event_comparisons.jsonl").read_text(encoding="utf-8").splitlines()]
            summary = json.loads((evidence / "technical_event_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(events)
            self.assertTrue(any(row["source_export"] == "historical_embedded_json" for row in events))
            self.assertTrue(any(row["event_family"] == "runtime_execution" for row in events))
            self.assertTrue(all(row["observation_status"] == "observed_no_physical_resolution" for row in events))
            self.assertTrue(all("physical_archive_path" not in row for row in events))
            self.assertTrue(all(row["technical_event_id"].startswith("technical-event:") for row in events))
            self.assertTrue(comparisons)
            self.assertEqual(summary["contract"], {"physical_resolution_called": False, "messages_merged": False, "candidate_edges_created": False})
            primary_messages = (evidence / "messages.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("historic-python-node", primary_messages)
            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(encoding="utf-8")
            self.assertIn("## Technical Event Evidence", note)
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_readable_profile_emits_v1_obsidian_pack(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            result = run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=True,
                emit_dataview=True,
                dry_run=False,
                markdown_profile="readable",
            )

            self.assertEqual(result["conversation_count"], 1)
            conversation_note = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            self.assertTrue(conversation_note.exists())
            self.assertFalse((output_path / "conversations").exists())

            note = conversation_note.read_text(encoding="utf-8")
            self.assertIn("type: openai_conversation", note)
            self.assertIn("created_at: 2023-11-14T22:13", note)
            self.assertIn("updated_at: 2023-11-14T22:14", note)
            self.assertNotIn("created_at: 2023-11-14T22:13:20", note)
            self.assertNotIn("updated_at: 2023-11-14T22:14:20", note)
            self.assertNotIn("\nyear:", note)
            self.assertNotIn("\nmonth:", note)
            self.assertNotIn("\nmonth_key:", note)
            self.assertIn("message_count: 2", note)
            self.assertIn("conversation_id: conv-1234567890abcdef", note)
            self.assertNotIn("proof_key:", note)
            self.assertIn("reviewed: false", note)
            self.assertIn("default_model: gpt-4o", note)
            self.assertIn("models_seen:", note)
            self.assertIn("models_seen:\n  - gpt-4o-mini", note)
            self.assertNotIn("\nmemory_scope:", note)
            self.assertNotIn("\nvoice:", note)
            self.assertNotIn("\nis_archived:", note)
            self.assertNotIn("\nis_starred:", note)
            self.assertNotIn("\nis_do_not_remember:", note)
            self.assertIn("Files: 2 unique · 1 resolved · 1 unresolved · 3 references", note)
            self.assertIn("unique_file_count: 2", note)
            self.assertIn("Sources: 4 evidence rows", note)
            self.assertIn("Tools & retrieval: 2 evidence rows", note)
            self.assertIn("- GPT: [[GPT - g-custom-explicit]]", note)
            self.assertNotIn("vault_projects:", note)
            self.assertNotIn("processes:", note)
            self.assertNotIn("has_gpt:", note)
            self.assertIn("## Manual Review", note)
            self.assertIn("### Summary", note)
            self.assertIn("### Restart Notes", note)
            self.assertIn("### Links", note)
            self.assertIn("<!-- BEGIN GENERATED OPENAI CONVERSATION -->", note)
            self.assertIn("<!-- END GENERATED OPENAI CONVERSATION -->", note)
            self.assertIn("## Full Conversation", note)
            self.assertIn("%% openai_message_id: msg-user %%\n> [!question] User · 2023-11-14 22:13:21 UTC", note)
            self.assertIn("%% openai_message_id: msg-assistant %%\n> [!success] Assistant · 2023-11-14 22:13:22 UTC · Model: gpt-4o-mini", note)
            self.assertIn("> [!question] User · 2023-11-14 22:13:21 UTC", note)
            self.assertIn("> [!success] Assistant · 2023-11-14 22:13:22 UTC · Model: gpt-4o-mini", note)
            self.assertNotIn("msg-user`", note)
            self.assertIn("## Evidence Summary", note)
            self.assertIn("- Files: 2 unique · 1 resolved · 1 unresolved · 3 references", note)
            self.assertIn("- Sources: 4 evidence rows", note)
            self.assertIn("- Tools & retrieval: 2 evidence rows", note)
            self.assertIn("- File references are raw mentions; unique/resolved/unresolved count deduplicated files.", note)
            self.assertIn(
                "- Source and tool/retrieval counts are JSONL evidence rows, not distinct files or distinct tools.",
                note,
            )
            self.assertIn("## Files", note)
            self.assertIn("## Sources", note)
            self.assertIn("## Tools & Retrieval", note)
            self.assertIn("- Web search groups: 1", note)
            self.assertIn("- Web references: 1", note)
            self.assertIn("many rows can come from one assistant answer", note)
            self.assertIn("### Tool Evidence Preview", note)
            self.assertIn("- web_search · example.com", note)
            self.assertIn("- content_reference · Example Reference", note)
            self.assertIn("## Export Metadata", note)
            self.assertIn("- Memory scope: `unknown`", note)
            self.assertIn("- Voice: `cove`", note)
            self.assertIn("- Archived: `false`", note)
            self.assertIn("- Starred: `true`", note)
            self.assertIn("- Do not remember: `false`", note)
            self.assertNotIn("## Linked Files", note)
            self.assertNotIn("## Context Evidence", note)
            self.assertNotIn("## Sources by Message", note)
            self.assertIn("Canonical machine evidence for `conversation_id: conv-1234567890abcdef`", note)
            self.assertIn("![[Logical Found - file-found.png|300]]", note)
            self.assertIn("`Missing Source.pdf`", note)

            self.assertTrue((output_path / "20_Files" / "Attachments" / "2023" / "11" / "Logical Found - file-found.png").exists())
            self.assertTrue((output_path / "20_Files" / "Knowledge" / "KB Guide - file_00000000kbsource.md").exists())
            self.assertTrue((output_path / "30_Contexts" / "GPTs" / "GPT - g-custom-explicit.md").exists())
            self.assertTrue((output_path / "30_Contexts" / "Knowledge" / "Knowledge Store - iks_testknowledge.md").exists())
            self.assertTrue((output_path / "40_Views" / "Contexts.base").exists())
            self.assertTrue((output_path / "40_Views" / "Needs Context Naming.base").exists())
            self.assertTrue((output_path / "40_Views" / "Conversations - Dataview.md").exists())
            self.assertTrue((output_path / "40_Views" / "Review - Dataview.md").exists())
            self.assertTrue((output_path / "40_Views" / "Needs File Review - Dataview.md").exists())
            self.assertTrue((output_path / "40_Views" / "Source Heavy Conversations - Dataview.md").exists())

            home = (output_path / "00_Home.md").read_text(encoding="utf-8")
            self.assertIn("## Overview", home)
            self.assertIn("## Start Here", home)
            self.assertIn("Needs Context Naming", home)
            self.assertIn("Needs File Review", home)
            self.assertIn("Source Heavy Conversations", home)
            self.assertIn("Manual sections are safe to edit.", home)

            review_view = (output_path / "40_Views" / "Review - Dataview.md").read_text(encoding="utf-8")
            self.assertIn("reviewed = false", review_view)

            conversations_view = (output_path / "40_Views" / "Conversations - Dataview.md").read_text(encoding="utf-8")
            contexts_base = (output_path / "40_Views" / "Contexts.base").read_text(encoding="utf-8")
            self.assertIn('type = "openai_conversation"', conversations_view)
            self.assertIn('type == "openai_context"', contexts_base)
            self.assertNotIn("file.inFolder", conversations_view)
            self.assertNotIn("file.inFolder", contexts_base)
            self.assertNotIn("file.inFolder", review_view)

            naming_base = (output_path / "40_Views" / "Needs Context Naming.base").read_text(encoding="utf-8")
            self.assertIn('type == "openai_context"', naming_base)
            self.assertIn("aliases", naming_base)
            self.assertIn("conversation_count", naming_base)
            self.assertNotIn("file.inFolder", naming_base)

            file_review_view = (output_path / "40_Views" / "Needs File Review - Dataview.md").read_text(encoding="utf-8")
            self.assertIn("unresolved_file_count", file_review_view)
            self.assertIn("file_reference_count", file_review_view)
            self.assertIn("space_projects", file_review_view)
            self.assertNotIn("file.inFolder", file_review_view)

            source_heavy_view = (output_path / "40_Views" / "Source Heavy Conversations - Dataview.md").read_text(encoding="utf-8")
            self.assertIn("source_evidence_count", source_heavy_view)
            self.assertIn("tool_evidence_count", source_heavy_view)
            self.assertIn("knowledge_stores", source_heavy_view)
            self.assertNotIn("file.inFolder", source_heavy_view)

            conversations_dataview = (output_path / "40_Views" / "Conversations - Dataview.md").read_text(
                encoding="utf-8"
            )
            contexts_dataview = (output_path / "40_Views" / "Contexts - Dataview.md").read_text(encoding="utf-8")
            self.assertIn('WHERE type = "openai_conversation"', conversations_dataview)
            self.assertIn('WHERE type = "openai_context"', contexts_dataview)
            self.assertNotIn('FROM "10_Conversations"', conversations_dataview)
            self.assertNotIn('FROM "30_Contexts"', contexts_dataview)

            proof_dir = output_path / "90_Evidence"
            for name in [
                "conversations.jsonl",
                "messages.jsonl",
                "message_sources.jsonl",
                "asset_links.jsonl",
                "context_links.jsonl",
                "file_manifest.jsonl",
                "tool_events.jsonl",
                "unlinked_assets.jsonl",
                "parse_audit.json",
            ]:
                self.assertTrue((proof_dir / name).exists(), name)

            file_manifest = [
                json.loads(line)
                for line in (proof_dir / "file_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            kb = next(row for row in file_manifest if row.get("file_id") == "file_00000000kbsource")
            self.assertEqual(kb["file_role"], "knowledge")
            self.assertEqual(kb["knowledge_store_id"], "iks_testknowledge")
            attachment = next(row for row in file_manifest if row.get("file_id") == "file-found")
            self.assertEqual(attachment["file_role"], "attachment")

            conversation_rows = [
                json.loads(line)
                for line in (proof_dir / "conversations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(conversation_rows[0]["voice"], "cove")
            self.assertEqual(conversation_rows[0]["is_archived"], False)
            self.assertEqual(conversation_rows[0]["is_starred"], True)
            self.assertEqual(conversation_rows[0]["is_do_not_remember"], False)

            gpt_note = (output_path / "30_Contexts" / "GPTs" / "GPT - g-custom-explicit.md").read_text(encoding="utf-8")
            self.assertIn("aliases: []", gpt_note)
            self.assertIn("name_evidence: source_id", gpt_note)
            self.assertIn("## Manual Context", gpt_note)
            self.assertIn("### Description", gpt_note)
            self.assertIn("### System Instructions", gpt_note)
            self.assertIn("### Restart Notes", gpt_note)
            self.assertIn("<!-- BEGIN GENERATED OPENAI CONTEXT -->", gpt_note)
            self.assertIn("<!-- END GENERATED OPENAI CONTEXT -->", gpt_note)
            self.assertIn("## Linked Conversations", gpt_note)
            self.assertIn("[[2023-11-14 - Forensic Parser Test]] · 2 messages · 1 files · 1 unresolved · memory unknown · voice cove", gpt_note)
            self.assertIn("## Fichiers observés dans les conversations du contexte", gpt_note)
            self.assertIn("### Knowledge Files", gpt_note)
            self.assertIn("[[KB Guide - file_00000000kbsource.md]]", gpt_note)
            self.assertIn("### Attachments", gpt_note)
            self.assertIn("![[Logical Found - file-found.png|300]]", gpt_note)
            self.assertIn("### Unresolved References", gpt_note)
            self.assertIn("`Missing Source.pdf`", gpt_note)

            tool_events = [
                json.loads(line)
                for line in (proof_dir / "tool_events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(row["event_kind"] == "web_search_group" for row in tool_events))
            self.assertTrue(any(row["event_kind"] == "web_reference" for row in tool_events))
            self.assertFalse(any(row["event_kind"] == "code_block" for row in tool_events))

    def test_manual_and_generated_blocks_are_present_for_future_incremental_updates(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                dry_run=False,
            )

            conversation_note = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = conversation_note.read_text(encoding="utf-8")
            self.assertIn("## Manual Review", note)
            self.assertIn("<!-- BEGIN GENERATED OPENAI CONVERSATION -->", note)
            self.assertIn("<!-- END GENERATED OPENAI CONVERSATION -->", note)

            context_note = output_path / "30_Contexts" / "GPTs" / "GPT - g-custom-explicit.md"
            context = context_note.read_text(encoding="utf-8")
            self.assertIn("## Manual Context", context)
            self.assertIn("<!-- BEGIN GENERATED OPENAI CONTEXT -->", context)
            self.assertIn("<!-- END GENERATED OPENAI CONTEXT -->", context)

    def test_update_existing_pack_preserves_manual_sections_and_emits_downstream_layers(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            conversation_note = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            context_note = output_path / "30_Contexts" / "GPTs" / "GPT - g-custom-explicit.md"
            conversation_note.write_text(
                conversation_note.read_text(encoding="utf-8").replace(
                    "### Summary\n\n", "### Summary\n\nHuman conversation annotation.\n\n"
                ),
                encoding="utf-8",
            )
            context_note.write_text(
                context_note.read_text(encoding="utf-8").replace(
                    "### Description\n\n", "### Description\n\nHuman GPT annotation.\n\n"
                ),
                encoding="utf-8",
            )

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                update_existing_pack=True,
                markdown_profile="readable",
            )

            self.assertIn("Human conversation annotation.", conversation_note.read_text(encoding="utf-8"))
            self.assertIn("Human GPT annotation.", context_note.read_text(encoding="utf-8"))
            evidence = output_path / "90_Evidence"
            for name in (
                "candidate_edges.jsonl",
                "context_profile_summary.json",
                "context_resource_usage_summary.json",
            ):
                self.assertTrue((evidence / name).exists(), name)
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_parse_audit_is_summary_not_row_dump(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                dry_run=False,
            )

            audit = json.loads((output_path / "90_Evidence" / "parse_audit.json").read_text(encoding="utf-8"))
            self.assertIn("counts", audit)
            self.assertIn("unresolved_link_count", audit)
            self.assertIn("orphan_asset_count", audit)
            self.assertNotIn("orphan_assets", audit)
            self.assertNotIn("unresolved_links", audit)
            self.assertTrue((output_path / "90_Evidence" / "unlinked_assets.jsonl").exists())

    def test_readable_message_omits_technical_empty_records(self):
        technical = MessageRecord(
            node_id="node",
            message_id="msg",
            parent_id=None,
            child_ids=[],
            author_role="assistant",
            create_time=None,
            update_time=None,
            model_slug=None,
            default_model_slug=None,
            channel=None,
            end_turn=None,
            status=None,
            content_type="thoughts",
            text="",
        )
        self.assertEqual(render_readable_message(technical), [])

        non_text = MessageRecord(
            node_id="node",
            message_id="msg",
            parent_id=None,
            child_ids=[],
            author_role="user",
            create_time=None,
            update_time=None,
            model_slug=None,
            default_model_slug=None,
            channel=None,
            end_turn=None,
            status=None,
            content_type="multimodal_text",
            text="",
            non_text_parts=[{"content_type": "image_asset_pointer"}],
        )
        rendered = "\n".join(render_readable_message(non_text))
        self.assertIn("Non-text content only", rendered)
        self.assertNotIn("No textual content recovered", rendered)

    def test_context_file_rows_treat_extensionless_copies_as_links_not_embeds(self):
        asset = AssetRecord(
            asset_ref_id="conv:msg:0001",
            conversation_id="conv",
            message_id="msg",
            source_shard="conversations-000.json",
            proof_path="mapping.node.message.metadata.attachments[0]",
            raw_file_id="file-no-extension",
            raw_dat_filename="file-no-extension.dat",
            physical_archive_path="nested.zip::file-no-extension.dat",
            reconstructed_filename="Extensionless",
            normalized_extension=None,
            mime_type=None,
            size=None,
            width=None,
            height=None,
            source_origin_fields={},
            library_file_id=None,
            origination_message_id=None,
            origination_thread_id=None,
            asset_status="found",
            provenance_status="explicit",
            origin_classification="user",
            origin_confidence="explicit",
            copied_filename="Extensionless - file-no-extension",
            file_role="attachment",
            copied_pack_path="20_Files/Attachments/unknown-year/unknown-month/Extensionless - file-no-extension",
        )

        self.assertEqual(context_asset_rows([asset])[0]["line"], "- [[Extensionless - file-no-extension]]")
        self.assertEqual(
            manifest_context_file_row(
                {
                    "file_id": "file-no-extension",
                    "copied_path": "20_Files/Attachments/unknown-year/unknown-month/Extensionless - file-no-extension",
                    "file_role": "attachment",
                }
            )["line"],
            "- [[Extensionless - file-no-extension]]",
        )

    def test_filtered_readable_sample_scopes_global_knowledge_files(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_second=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
                conversation_ids={"conv-1234567890abcdef"},
            )

            self.assertTrue((output_path / "20_Files" / "Knowledge" / "KB Guide - file_00000000kbsource.md").exists())
            self.assertFalse((output_path / "20_Files" / "Knowledge" / "Global KB - file_00000000globalkb.md").exists())
            self.assertTrue((output_path / "30_Contexts" / "Knowledge" / "Knowledge Store - iks_testknowledge.md").exists())
            self.assertFalse((output_path / "30_Contexts" / "Knowledge" / "Knowledge Store - iks_global.md").exists())

    def test_snorlax_template_is_project_context_not_gpt(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_second=True, include_snorlax_project=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
                markdown_profile="readable",
                conversation_ids={"conv-snorlax-project", "conv-second"},
            )

            conversation_note = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Snorlax Project Conversation.md"
            self.assertTrue(conversation_note.exists())
            note = conversation_note.read_text(encoding="utf-8")
            self.assertIn("gpts: []", note)
            self.assertIn("projects: []", note)
            self.assertIn('space_projects:\n  - "[[Project - g-p-project-context]]"', note)
            self.assertIn("Space project: [[Project - g-p-project-context]]", note)
            self.assertIn("## Sources", note)
            self.assertNotIn("## Context Evidence", note)
            self.assertNotIn("## Sources by Message", note)
            self.assertIn("[[2023-11-14 - Second Conversation]]", note)
            self.assertIn("A useful prior detail.", note)
            self.assertTrue((output_path / "30_Contexts" / "Projects" / "Project - g-p-project-context.md").exists())
            self.assertFalse((output_path / "30_Contexts" / "GPTs" / "GPT - g-p-project-context.md").exists())

            project_note = (output_path / "30_Contexts" / "Projects" / "Project - g-p-project-context.md").read_text(encoding="utf-8")
            self.assertIn("aliases: []", project_note)
            self.assertIn("## Manual Context", project_note)
            self.assertIn("<!-- BEGIN GENERATED OPENAI CONTEXT -->", project_note)
            self.assertIn("project_url: https://chatgpt.com/g/g-p-project-context", project_note)
            self.assertIn("project_instruction_status: unknown", project_note)

            rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "conversations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            row = next(row for row in rows if row["conversation_id"] == "conv-snorlax-project")
            self.assertEqual(row["gpts"], [])
            self.assertEqual(row["projects"], [])
            self.assertEqual(row["space_projects"], ["[[Project - g-p-project-context]]"])
            context_rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "context_links.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            past = next(row for row in context_rows if row.get("context_kind") == "past_conversation")
            self.assertEqual(past["cited_conversation_id"], "conv-second")
            self.assertEqual(past["cited_note"], "[[2023-11-14 - Second Conversation]]")
            self.assertEqual(
                past["proof_path"],
                "mapping.project-assistant.message.metadata.conversation_context_citation_metadata[0]",
            )
            source_rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "message_sources.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            source_past = next(row for row in source_rows if row.get("source_kind") == "past_conversation")
            self.assertEqual(source_past["cited_conversation_id"], "conv-second")

    def test_message_gizmo_project_id_is_project_context_not_gpt(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_message_gizmo_project=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
                conversation_ids={"conv-message-gizmo-project"},
            )

            note = (
                output_path
                / "10_Conversations"
                / "2023"
                / "11"
                / "2023-11-14 - Message Gizmo Project Conversation.md"
            ).read_text(encoding="utf-8")
            frontmatter = note.split("---\n", 1)[1].split("\n---", 1)[0]
            self.assertIn("type: openai_conversation", frontmatter)
            self.assertNotIn("\ngpts:", frontmatter)
            self.assertIn('space_projects:\n  - "[[Project - g-p-message-project]]"', frontmatter)
            self.assertNotIn("space_projects::", note)
            self.assertTrue((output_path / "30_Contexts" / "Projects" / "Project - g-p-message-project.md").exists())
            self.assertFalse((output_path / "30_Contexts" / "GPTs" / "GPT - g-p-message-project.md").exists())

            row = json.loads(
                (output_path / "90_Evidence" / "conversations.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(row["gpts"], [])
            self.assertEqual(row["space_projects"], ["[[Project - g-p-message-project]]"])

    def test_query_evidence_conversation_outputs_pack_summary(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_runtime_artifact=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "query",
                    "conversation",
                    "--pack",
                    str(output_path),
                    "conv-1234567890abcdef",
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Conversation", completed.stdout)
            self.assertIn("Forensic Parser Test", completed.stdout)
            self.assertIn("Pack", completed.stdout)
            self.assertIn("openai-obsidian-pack-v1.4", completed.stdout)
            self.assertIn("readable", completed.stdout)
            self.assertIn("Contexts", completed.stdout)
            self.assertIn("[[GPT - g-custom-explicit]]", completed.stdout)
            self.assertIn("Sources", completed.stdout)
            self.assertIn("Assets", completed.stdout)
            self.assertIn("Python Runtime Artifacts", completed.stdout)
            self.assertIn("confirmed_by_chat_html=1", completed.stdout)
            self.assertIn("Tools & Retrieval", completed.stdout)
            self.assertIn("Evidence pointers", completed.stdout)

            missing = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "query",
                    "conversation",
                    "--pack",
                    str(output_path),
                    "missing-conversation",
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("Conversation not found", missing.stderr)

    def test_readable_pack_emits_manifest_and_schema_version(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=True,
                emit_dataview=True,
                dry_run=False,
                markdown_profile="readable",
                conversation_ids={"conv-1234567890abcdef"},
                months={"2023-11"},
            )

            manifest = json.loads((output_path / "90_Evidence" / "pack_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "openai-obsidian-pack-v1.4")
            self.assertEqual(manifest["markdown_profile"], "readable")
            self.assertEqual(manifest["copy_assets"], True)
            self.assertEqual(manifest["copy_unlinked_assets"], False)
            self.assertEqual(manifest["emit_json"], True)
            self.assertEqual(manifest["emit_markdown"], True)
            self.assertEqual(manifest["emit_bases"], True)
            self.assertEqual(manifest["emit_dataview"], True)
            self.assertEqual(manifest["filters"]["conversation_ids"], ["conv-1234567890abcdef"])
            self.assertEqual(manifest["filters"]["months"], ["2023-11"])
            self.assertEqual(manifest["filters"]["max_conversations"], None)
            self.assertEqual(manifest["counts"]["parsed_conversations"], 1)
            self.assertEqual(manifest["counts"]["emitted_conversations"], 1)

            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("schema_version: openai-obsidian-pack-v1.4", note)

            conversation_row = json.loads(
                (output_path / "90_Evidence" / "conversations.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(conversation_row["schema_version"], "openai-obsidian-pack-v1.4")

    def test_default_markdown_profile_is_readable_compact(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)

            manifest = json.loads((output_path / "90_Evidence" / "pack_manifest.json").read_text(encoding="utf-8"))
            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(manifest["markdown_profile"], "readable_compact")
            self.assertLess(note.splitlines().index("## Full conversation") + 1, 80)
            self.assertIn("## Evidence register", note)

    def test_readable_compact_profile_places_transcript_first_and_groups_evidence(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(
                export_path,
                include_many_tool_rows=True,
                include_canvas_reference=True,
                include_textdoc_gap=True,
            )

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                dry_run=False,
                markdown_profile="readable_compact",
            )

            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(
                encoding="utf-8"
            )
            self.assertLess(note.splitlines().index("## Full conversation") + 1, 80)
            self.assertNotIn("## Evidence Summary", note)
            self.assertNotIn("## Files", note)
            self.assertNotIn("## Sources", note)
            self.assertNotIn("## Tools & Retrieval", note)
            self.assertEqual(note.count("> > [!info] Attached files · 2"), 1)
            self.assertEqual(note.count("> > [!example] Tools & retrieval · 1"), 1)
            self.assertIn(
                "> [!question] User · 2023-11-14 · 22:13:21 UTC\n"
                "> <!-- openai_message_id: msg-user -->",
                note,
            )
            frontmatter = note.split("---\n", 1)[1].split("\n---", 1)[0]
            self.assertIn("type: openai_conversation", frontmatter)
            self.assertIn("title: Forensic Parser Test", frontmatter)
            self.assertNotIn("\naliases:", frontmatter)
            self.assertEqual(
                [
                    line.split(":", 1)[0]
                    for line in frontmatter.splitlines()
                    if line and not line.startswith("  ")
                ],
                [
                    "type",
                    "schema_version",
                    "conversation_id",
                    "title",
                    "created_at",
                    "updated_at",
                    "status",
                    "reviewed",
                    "message_count",
                    "unique_file_count",
                    "provider",
                    "default_model",
                    "models_seen",
                    "space_projects",
                    "tags",
                ],
            )
            self.assertNotIn("\nsource_shard:", frontmatter)
            self.assertEqual(note.count("source_shard::"), 1)
            self.assertNotIn("message_count::", note)
            self.assertEqual(note.count("file_reference_count::"), 1)
            self.assertNotIn("unique_file_count::", note)
            self.assertEqual(note.count("unresolved_file_count::"), 1)
            self.assertLess(note.index("# Forensic Parser Test"), note.index("> [!abstract] Summary"))
            self.assertLess(note.index("> [!abstract] Summary"), note.index("## Manual Review"))
            self.assertLess(note.index("## Manual Review"), note.index("<!-- BEGIN GENERATED OPENAI CONVERSATION -->"))
            self.assertLess(note.index("<!-- BEGIN GENERATED OPENAI CONVERSATION -->"), note.index("## Full conversation"))
            self.assertLess(note.index("## Full conversation"), note.index("## References"))
            self.assertLess(note.index("## References"), note.index("> [!info] Identity and source properties"))
            self.assertLess(note.index("> [!info] Identity and source properties"), note.index("## Evidence register"))
            self.assertIn("Chat URL: https://chatgpt.com/c/conv-1234567890abcdef", note)
            self.assertNotIn("chat_url::", note)
            self.assertNotRegex(
                note,
                r"(?m)^>+ (?:> )?\[!(?:info|example|cite|abstract|success|question|note)\]-",
            )
            self.assertIn("> > [!abstract] Canvas TextDoc reference · payload absent from export", note)
            self.assertRegex(
                note,
                r"> > \[!abstract\] Canvas TextDoc reference · payload absent from export \*details\*\[\^artifact-[0-9a-f]{12}\]",
            )
            self.assertIn("> > ID: `td-ref-456`", note)
            self.assertIn("No Canvas/TextDoc payload or file for this ID is included in the export.", note)
            self.assertIn(
                "[Check original conversation](https://chatgpt.com/c/conv-1234567890abcdef) for the Canvas document or download.",
                note,
            )
            self.assertIn(
                "Proof: `mapping.assistant-node.message.metadata.open_in_canvas_view`",
                note,
            )
            self.assertIn("Canvas TextDoc references: 1 observed trace(s), each anchored to its original message", note)
            self.assertIn("assistant_claim_without_payload", note)
            self.assertIn("Text document or file mentioned by assistant, but not exported", note)
            self.assertIn("TECH_TRACE_2025-07-13_TheorieActivationJardin.md", note)
            self.assertIn(
                "[Check original conversation](https://chatgpt.com/c/conv-1234567890abcdef) for the Canvas document or download.",
                note,
            )
            self.assertIn("## Evidence register", note)
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_readable_compact_profile_materializes_native_footnotes_without_embeds(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_citation_footnotes=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                markdown_profile="readable_compact",
            )

            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Market evidence. [^cite-source-c49a5a599e43]", note)
            self.assertIn("File evidence. [^cite-source-00f760352e2b]", note)
            self.assertIn("Research evidence. [^cite-source-ebd290563baa]", note)
            self.assertRegex(
                note,
                r"Composite evidence\. \[\^cite-source-[0-9a-f]{12}\]\[\^cite-source-[0-9a-f]{12}\]",
            )
            self.assertNotIn("## Citation notes", note)
            self.assertIn("Unresolved evidence. [^cite-source-", note)
            self.assertIn("## References", note)
            self.assertRegex(
                note,
                r"> > - \[\[20_Files/Attachments/2023/11/Logical Found - file-found\.png\|Logical Found\.png\]\] • \*details\*\[\^file-[0-9a-f]{12}\]",
            )
            self.assertRegex(note, r"> > - Fixture tether result • \*details\*\[\^source-[0-9a-f]{12}\]")
            self.assertRegex(note, r"> > - example\.com • \*details\*\[\^tool-[0-9a-f]{12}\]")
            self.assertIn("[^cite-source-c49a5a599e43]: **Fixture web result** — citation résolue", note)
            self.assertIn(
                "[^cite-source-00f760352e2b]: **Missing Source.pdf** — "
                "citation de fichier identifiée, payload physique non résolu",
                note,
            )
            self.assertIn("[^cite-source-ebd290563baa]: **Fixture tether result** — citation résolue", note)
            self.assertIn("    - Snippet : Fixture web evidence.", note)
            self.assertIn("    - Snippet : Fixture tether evidence.", note)
            self.assertNotIn("Fixture web evidence. · proof_path", note)
            self.assertIn("citation unresolved_marker", note)
            self.assertNotRegex(note, r"(?m)^(?:> > - |  - )!\[\[")
            self.assertIn("    - Evidence : `90_Evidence/citation_links.jsonl`", note)
            references = note.split("## References", 1)[1].split("## Evidence register", 1)[0]
            self.assertNotRegex(references, r"(?m)^  - ")
            rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "citation_links.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 6)
            self.assertEqual(
                [row["resolution_status"] for row in rows],
                ["resolved", "resolved", "resolved", "resolved", "resolved", "unresolved_marker"],
            )
            self.assertEqual(
                [row["citation_family"] for row in rows],
                [
                    "web_search_result",
                    "file_retrieval",
                    "bracket_content_reference",
                    "web_search_result",
                    "web_search_result",
                    "web_search_result",
                ],
            )
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_readable_compact_footnote_links_known_filtered_conversation_by_physical_path(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_second=True, include_snorlax_project=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
                markdown_profile="readable_compact",
                conversation_ids={"conv-snorlax-project"},
            )

            note_path = (
                output_path
                / "10_Conversations"
                / "2023"
                / "11"
                / "2023-11-14 - Snorlax Project Conversation.md"
            )
            note = note_path.read_text(encoding="utf-8")
            self.assertIn(
                "    - Note : [[10_Conversations/2023/11/2023-11-14 - Second Conversation|Second Conversation]]",
                note,
            )
            self.assertNotIn(
                "     - [[10_Conversations/2023/11/2023-11-14 - Second Conversation",
                note,
            )
            source_rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "message_sources.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            source = next(row for row in source_rows if row.get("source_kind") == "past_conversation")
            self.assertIsNone(source["cited_note_path"])
            self.assertEqual(
                source["cited_note_candidate_path"],
                "10_Conversations/2023/11/2023-11-14 - Second Conversation.md",
            )
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_compact_evidence_register_reports_technical_omissions(self):
        rendered = "\n".join(
            render_compact_evidence_register(
                assets=[],
                message_source_rows=[],
                tool_event_rows=[],
                technical_events=[],
                runtime_artifacts=[],
                historical_comparisons=[],
                canvas_context_count=0,
                citation_links=[],
                skipped_technical=3,
            )
        )
        self.assertIn("## Evidence register", rendered)
        self.assertIn(
            "Technical empty assistant records omitted from Markdown: 3",
            rendered,
        )

    def test_compact_snippet_marks_truncation_explicitly(self):
        exact = "A" * 280
        long = "A" * 320
        self.assertEqual(compact_snippet(exact), exact)
        self.assertEqual(len(compact_snippet(long)), 280)
        self.assertTrue(compact_snippet(long).endswith("..."))

    def test_readable_compact_changes_only_markdown_projection(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            readable_path = tmp_path / "readable"
            compact_path = tmp_path / "compact"
            build_fixture_export(
                export_path,
                include_citation_footnotes=True,
                include_side_branch=True,
                include_canvas_reference=True,
            )

            common = {
                "copy_assets": True,
                "emit_json": True,
                "emit_markdown": True,
                "emit_bases": True,
                "emit_dataview": True,
                "dry_run": False,
            }
            run_parse(export_path, readable_path, markdown_profile="readable", **common)
            run_parse(export_path, compact_path, markdown_profile="readable_compact", **common)

            readable_evidence = readable_path / "90_Evidence"
            compact_evidence = compact_path / "90_Evidence"
            readable_jsonl = {
                path.relative_to(readable_evidence)
                for path in readable_evidence.rglob("*.jsonl")
            }
            compact_jsonl = {
                path.relative_to(compact_evidence)
                for path in compact_evidence.rglob("*.jsonl")
            }
            self.assertEqual(readable_jsonl, compact_jsonl)
            for relative in sorted(readable_jsonl):
                self.assertEqual(
                    (readable_evidence / relative).read_bytes(),
                    (compact_evidence / relative).read_bytes(),
                    relative.as_posix(),
                )

            readable_notes = {
                path.relative_to(readable_path / "10_Conversations")
                for path in (readable_path / "10_Conversations").rglob("*.md")
            }
            compact_notes = {
                path.relative_to(compact_path / "10_Conversations")
                for path in (compact_path / "10_Conversations").rglob("*.md")
            }
            self.assertEqual(readable_notes, compact_notes)
            self.assertFalse((compact_evidence / "conversation_note_locators.jsonl").exists())

    def test_compact_citations_reuse_one_note_per_explicit_source(self):
        text = "citeturn0search0 then citeturn0search1"
        source = dict(
            conversation_id="conv",
            message_id="message",
            node_id="node",
            citation_family="web_search_result",
            resolution_status="resolved",
            title="One source",
            url="https://example.com/source?edition=1",
            file_id=None,
            copied_pack_path=None,
            snippet="Explicit result",
            proof_path="mapping.node.message.metadata.search_result_groups[0]",
        )
        first = CitationLinkRecord(
            citation_link_id="conv:message:citation-001",
            marker_original="citeturn0search0",
            marker_start=text.index("citeturn0search0"),
            marker_end=text.index("citeturn0search0") + len("citeturn0search0"),
            footnote_id="legacy-first",
            line_start=1,
            line_end=4,
            **source,
        )
        second = CitationLinkRecord(
            citation_link_id="conv:message:citation-002",
            marker_original="citeturn0search1",
            marker_start=text.index("citeturn0search1"),
            marker_end=text.index("citeturn0search1") + len("citeturn0search1"),
            footnote_id="legacy-second",
            line_start=20,
            line_end=24,
            **source,
        )
        rendered = replace_resolved_citation_markers(text, [first, second])
        notes = "\n".join(render_citation_notes([first, second]))
        footnotes = "\n".join(render_native_footnotes([first, second], {}))

        self.assertEqual(rendered.count("#^cite-source-"), 2)
        anchor = rendered.split("#^")[1].split("|")[0]
        self.assertEqual(rendered.split("#^")[2].split("|")[0], anchor)
        self.assertEqual(notes.count("- [One source](https://example.com/source?edition=1)"), 1)
        self.assertEqual(notes.count(f"^{anchor}"), 1)
        self.assertIn("lines 1–4", notes)
        self.assertIn("lines 20–24", notes)
        self.assertEqual(footnotes.count("Snippet : Explicit result"), 1)

    def test_compact_files_show_all_distinct_identities_without_truncation(self):
        message = MessageRecord(
            node_id="node",
            message_id="message",
            parent_id=None,
            child_ids=[],
            author_role="user",
            create_time=None,
            update_time=None,
            model_slug=None,
            default_model_slug=None,
            channel=None,
            end_turn=None,
            status=None,
            content_type="text",
            text="files",
        )
        annotations = [
            MessageAnnotation(
                kind="file",
                status="found",
                label=f"file-{index}.png",
                presentation_identity=f"raw_file_id:file-{index}",
            )
            for index in range(7)
        ]
        rendered = "\n".join(render_compact_message(message, annotations))
        self.assertIn("> > [!info] Attached files · 7", rendered)
        self.assertNotIn("additional row(s)", rendered)
        for index in range(7):
            self.assertRegex(
                rendered,
                rf"`file-{index}\.png` • \*details\*\[\^file-[0-9a-f]{{12}}\]",
            )

    def test_readable_compact_message_matches_clean_specimen_golden(self):
        message = MessageRecord(
            node_id="node",
            message_id="message",
            parent_id=None,
            child_ids=[],
            author_role="user",
            create_time=None,
            update_time=None,
            model_slug=None,
            default_model_slug=None,
            channel=None,
            end_turn=None,
            status=None,
            content_type="text",
            text="Inspect the attached source.",
        )
        annotations = [
            MessageAnnotation(
                kind="file",
                status="missing",
                label="Synthetic brief.pdf",
                presentation_identity="raw_file_id:file-synthetic-brief",
            ),
            MessageAnnotation(
                kind="tool",
                status="web_search",
                label="Synthetic search",
                proof_path="mapping.node.message.metadata.search",
            ),
        ]
        rendered = "\n".join(render_compact_message(message, annotations)).rstrip() + "\n"
        golden = (Path(__file__).parent / "fixtures" / "readable_compact_message.golden.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(rendered, golden)

    def test_sandbox_projection_prefers_exact_local_payload_then_conversation_fallback(self):
        local = rewrite_sandbox_references(
            "[Download output](sandbox:/mnt/data/result.png)",
            {"sandbox:/mnt/data/result.png": "20_Files/Generated/result.png"},
            "https://chatgpt.com/c/conversation",
        )
        fallback = rewrite_sandbox_references(
            "sandbox:/mnt/data/missing.csv",
            {"sandbox:/mnt/data/missing.csv": None},
            "https://chatgpt.com/c/conversation",
        )
        self.assertEqual(local, "[[20_Files/Generated/result.png|Download output]]")
        self.assertIn("https://chatgpt.com/c/conversation", fallback)
        self.assertIn("visit original conversation to download", fallback)

        already_prefixed = rewrite_sandbox_references(
            "👉 [Download output](sandbox:/mnt/data/result.png)",
            {"sandbox:/mnt/data/result.png": None},
            "https://chatgpt.com/c/conversation",
        )
        self.assertEqual(already_prefixed.count("👉"), 1)

    def test_compact_sandbox_reference_falls_back_when_exact_payload_is_not_materialized(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_sandbox_reference=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                copy_unlinked_assets=True,
                emit_json=True,
                emit_markdown=True,
                markdown_profile="readable_compact",
            )

            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("https://chatgpt.com/c/conv-1234567890abcdef", note)
            self.assertIn("visit original conversation to download", note)
            self.assertNotIn("sandbox:/mnt/data/result.png", note)
            references = (output_path / "90_Evidence" / "references.jsonl").read_text(encoding="utf-8")
            self.assertIn("sandbox:/mnt/data/result.png", references)

    def test_conversation_record_preserves_linear_node_ids_for_main_path(self):
        with TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "export.zip"
            build_fixture_export(export_path, include_side_branch=True)

            archive = ExportArchive(export_path)
            shard = archive.find_members(lambda m: m.name == "conversations-000.json")[0]
            raw = json.loads(archive.read_text(shard))[0]
            conversation = parse_conversation(raw, shard, {})

            self.assertEqual(conversation.linear_node_ids, ["root", "user-node", "assistant-node"])
            self.assertEqual(conversation.linear_message_ids, ["msg-user", "msg-assistant"])

    def test_branch_infos_detects_alternate_child_not_on_main_path(self):
        with TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "export.zip"
            build_fixture_export(export_path, include_side_branch=True)

            archive = ExportArchive(export_path)
            shard = archive.find_members(lambda m: m.name == "conversations-000.json")[0]
            raw = json.loads(archive.read_text(shard))[0]
            conversation = parse_conversation(raw, shard, {})
            branches = branch_infos(conversation)

            self.assertEqual(len(branches), 1)
            self.assertEqual(branches[0].node_id, "user-node")
            self.assertEqual(branches[0].main_child_id, "assistant-node")
            self.assertEqual(branches[0].alternate_child_ids, ["side-assistant-node"])

    def test_message_annotations_index_source_and_tool_rows_by_message(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")

            archive = ExportArchive(export_path)
            shard = archive.find_members(lambda m: m.name == "conversations-000.json")[0]
            raw = json.loads(archive.read_text(shard))[0]
            conversation = parse_conversation(raw, shard, {})
            proof_dir = output_path / "90_Evidence"
            assets = [
                AssetRecord(**json.loads(line))
                for line in (proof_dir / "asset_links.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            source_rows = [
                json.loads(line)
                for line in (proof_dir / "message_sources.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            tool_rows = [
                json.loads(line)
                for line in (proof_dir / "tool_events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            annotations = build_message_annotations(conversation, assets, source_rows, tool_rows)

            self.assertIn("msg-assistant", annotations)
            self.assertTrue(any(annotation.kind == "tool" for annotation in annotations["msg-assistant"]))
            self.assertTrue(any(annotation.status == "web_search_group" for annotation in annotations["msg-assistant"]))

    def test_readable_parse_emits_noncanonical_same_name_payload_diagnostic(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_same_name_payload_candidate=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")

            evidence = output_path / "90_Evidence"
            candidates = [
                json.loads(line)
                for line in (evidence / "unverified_payload_candidates.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            candidate = next(row for row in candidates if row["reference_file_id"] == "file-missing")
            self.assertEqual(candidate["status"], "unverified_candidate")
            self.assertFalse(candidate["canonical"])
            self.assertEqual(candidate["candidate_file_id"], "file-found")
            self.assertEqual(candidate["candidate_count_for_name"], 1)
            self.assertEqual(candidate["reference_resolution_statuses"], ["no_candidate_in_inventory"])
            self.assertEqual(candidate["type_comparison"], "extension_conflict")

            summary = json.loads((evidence / "unverified_payload_candidate_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["counts"]["candidate_rows"], 1)
            self.assertTrue(summary["contract"]["name_equality_is_not_physical_identity"])
            audit = json.loads((evidence / "parse_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["unverified_payload_candidates"]["counts"]["candidate_rows"], 1)

            note = (output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md").read_text(encoding="utf-8")
            self.assertIn("Related payload candidate(s) elsewhere in export; identity, content, and version are not demonstrated.", note)
            self.assertIn("Candidate OpenAI file ID: `file-found`", note)
            self.assertIn("Details: `90_Evidence/unverified_payload_candidates.jsonl`", note)
            self.assertNotIn("Same-name exported payload:", note)
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_pack_validator_rejects_incomplete_or_unproven_unverified_payload_candidates(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_same_name_payload_candidate=True)
            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)

            evidence = output_path / "90_Evidence"
            summary_path = evidence / "unverified_payload_candidate_summary.json"
            summary_path.unlink()
            self.assertTrue(
                any("unverified payload candidate evidence incomplete" in error for error in validate_pack(output_path)["errors"])
            )

            second_output_path = tmp_path / "out-second"
            run_parse(export_path, second_output_path, copy_assets=True, emit_json=True, emit_markdown=True)
            candidates_path = second_output_path / "90_Evidence" / "unverified_payload_candidates.jsonl"
            candidates = [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines()]
            candidates[0]["candidate_physical_archive_path"] = "missing-proof.dat"
            candidates_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in candidates),
                encoding="utf-8",
            )
            self.assertTrue(
                any("candidate does not match a materialized manifest row" in error for error in validate_pack(second_output_path)["errors"])
            )

    def test_unverified_payload_candidates_preserve_ambiguous_same_name_candidates(self):
        asset = AssetRecord(
            asset_ref_id="conv:msg:missing",
            conversation_id="conv",
            message_id="msg",
            source_shard="conversations-000.json",
            proof_path="mapping.node.message.metadata.attachments[0]",
            raw_file_id="file-missing",
            raw_dat_filename="file-missing.dat",
            physical_archive_path=None,
            reconstructed_filename="Repeated name.md",
            normalized_extension="md",
            mime_type="text/markdown",
            size=None,
            width=None,
            height=None,
            source_origin_fields={},
            library_file_id=None,
            origination_message_id=None,
            origination_thread_id=None,
            asset_status="missing",
            provenance_status="explicit",
            origin_classification="user",
            origin_confidence="explicit",
        )
        result = UnverifiedPayloadCandidateAnalyzer().construct(
            [asset],
            [{
                "asset_ref_id": asset.asset_ref_id,
                "effective_asset_status": "missing",
                "matching_resolution_statuses": ["no_candidate_in_inventory"],
            }],
            [
                {
                    "materialization_status": "materialized",
                    "file_id": "file-first",
                    "logical_basename": "Repeated name.md",
                    "copied_path": "20_Files/Knowledge/Repeated name - first.md",
                    "physical_archive_path": "first.zip::file-first.dat",
                    "detected_extension": "md",
                },
                {
                    "materialization_status": "materialized",
                    "file_id": "file-second",
                    "logical_basename": "Repeated name.md",
                    "copied_path": "20_Files/Knowledge/Repeated name - second.md",
                    "physical_archive_path": "second.zip::file-second.dat",
                    "detected_extension": "md",
                },
            ],
        )

        self.assertEqual(len(result.records), 2)
        self.assertEqual({row.candidate_file_id for row in result.records}, {"file-first", "file-second"})
        self.assertTrue(all(row.candidate_count_for_name == 2 for row in result.records))
        self.assertTrue(all(row.ambiguities == ["multiple_same_name_payloads"] for row in result.records))
        self.assertTrue(all(row.canonical is False for row in result.records))

    def test_unverified_payload_candidate_matches_url_encoded_uncopied_homonym(self):
        asset = AssetRecord(
            asset_ref_id="conv:msg:missing-url-name",
            conversation_id="conv",
            message_id="msg",
            source_shard="conversations-000.json",
            proof_path="mapping.node.message.metadata.content_references[0]",
            raw_file_id="file-reference",
            raw_dat_filename="file-reference.dat",
            physical_archive_path=None,
            reconstructed_filename="Hornika — Mascotte canonique.v1.1.md",
            normalized_extension="md",
            mime_type="text/markdown",
            size=26675,
            width=None,
            height=None,
            source_origin_fields={},
            library_file_id=None,
            origination_message_id=None,
            origination_thread_id=None,
            asset_status="missing",
            provenance_status="explicit",
            origin_classification="user",
            origin_confidence="explicit",
        )
        result = UnverifiedPayloadCandidateAnalyzer().construct(
            [asset],
            [{
                "asset_ref_id": asset.asset_ref_id,
                "effective_asset_status": "missing",
                "matching_resolution_statuses": ["no_candidate_in_inventory"],
            }],
            [{
                "materialization_status": "materialized",
                "file_id": "file-payload",
                "logical_basename": "Hornika%20%E2%80%94%20Mascotte%20canonique.v1.1.md",
                "copied_path": None,
                "physical_archive_path": "nested.zip::file-payload.dat",
                "declared_extension": "md",
                "detected_extension": "txt",
                "size": 25721,
                "payload_disposition_status": "unlinked_payload",
            }],
        )

        self.assertEqual(len(result.records), 1)
        candidate = result.records[0]
        self.assertEqual(candidate.matching_rule, "url_decoded_casefolded_logical_basename_equality")
        self.assertEqual(candidate.candidate_file_id, "file-payload")
        self.assertIsNone(candidate.candidate_copied_path)
        self.assertEqual(candidate.reference_declared_size, 26675)
        self.assertEqual(candidate.candidate_size, 25721)
        self.assertEqual(candidate.candidate_payload_disposition_status, "unlinked_payload")

    def test_readable_markdown_renders_inline_annotations_and_export_gap(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_generation_gap=True, include_json_only_activity=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("[!example]- Tool / retrieval", note)
            self.assertIn("web_search_group", note)
            self.assertIn("[!warning]- File evidence", note)
            self.assertIn("Missing Source.pdf", note)
            self.assertIn("Referenced file ID has no physical payload in export: `file-missing`", note)
            self.assertIn("Physical payload missing from export for this reference: Missing Source.pdf", note)
            self.assertIn("File ID: `file-missing`", note)
            self.assertIn("[!warning]- Export gap", note)
            self.assertIn("mentioned_not_exported", note)
            self.assertIn("Generated artifact mentioned by assistant, but not exported", note)
            self.assertIn(
                "[Check original conversation](https://chatgpt.com/c/conv-1234567890abcdef) for the Canvas document or download.",
                note,
            )
            self.assertNotIn("\n>\n> [!info]- File evidence", note)
            self.assertNotIn("\n>\n> [!example]- Tool / retrieval", note)
            self.assertNotIn("[!info]- JSON-only activity", note)
            self.assertNotIn("hidden reasoning/thought row", note)

    def test_readable_markdown_groups_many_tool_annotations_by_message(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_many_tool_rows=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("tool_retrieval_summary", note)
            self.assertIn("8 tool/retrieval evidence rows", note)
            self.assertIn("web_search_group: `7`", note)
            self.assertIn("web_reference: `1`", note)
            self.assertIn("Full rows: `90_Evidence/tool_events.jsonl`", note)
            self.assertEqual(note.count("[!example]- Tool / retrieval"), 1)

    def test_readable_markdown_marks_textdoc_claim_without_payload(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_gap=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("[!warning]- Export gap", note)
            self.assertIn("assistant_claim_without_payload", note)
            self.assertIn("Text document or file mentioned by assistant, but not exported", note)
            self.assertIn("TECH_TRACE_2025-07-13_TheorieActivationJardin.md", note)
            self.assertIn("Mentioned document: `TECH_TRACE_2025-07-13_TheorieActivationJardin.md`", note)
            self.assertIn("No Canvas/TextDoc payload, tool event, or exported file was found", note)
            self.assertIn(
                "[Check original conversation](https://chatgpt.com/c/conv-1234567890abcdef) for the Canvas document or download.",
                note,
            )
            self.assertIn("Confidence : `textual_claim_only`", note)
            transcript = note.split("## References", 1)[0]
            references = note.split("## References", 1)[1]
            self.assertNotIn("Mentioned document:", transcript)
            self.assertIn("Mentioned document:", references)
            self.assertIn(
                "Chat URL: https://chatgpt.com/c/conv-1234567890abcdef",
                note,
            )
            self.assertIn(
                "[Check original conversation](https://chatgpt.com/c/conv-1234567890abcdef)",
                references,
            )

    def test_readable_markdown_marks_picture_v2_missing_image_payload(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_picture_v2_gap=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("[!warning]- Export gap", note)
            self.assertIn("image_generation_missing_from_export", note)
            self.assertIn("Image generation turn detected; generated image not included in export", note)
            self.assertIn("Check original conversation: https://chatgpt.com/c/conv-1234567890abcdef", note)
            self.assertIn("Exported `image_results`: `[]`", note)
            self.assertIn("Image send UUID: `image-send-123`", note)
            self.assertIn("Confidence : `explicit_system_hint`", note)

    def test_readable_markdown_places_generated_knowledge_file_at_origin_message(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_generated_knowledge_file=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("knowledge_generated_image_found", note)
            self.assertIn("Generated Knowledge Image.png", note)
            self.assertIn("Knowledge Store: `iks_testknowledge`", note)
            self.assertIn("Library file: `libfile_generated_fixture`", note)
            self.assertIn("Image generation ID: `s_generated_fixture`", note)
            self.assertIn("personal/files/Generated Knowledge Image.png", note)
            self.assertIn("![[Generated Knowledge Image - file-generated-library.png|300]]", note)

    def test_readable_markdown_marks_structural_textdoc_payload(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_payload=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("[!info] Export evidence", note)
            self.assertIn("structural_textdoc_found", note)
            self.assertIn("Text document payload present in export", note)
            self.assertIn("Textdoc ID: `td-123`", note)
            self.assertNotIn("assistant_claim_without_payload", note)
            self.assertNotIn("No canvas/textdoc payload", note)

    def test_textdoc_payload_is_extracted_to_textdocs_folder_and_jsonl(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_payload=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)

            textdocs_path = output_path / "90_Evidence" / "textdocs.jsonl"
            rows = [json.loads(line) for line in textdocs_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["status"], "exported_payload")
            self.assertEqual(row["conversation_id"], "conv-1234567890abcdef")
            self.assertEqual(row["message_id"], "msg-assistant")
            self.assertEqual(row["node_id"], "assistant-node")
            self.assertEqual(row["textdoc_id"], "td-123")
            self.assertEqual(row["title"], "TECH_TRACE_2025-07-13_TheorieActivationJardin.md")
            self.assertEqual(row["textdoc_type"], "document")
            self.assertEqual(row["content_length"], len("# TheorieActivationJardin\n\nPayload exported by ChatGPT."))
            self.assertEqual(
                row["content_sha256"],
                hashlib.sha256("# TheorieActivationJardin\n\nPayload exported by ChatGPT.".encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                row["copied_pack_path"],
                "20_Files/Textdocs/2023/11/TECH_TRACE_2025-07-13_TheorieActivationJardin.md",
            )
            extracted = output_path / row["copied_pack_path"]
            self.assertEqual(extracted.read_text(encoding="utf-8"), "# TheorieActivationJardin\n\nPayload exported by ChatGPT.")

            note = (
                output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            ).read_text(encoding="utf-8")
            self.assertIn("[[TECH_TRACE_2025-07-13_TheorieActivationJardin.md]]", note)

    def test_canvas_reference_only_is_indexed_without_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_canvas_reference=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)

            rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "textdocs.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "canvas_reference_only")
            self.assertEqual(rows[0]["textdoc_id"], "td-ref-456")
            self.assertIsNone(rows[0]["copied_pack_path"])
            self.assertFalse((output_path / "20_Files" / "Textdocs").exists())

    def test_canmore_uri_reference_is_indexed_without_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_canmore_uri=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)

            rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "textdocs.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "canmore_uri_reference")
            self.assertEqual(rows[0]["textdoc_id"], "canmore-789")
            self.assertEqual(rows[0]["title"], "canmore://textdoc/canmore-789")
            self.assertIsNone(rows[0]["copied_pack_path"])

    def test_textdoc_claim_without_payload_does_not_create_textdoc_row_or_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_gap=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)

            textdocs_path = output_path / "90_Evidence" / "textdocs.jsonl"
            self.assertEqual(textdocs_path.read_text(encoding="utf-8"), "")
            self.assertFalse((output_path / "20_Files" / "Textdocs").exists())

    def test_readable_markdown_does_not_mark_textdoc_proposal_as_export_gap(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_proposal=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True)
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertNotIn("generated_document_not_exported", note)
            self.assertNotIn("Generated text document mentioned, no textdoc payload found", note)

    def test_readable_markdown_renders_branch_marker_and_alternate_branch_section(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_side_branch=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("[!abstract]- Branch", note)
            self.assertIn("2 continuations", note)
            self.assertIn("Main path continues below", note)
            self.assertIn("Alternate branch: [[#branch-user-node]]", note)
            self.assertIn("## Alternate Branches", note)
            self.assertIn("### branch-user-node", note)
            self.assertIn("From message:", note)
            self.assertIn("> Please inspect this.", note)
            self.assertIn("Alternate continuation `side-assistant-node`:", note)
            self.assertIn("Side branch answer.", note)

    def test_evidence_section_mentions_inline_annotations_and_canonical_jsonl(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            note_path = output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("Message-level callouts above are a readable index of these proof rows.", note)
            self.assertIn("Canonical machine evidence remains in JSONL.", note)

    def test_validate_pack_accepts_valid_output_and_rejects_count_mismatch(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_side_branch=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
                markdown_profile="readable",
            )

            valid = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            self.assertIn("Pack validation OK", valid.stdout)

            audit_path = output_path / "90_Evidence" / "parse_audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit_with_row_dumps = audit | {"orphan_assets": [], "unresolved_links": []}
            audit_path.write_text(json.dumps(audit_with_row_dumps) + "\n", encoding="utf-8")
            invalid_audit = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(invalid_audit.returncode, 0)
            self.assertIn("should not embed orphan_assets rows", invalid_audit.stdout)
            self.assertIn("should not embed unresolved_links rows", invalid_audit.stdout)
            audit_path.write_text(json.dumps(audit) + "\n", encoding="utf-8")

            conversations_path = output_path / "90_Evidence" / "conversations.jsonl"
            conversation_row = json.loads(conversations_path.read_text(encoding="utf-8").splitlines()[0])
            conversation_row["message_count"] = 999
            conversations_path.write_text(json.dumps(conversation_row) + "\n", encoding="utf-8")

            invalid = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("message_count mismatch", invalid.stdout)

    def test_validate_pack_allows_distinct_unlinked_file_with_same_basename(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
            )

            proof_dir = output_path / "90_Evidence"
            found_asset = next(
                json.loads(line)
                for line in (proof_dir / "asset_links.jsonl").read_text(encoding="utf-8").splitlines()
                if '"asset_status":"found"' in line
            )
            basename = Path(found_asset["physical_archive_path"]).name
            duplicate_name_distinct_path = {
                "basename": basename,
                "archive_path": f"other-export-folder/{basename}",
                "size": found_asset["size"],
                "copied_path": None,
            }
            unlinked_path = proof_dir / "unlinked_assets.jsonl"
            unlinked_path.write_text(
                unlinked_path.read_text(encoding="utf-8") + json_dumps(duplicate_name_distinct_path) + "\n",
                encoding="utf-8",
            )

            report = validate_pack(output_path)

            self.assertEqual(report["errors"], [])

    def test_validate_pack_rejects_missing_extracted_textdoc_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_payload=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            row = json.loads((output_path / "90_Evidence" / "textdocs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            (output_path / row["copied_pack_path"]).unlink()

            report = validate_pack(output_path)

            self.assertTrue(
                any("textdocs.jsonl copied_pack_path missing on disk" in error for error in report["errors"]),
                report["errors"],
            )

    def test_validate_pack_rejects_extracted_textdoc_hash_mismatch(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_textdoc_payload=True)

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=True, markdown_profile="readable")
            row = json.loads((output_path / "90_Evidence" / "textdocs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            (output_path / row["copied_pack_path"]).write_text("corrupted", encoding="utf-8")

            report = validate_pack(output_path)

            self.assertTrue(
                any("textdocs.jsonl content_sha256 mismatch" in error for error in report["errors"]),
                report["errors"],
            )

    def test_validate_pack_rejects_obsolete_or_ambiguous_conversation_properties(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
            )

            conversations_path = output_path / "90_Evidence" / "conversations.jsonl"
            rows = [json.loads(line) for line in conversations_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["vault_projects"] = []
            rows[0]["projects"] = ["[[Project - should-not-be-space-project]]"]
            rows[0]["space_projects"] = "not-a-list"
            rows[0]["gpts"] = ["[[GPT - g-p-should-be-project]]"]
            rows[0]["created_at"] = 1_700_000_000
            rows[0]["updated_at"] = 1_700_000_060
            conversations_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )

            note_path = next((output_path / "10_Conversations").glob("**/*.md"))
            note = note_path.read_text(encoding="utf-8")
            note = note.replace("projects: []", "vault_projects: []\nprojects: []")
            note = note.replace("created_at: 2023-11-14T22:13", "created_at: 2023-11-14T22:13:20+02:00")
            note_path.write_text(note, encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("vault_projects", completed.stdout)
            self.assertIn("use projects for vault links and space_projects for ChatGPT/Perplexity project links", completed.stdout)
            self.assertIn("space_projects must be a list", completed.stdout)
            self.assertIn("space project links must use space_projects", completed.stdout)
            self.assertIn("project-like g-p context must use space_projects, not gpts", completed.stdout)
            self.assertIn("created_at must be an ISO datetime string in JSONL", completed.stdout)
            self.assertIn("updated_at must be an ISO datetime string in JSONL", completed.stdout)
            self.assertIn("created_at should be an Obsidian date-time without timezone in frontmatter", completed.stdout)

            rows[0].pop("vault_projects")
            rows[0]["projects"] = "not-a-list"
            rows[0]["gpts"] = []
            rows[0]["space_projects"] = []
            rows[0]["created_at"] = "2023-11-14T22:13:20+00:00"
            rows[0]["updated_at"] = "2023-11-14T22:14:20+00:00"
            conversations_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("projects must be a list", completed.stdout)

            note = note_path.read_text(encoding="utf-8")
            note = note.replace("created_at: 2023-11-14T22:13:20+02:00", 'created_at: "2023-11-14T22:13:20+02:00"')
            note_path.write_text(note, encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("created_at should be an Obsidian date-time without timezone in frontmatter", completed.stdout)

            note = note_path.read_text(encoding="utf-8")
            note = note.replace('created_at: "2023-11-14T22:13:20+02:00"', "created_at: '2023-11-14T22:13:20Z'")
            note_path.write_text(note, encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "openai_export_obsidian",
                    "validate",
                    "--pack",
                    str(output_path),
                ],
                check=False,
                env=CLI_ENV,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("created_at should be an Obsidian date-time without timezone in frontmatter", completed.stdout)

    def test_evidence_jsonl_includes_non_linear_branch_messages(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_side_branch=True)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
            )

            proof_dir = output_path / "90_Evidence"
            conversation_rows = [
                json.loads(line)
                for line in (proof_dir / "conversations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            message_rows = [
                json.loads(line)
                for line in (proof_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            message_ids = {row["message_id"] for row in message_rows}
            self.assertEqual(conversation_rows[0]["message_count"], len(message_rows))
            self.assertIn("msg-side-assistant", message_ids)

            context_rows = [
                json.loads(line)
                for line in (proof_dir / "context_links.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(row.get("message_id") == "msg-side-assistant" for row in context_rows))

            source_rows = [
                json.loads(line)
                for line in (proof_dir / "message_sources.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(row.get("message_id") == "msg-side-assistant" for row in source_rows))

            tool_events = [
                json.loads(line)
                for line in (proof_dir / "tool_events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(row.get("message_id") == "msg-side-assistant" for row in tool_events))

            note = (
                output_path / "10_Conversations" / "2023" / "11" / "2023-11-14 - Forensic Parser Test.md"
            ).read_text(encoding="utf-8")
            self.assertIn("## Alternate Branches", note)
            self.assertIn("Side branch answer.", note)

    def test_unlinked_assets_exclude_referenced_files_without_copying(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=False,
                dry_run=False,
            )

            unlinked_rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "unlinked_assets.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            unlinked_names = {row["basename"] for row in unlinked_rows}
            self.assertEqual(unlinked_names, {"file-orphan.dat", "table-export.csv"})

    def test_readable_full_output_does_not_copy_unlinked_assets_by_default(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
            )

            rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "unlinked_assets.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(rows)
            self.assertFalse((output_path / "20_Files" / "Unlinked").exists())
            self.assertTrue(all(row.get("copied_path") is None for row in rows))

    def test_readable_full_output_can_copy_unlinked_assets_when_enabled(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                copy_unlinked_assets=True,
                emit_json=True,
                emit_markdown=True,
                emit_bases=False,
                emit_dataview=False,
                dry_run=False,
            )

            rows = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "unlinked_assets.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            copied = [row for row in rows if row.get("copied_path")]
            self.assertTrue(copied)
            for row in copied:
                self.assertTrue((output_path / row["copied_path"]).exists())

    def test_parse_materializes_identifiable_payloads_before_copying(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            payloads = build_materialization_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                copy_unlinked_assets=True,
                emit_json=True,
                emit_markdown=False,
            )

            evidence = output_path / "90_Evidence"
            unlinked_rows = {
                row["physical_name"]: row
                for row in (
                    json.loads(line)
                    for line in (evidence / "unlinked_assets.jsonl").read_text(encoding="utf-8").splitlines()
                )
            }
            self.assertEqual(unlinked_rows["file-unlinked.dat"]["logical_basename"], "Unlinked logical.png")
            self.assertEqual(unlinked_rows["file-unlinked.dat"]["naming_basis"], "openai_filename_map")
            self.assertTrue(unlinked_rows["file-unlinked.dat"]["copied_path"].endswith("Unlinked logical.png"))
            self.assertEqual(unlinked_rows["file-wav.dat"]["logical_path"], "conversation/audio/logical-audio.wav")
            self.assertTrue(unlinked_rows["file-wav.dat"]["copied_path"].endswith("logical-audio.wav"))
            self.assertEqual(unlinked_rows["file-signature.dat"]["identity_status"], "identified_by_signature")
            self.assertTrue(unlinked_rows["file-signature.dat"]["copied_path"].endswith("file-signature.jpg"))
            self.assertTrue(unlinked_rows["000000123456789"]["copied_path"].endswith("000000123456789.png"))
            self.assertEqual(unlinked_rows["file-conflict.dat"]["type_status"], "type_conflict")
            self.assertTrue(unlinked_rows["file-conflict.dat"]["copied_path"].endswith("mapping-wins.png"))
            self.assertEqual(unlinked_rows["file-unknown.dat"]["materialization_status"], "fallback_dat")
            self.assertTrue(unlinked_rows["file-unknown.dat"]["copied_path"].endswith("file-unknown.dat"))

            collision_paths = {
                Path(unlinked_rows[name]["copied_path"]).name
                for name in ("file-collision-a.dat", "file-collision-b.dat")
            }
            self.assertEqual(collision_paths, {"same logical.png", "same logical--2.png"})
            long_name = Path(unlinked_rows["file-long.dat"]["copied_path"]).name
            self.assertLessEqual(len(long_name), 180)
            self.assertEqual(Path(long_name).suffix, ".png")
            self.assertEqual(
                hashlib.sha256((output_path / unlinked_rows["file-unlinked.dat"]["copied_path"]).read_bytes()).hexdigest(),
                hashlib.sha256(payloads["file-unlinked.dat"]).hexdigest(),
            )

            manifests = [
                json.loads(line)
                for line in (evidence / "file_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            linked = next(row for row in manifests if row.get("file_id") == "file-noextension")
            self.assertTrue(linked["copied_path"].endswith(".png"))
            self.assertEqual(linked["physical_name"], "file-noextension")
            self.assertEqual(linked["identity_status"], "mapped_by_openai")
            linked_original = next(row for row in manifests if row.get("file_id") == "file-linked")
            linked_duplicate = next(row for row in manifests if row.get("file_id") == "file-linked-duplicate")
            self.assertEqual(linked_duplicate["copy_status"], "duplicate_payload_not_copied")
            self.assertEqual(linked_duplicate["duplicate_payload_of"], linked_original["materialized_from_archive_path"])
            self.assertEqual(
                linked_duplicate["materialized_from_archive_path"],
                linked_original["materialized_from_archive_path"],
            )
            self.assertEqual(linked_duplicate["copied_path"], linked_original["copied_path"])

            self.assertNotIn("linked-duplicate.png", unlinked_rows)
            dispositions = [
                json.loads(line)
                for line in (evidence / "payload_dispositions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            duplicate = next(row for row in dispositions if row["physical_archive_path"].endswith("linked-duplicate.png"))
            self.assertEqual(duplicate["disposition_status"], "duplicate_of_referenced_payload")
            self.assertEqual(duplicate["content_sha256"], hashlib.sha256(payloads["file-linked.dat"]).hexdigest())
            self.assertIn(
                duplicate["canonical_archive_path"],
                {linked_original["archive_path"], linked_duplicate["archive_path"]},
            )
            self.assertFalse((output_path / "20_Files" / "Unlinked" / "linked-duplicate.png").exists())

            for row in unlinked_rows.values():
                if row["copy_status"] == "duplicate_payload_not_copied":
                    self.assertTrue((output_path / row["duplicate_payload_copied_path"]).exists())
                    continue
                if row["identity_status"] != "unknown":
                    self.assertNotEqual(Path(row["copied_path"]).suffix, "")
                    self.assertFalse(row["copied_path"].endswith(".dat"))

    def test_forensic_profile_keeps_legacy_folder_shape(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path)

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                emit_json=True,
                emit_markdown=True,
                dry_run=False,
                markdown_profile="forensic",
            )

            conv_dirs = list((output_path / "conversations").iterdir())
            self.assertEqual(len(conv_dirs), 1)
            note = (conv_dirs[0] / "conversation.md").read_text(encoding="utf-8")
            self.assertIn("## Metadata Summary", note)
            self.assertIn("mapping.user-node.message.metadata.attachments[0]", note)

    def test_run_parse_can_limit_conversations_for_sample_generation(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_second=True)

            result = run_parse(
                export_path,
                output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=False,
                dry_run=False,
                max_conversations=1,
                markdown_profile="forensic",
            )

            rows = (output_path / "conversations_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(result["conversation_count"], 1)
            self.assertEqual(len(rows), 1)

    def test_run_parse_can_filter_by_created_month(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            build_fixture_export(export_path, include_second=True)

            result = run_parse(
                export_path,
                output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=False,
                dry_run=False,
                months={"2023-11"},
            )

            rows = (output_path / "90_Evidence" / "conversations.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(result["conversation_count"], 2)
            self.assertEqual(len(rows), 2)

            empty_output_path = tmp_path / "empty"
            empty_result = run_parse(
                export_path,
                empty_output_path,
                copy_assets=False,
                emit_json=True,
                emit_markdown=False,
                dry_run=False,
                months={"2024-01"},
            )
            self.assertEqual(empty_result["conversation_count"], 0)


if __name__ == "__main__":
    unittest.main()
