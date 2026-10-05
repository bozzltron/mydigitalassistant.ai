import argparse
from unittest.mock import MagicMock, patch

import httpx
import pytest

from assistant.cli.app import (
    BackendClient,
    cmd_chat,
    cmd_db_backup,
    cmd_db_list,
    cmd_db_restore,
    cmd_memory_conflicts,
    cmd_memory_list,
    cmd_memory_resolve,
    cmd_memory_show,
    cmd_status,
    cmd_users_add,
    cmd_users_list,
)


def make_args(**kwargs):
    return argparse.Namespace(**kwargs)


def test_cmd_memory_list_calls_client_with_no_filter():
    client = MagicMock(spec=BackendClient)
    client.list_frames.return_value = [
        {
            "id": 1,
            "name": "guitar",
            "type": "entity",
            "confidence": 0.8,
            "priority": 0.5,
            "essential": 0,
            "updated_at": "2026-01-01T00:00:00",
        },
    ]
    args = make_args(type=None)
    cmd_memory_list(args, client)
    client.list_frames.assert_called_once_with(type=None)


def test_cmd_memory_list_calls_client_with_type_filter():
    client = MagicMock(spec=BackendClient)
    client.list_frames.return_value = []
    args = make_args(type="entity")
    cmd_memory_list(args, client)
    client.list_frames.assert_called_once_with(type="entity")


def test_cmd_memory_show_by_id():
    client = MagicMock(spec=BackendClient)
    client.get_frame.return_value = {
        "id": 1,
        "name": "guitar",
        "type": "entity",
        "confidence": 0.8,
        "updated_at": "2026-01-01T00:00:00",
    }
    client.get_frame_slots.return_value = []
    args = make_args(frame="1")
    cmd_memory_show(args, client)
    client.get_frame.assert_called_once_with(1)
    client.get_frame_by_name.assert_not_called()


def test_cmd_memory_show_by_name():
    client = MagicMock(spec=BackendClient)
    client.get_frame_by_name.return_value = {
        "id": 1,
        "name": "guitar",
        "type": "entity",
        "confidence": 0.8,
        "updated_at": "2026-01-01T00:00:00",
    }
    client.get_frame_slots.return_value = []
    args = make_args(frame="guitar")
    cmd_memory_show(args, client)
    client.get_frame_by_name.assert_called_once_with("guitar")
    client.get_frame.assert_not_called()


def test_cmd_memory_show_not_found_exits():
    client = MagicMock(spec=BackendClient)
    client.get_frame_by_name.return_value = None
    args = make_args(frame="nonexistent")
    with pytest.raises(SystemExit):
        cmd_memory_show(args, client)


def test_cmd_memory_conflicts_with_status_filter():
    client = MagicMock(spec=BackendClient)
    client.list_conflicts.return_value = []
    args = make_args(status="pending")
    cmd_memory_conflicts(args, client)
    client.list_conflicts.assert_called_once_with(status="pending")


def test_cmd_memory_resolve_calls_client():
    client = MagicMock(spec=BackendClient)
    client.resolve_conflict.return_value = {
        "status": "resolved",
        "slot": {
            "value": "7",
            "key": "strings",
            "frame_id": 1,
            "id": 1,
            "confidence": 1.0,
            "source_episode_id": None,
            "updated_at": "2026-01-01",
        },
    }
    args = make_args(conflict_id=5, value="7")
    cmd_memory_resolve(args, client)
    client.resolve_conflict.assert_called_once_with(5, "7")


def test_cmd_users_add_success():
    client = MagicMock(spec=BackendClient)
    client.create_user.return_value = {
        "id": 1,
        "name": "alice",
        "created_at": "2026-01-01T00:00:00",
    }
    args = make_args(name="alice")
    cmd_users_add(args, client)
    client.create_user.assert_called_once_with("alice")


def test_cmd_users_add_duplicate_shows_message():
    client = MagicMock(spec=BackendClient)
    response = MagicMock()
    response.status_code = 409
    error = httpx.HTTPStatusError("conflict", request=MagicMock(), response=response)
    client.create_user.side_effect = error
    args = make_args(name="alice")
    with patch("assistant.cli.app.console") as mock_console:
        cmd_users_add(args, client)
    printed = " ".join(str(c.args[0]) for c in mock_console.print.call_args_list)
    assert "already exists" in printed
    assert "alice" in printed


