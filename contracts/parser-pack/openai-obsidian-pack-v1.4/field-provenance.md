# Field provenance

| Record family | Authoritative source | Serializer | Validator / rule |
| --- | --- | --- | --- |
| Pack manifest | `runner.py:SCHEMA_VERSION`, `build_pack_manifest` | `emit_readable_outputs` -> `write_json` | `validate_pack` uses it as pack metadata; schema is closed here |
| Conversation evidence | `models.py:ConversationRecord`; `runner.py:readable_conversation_row` | `emit_readable_outputs` -> `conversations.jsonl` | `validate_pack` checks IDs, datetime type, context lists, message/file counts |
| Message evidence | `models.py:MessageRecord`; `conversations.py:parse_message` | `MessageRecord.to_dict` plus `conversation_id` in `emit_readable_outputs` | `validate_pack` checks conversation and non-null message references |
| Asset evidence | `models.py:AssetRecord`; `assets.py:build_asset_records` | `AssetRecord.to_dict` | `validate_pack` checks IDs, proof paths, status and copied files |
| Cross-file structure | `runner.py:emit_readable_outputs` | ordered JSONL writers | `validation.py:validate_pack` |
| Conversation note locator | `runner.py` exact note-emission path | `40_Views/conversation_note_locators.jsonl` | `validation.py:validate_conversation_note_locator` |

The internal `ConversationRecord.to_dict()` is not the exported conversation row: the public row is the separate `readable_conversation_row()` projection. `MessageRecord` and `AssetRecord` are exported through their dataclass serializers, with `conversation_id` added to message rows by the readable writer.
