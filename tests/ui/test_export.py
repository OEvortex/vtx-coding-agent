import json

from vtx import get_config_dir
from vtx.ui.export import export_session_html

CWD = "/tmp/export-proj"
SESSION_ID = "abc12345"


def _write_session(entries: list[dict]) -> None:
    safe_cwd = CWD.replace("/", "-").replace("\\", "-").strip("-")
    sessions_dir = get_config_dir() / "sessions" / safe_cwd
    sessions_dir.mkdir(parents=True, exist_ok=True)

    header = {
        "type": "header",
        "id": SESSION_ID,
        "timestamp": "2026-06-12T10:00:00+00:00",
        "cwd": CWD,
    }
    lines = [json.dumps(header)] + [json.dumps(entry) for entry in entries]
    (sessions_dir / f"{SESSION_ID}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _entry(entry_id: str, parent_id: str | None, **fields) -> dict:
    return {
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": "2026-06-12T10:00:00+00:00",
        **fields,
    }


def _export(tmp_path) -> str:
    output_path = export_session_html(CWD, SESSION_ID, str(tmp_path))
    return output_path.read_text(encoding="utf-8")


def test_export_includes_failed_shell_command_output(tmp_path):
    _write_session(
        [
            _entry("e1", None, type="message", message={"role": "user", "content": "hello"}),
            _entry(
                "e2",
                "e1",
                type="message",
                message={"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
            ),
            _entry(
                "e3",
                "e2",
                type="custom_message",
                custom_type="shell_command",
                content="!grep -r register_cmd .",
                details={
                    "command": "grep -r register_cmd .",
                    "output": "[stderr]\ngrep: bad option",
                    "success": False,
                },
            ),
        ]
    )

    html = _export(tmp_path)

    assert "hi there" in html
    assert "$ grep -r register_cmd ." in html
    assert "grep: bad option" in html
    assert "tool-result error" in html


def test_export_includes_successful_shell_command_output(tmp_path):
    _write_session(
        [
            _entry(
                "e1",
                None,
                type="custom_message",
                custom_type="shell_command",
                content="!ls",
                details={"command": "ls", "output": "README.md", "success": True},
            )
        ]
    )

    html = _export(tmp_path)

    assert "$ ls" in html
    assert "README.md" in html
    assert "tool-result error" not in html


def test_export_renders_generic_custom_message_as_system_message(tmp_path):
    _write_session(
        [_entry("e1", None, type="custom_message", custom_type="note", content="some system note")]
    )

    html = _export(tmp_path)

    assert "some system note" in html
    assert 'class="system-msg"' in html