def test_cmd_users_add_other_error_exits():
    client = MagicMock(spec=BackendClient)
    response = MagicMock()
    response.status_code = 500
    error = httpx.HTTPStatusError("server error", request=MagicMock(), response=response)
    client.create_user.side_effect = error
    args = make_args(name="alice")
    with pytest.raises(SystemExit):
        cmd_users_add(args, client)


def test_cmd_users_list_empty_shows_message():
    client = MagicMock(spec=BackendClient)
    client.list_users.return_value = []
    args = make_args()
    with patch("assistant.cli.app.console") as mock_console:
        cmd_users_list(args, client)
    printed = " ".join(str(c.args[0]) for c in mock_console.print.call_args_list)
    assert "No users" in printed


def test_cmd_status_calls_health():
    client = MagicMock(spec=BackendClient)
    client.base_url = "http://127.0.0.1:8000"
    client.health.return_value = {
        "status": "ok",
        "ollama_reachable": True,
        "models": {
            "chat": "qwen2.5:7b",
            "utility": "qwen2.5:3b",
            "embedding": "nomic-embed-text",
            "coder": "",
        },
        "thinking_supported": False,
    }
    args = make_args()
    cmd_status(args, client)
    client.health.assert_called_once()


def test_cmd_status_backend_down_exits():
    client = MagicMock(spec=BackendClient)
    client.base_url = "http://127.0.0.1:8000"
    client.health.side_effect = httpx.ConnectError("refused")
    args = make_args()
    with pytest.raises(SystemExit):
        cmd_status(args, client)


def test_cmd_db_backup_calls_api():
    client = MagicMock(spec=BackendClient)
    client.db_backup.return_value = {
        "status": "ok",
        "backup_filename": "backup-20260101-120000.db",
        "backup_path": "/app/data/backup-20260101-120000.db",
        "db_size_bytes": 12345,
        "backup_size_bytes": 12340,
    }
    args = make_args(plain=True)
    cmd_db_backup(args, client)
    client.db_backup.assert_called_once()


def test_cmd_db_restore_calls_api():
    client = MagicMock(spec=BackendClient)
    client.list_backups.return_value = {
        "backups": [
            {
                "filename": "backup-20260101.db",
                "size_bytes": 100,
                "created_at": "2026-01-01T12:00:00",
            }
        ]
    }
    client.db_restore.return_value = {"status": "ok", "restored_from": "backup-20260101.db"}
    args = make_args(file="backup-20260101.db", yes=True)
    cmd_db_restore(args, client)
    client.db_restore.assert_called_once_with("backup-20260101.db")


def test_cmd_db_restore_file_not_found():
    client = MagicMock(spec=BackendClient)
    client.list_backups.return_value = {"backups": []}
    args = make_args(file="nonexistent.db", yes=True)
    with pytest.raises(SystemExit):
        cmd_db_restore(args, client)


def test_cmd_db_list_calls_api():
    client = MagicMock(spec=BackendClient)
    client.list_backups.return_value = {
        "backups": [
            {
                "filename": "backup-20260101.db",
                "size_bytes": 100,
                "created_at": "2026-01-01T12:00:00",
            }
        ]
    }
    args = make_args()
    cmd_db_list(args, client)
    client.list_backups.assert_called_once()


def test_cmd_chat_exits_on_exit_command():
    client = MagicMock(spec=BackendClient)
    client.base_url = "http://127.0.0.1:8000"
    client.list_users.return_value = [{"id": 1, "name": "alice"}]
    args = make_args(user=1, trace=False)
    with patch("assistant.cli.app.Prompt.ask", side_effect=["exit"]):
        cmd_chat(args, client)
    client.chat.assert_not_called()


def test_cmd_chat_skips_empty_input():
    client = MagicMock(spec=BackendClient)
    client.base_url = "http://127.0.0.1:8000"
    client.list_users.return_value = [{"id": 1, "name": "alice"}]
    args = make_args(user=1, trace=False)
    with patch("assistant.cli.app.Prompt.ask", side_effect=["", "exit"]):
        cmd_chat(args, client)
    client.chat.assert_not_called()


def test_cmd_chat_recalls_memory_indicator():
    client = MagicMock(spec=BackendClient)
    client.base_url = "http://127.0.0.1:8000"
    client.list_users.return_value = [{"id": 1, "name": "alice"}]
    client.chat.return_value = {
        "response": "You like blue.",
        "session_id": "abc123",
        "task_type": "recall",
        "memory_context": "User prefers blue guitar.",
    }
    args = make_args(user=1, trace=False)
    with patch("assistant.cli.app.Prompt.ask", side_effect=["what do I like?", "exit"]):
        cmd_chat(args, client)
    client.chat.assert_called_once()
