from __future__ import annotations

import datetime as dt
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("penpad_under_test", ROOT / "penpad.py")
penpad = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(penpad)


class PenpadRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.old = {
            "DATA_FILE": penpad.DATA_FILE,
            "WEEK_FILE": penpad.WEEK_FILE,
            "ARCHIVE_FILE": penpad.ARCHIVE_FILE,
            "STATE_FILE": penpad.STATE_FILE,
            "CHAT_FILE": penpad.CHAT_FILE,
            "PRESENCE_FILE": penpad.PRESENCE_FILE,
            "FILES_DIR": penpad.FILES_DIR,
            "NOTE_FILES": penpad.NOTE_FILES,
            "PENPAD_TOKEN": penpad.PENPAD_TOKEN,
            "MAX_NOTE_BYTES": penpad.MAX_NOTE_BYTES,
            "MAX_CHAT_BYTES": penpad.MAX_CHAT_BYTES,
            "MAX_UPLOAD_BYTES": penpad.MAX_UPLOAD_BYTES,
            "local_today": penpad.local_today,
        }

        penpad.DATA_FILE = self.base / "penpad.txt"
        penpad.WEEK_FILE = self.base / "penpad.week.txt"
        penpad.ARCHIVE_FILE = self.base / "penpad.archive.txt"
        penpad.STATE_FILE = self.base / "penpad.state.json"
        penpad.CHAT_FILE = self.base / "penpad.chat.jsonl"
        penpad.PRESENCE_FILE = self.base / "penpad.presence.json"
        penpad.FILES_DIR = self.base / "files"
        penpad.NOTE_FILES = {
            "today": penpad.DATA_FILE,
            "week": penpad.WEEK_FILE,
            "archive": penpad.ARCHIVE_FILE,
        }
        penpad.PENPAD_TOKEN = ""
        penpad.MAX_NOTE_BYTES = 1024 * 1024
        penpad.MAX_CHAT_BYTES = 64 * 1024
        penpad.MAX_UPLOAD_BYTES = 1024 * 1024

        for path in (penpad.DATA_FILE, penpad.WEEK_FILE, penpad.ARCHIVE_FILE,
                     penpad.CHAT_FILE, penpad.PRESENCE_FILE):
            path.touch()
        penpad.FILES_DIR.mkdir()

        self.httpd = penpad.ReusableTCPServer(("127.0.0.1", 0), penpad.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        for name, value in self.old.items():
            setattr(penpad, name, value)
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: bytes | str | None = None,
                headers: dict[str, str] | None = None):
        data = None
        if body is not None:
            data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(
            self.url + path,
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def json_request(self, method: str, path: str, payload: dict,
                     headers: dict[str, str] | None = None):
        merged = {"Content-Type": "application/json", **(headers or {})}
        status, res_headers, body = self.request(
            method, path, json.dumps(payload), merged
        )
        data = json.loads(body.decode("utf-8")) if body else None
        return status, res_headers, data

    def test_save_append_and_optional_token_auth(self) -> None:
        status, headers, _ = self.request("POST", "/append", "alpha\n")
        self.assertEqual(status, 200)
        self.assertIn("X-Rev", headers)

        status, _, _ = self.request("POST", "/append", "beta\n")
        self.assertEqual(status, 200)
        self.assertEqual(penpad.DATA_FILE.read_text("utf-8"), "alpha\nbeta\n")

        status, _, body = self.request("POST", "/save?note=archive", "nope")
        self.assertEqual(status, 403, body)

        penpad.PENPAD_TOKEN = "secret"
        status, _, _ = self.request(
            "POST", "/chat", json.dumps({"author": "a", "text": "blocked"}),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 401)
        status, _, payload = self.json_request(
            "POST", "/chat", {"author": "a", "text": "@b allowed"},
            {"X-Penpad-Token": "secret"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["message"]["mentions"], ["b"])

    def test_body_limits_reject_oversized_payloads(self) -> None:
        penpad.MAX_NOTE_BYTES = 4
        status, _, _ = self.request("POST", "/save", b"12345")
        self.assertEqual(status, 413)

        penpad.MAX_CHAT_BYTES = 8
        status, _, _ = self.request(
            "POST", "/chat", json.dumps({"text": "0123456789"}),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 413)

        penpad.MAX_UPLOAD_BYTES = 3
        status, _, _ = self.request("PUT", "/files/too-big.txt", b"1234")
        self.assertEqual(status, 413)
        self.assertFalse((penpad.FILES_DIR / "too-big.txt").exists())

    def test_chat_presence_and_metadata_events(self) -> None:
        status, _, payload = self.json_request(
            "POST", "/presence",
            {"id": "mini", "host": "box", "targets": ["mini", "agents"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["agent"]["id"], "mini")

        status, _, body = self.request("GET", "/presence")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["agents"][0]["id"], "mini")

        _, _, root = self.json_request(
            "POST", "/chat",
            {"author": "me", "text": "@mini start task", "mentions": ["mini"]},
        )
        root_id = root["message"]["id"]
        _, _, reply = self.json_request(
            "POST", "/chat",
            {"author": "mini", "text": "on it", "reply_to": root_id},
        )
        _, _, react = self.json_request(
            "POST", "/chat",
            {"author": "me", "text": "reacted", "kind": "react",
             "task_id": root_id, "reaction": "+1"},
        )
        _, _, unreact = self.json_request(
            "POST", "/chat",
            {"author": "me", "text": "removed", "kind": "unreact",
             "task_id": root_id, "reaction": "+1"},
        )
        _, _, pin = self.json_request(
            "POST", "/chat",
            {"author": "me", "text": "pinned", "kind": "pin", "task_id": root_id},
        )
        _, _, unpin = self.json_request(
            "POST", "/chat",
            {"author": "me", "text": "unpinned", "kind": "unpin", "task_id": root_id},
        )

        status, _, body = self.request("GET", "/chat?limit=10")
        self.assertEqual(status, 200)
        messages = {m["id"]: m for m in json.loads(body)["messages"]}
        self.assertEqual(messages[reply["message"]["id"]]["reply_to"], root_id)
        self.assertEqual(messages[react["message"]["id"]]["reaction"], "+1")
        self.assertEqual(messages[unreact["message"]["id"]]["kind"], "unreact")
        self.assertEqual(messages[pin["message"]["id"]]["task_id"], root_id)
        self.assertEqual(messages[unpin["message"]["id"]]["kind"], "unpin")

    def test_chat_reader_caps_memory_to_requested_limit(self) -> None:
        with penpad.CHAT_FILE.open("w", encoding="utf-8") as f:
            for i in range(1500):
                f.write(json.dumps({
                    "id": f"id{i}",
                    "ts": "2026-01-01T00:00:00Z",
                    "author": "load",
                    "text": str(i),
                    "mentions": [],
                    "kind": "message",
                }) + "\n")
        with penpad.LOCK:
            messages = penpad.read_chat_messages_locked(limit=3)
            after = penpad.read_chat_messages_locked(limit=3, since="id1497")
        self.assertEqual([m["id"] for m in messages], ["id1497", "id1498", "id1499"])
        self.assertEqual([m["id"] for m in after], ["id1498", "id1499"])

    def test_file_upload_range_delete_and_local_uri_copy(self) -> None:
        status, _, _ = self.request("PUT", "/files/demo.txt", b"abcdef")
        self.assertEqual(status, 201)

        status, _, body = self.request("GET", "/files/demo.txt",
                                       headers={"Range": "bytes=1-3"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"bcd")

        status, _, body = self.request("GET", "/files/")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["name"], "demo.txt")

        status, _, _ = self.request("DELETE", "/files/demo.txt")
        self.assertEqual(status, 200)
        self.assertFalse((penpad.FILES_DIR / "demo.txt").exists())

        src = self.base / "uri-source.txt"
        src.write_text("from uri", encoding="utf-8")
        status, _, body = self.request(
            "POST", "/upload-uri", src.as_uri(),
            {"Content-Type": "text/uri-list"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["copied"], ["uri-source.txt"])
        self.assertEqual((penpad.FILES_DIR / "uri-source.txt").read_text("utf-8"),
                         "from uri")

    def test_events_stream_sends_ready(self) -> None:
        req = urllib.request.Request(self.url + "/events", method="GET")
        with urllib.request.urlopen(req, timeout=5) as res:
            first = res.readline().decode("utf-8").strip()
            second = res.readline().decode("utf-8").strip()
        self.assertEqual(first, "event: ready")
        self.assertTrue(second.startswith("data: "))

    def test_daily_rollover_moves_today_into_week(self) -> None:
        stored = dt.date(2026, 4, 28)
        current = dt.date(2026, 4, 29)
        penpad.DATA_FILE.write_text("daily note\n\n\nwith gaps\n", encoding="utf-8")
        penpad.WEEK_FILE.write_text("existing week\n", encoding="utf-8")
        penpad.ARCHIVE_FILE.write_text("", encoding="utf-8")
        penpad.STATE_FILE.write_text(json.dumps({
            "today": stored.isoformat(),
            "week_start": penpad.week_start(stored).isoformat(),
        }), encoding="utf-8")
        penpad.local_today = lambda: current

        with penpad.LOCK:
            state = penpad.rollover_notes_locked()

        self.assertEqual(state["today"], current.isoformat())
        self.assertEqual(penpad.DATA_FILE.read_text("utf-8"), "")
        week = penpad.WEEK_FILE.read_text("utf-8")
        self.assertIn("existing week", week)
        self.assertIn("daily note\n\nwith gaps", week)
        self.assertEqual(penpad.ARCHIVE_FILE.read_text("utf-8"), "")

    def test_sunday_rollover_archives_completed_week_and_starts_clean(self) -> None:
        stored = dt.date(2026, 4, 26)
        current = dt.date(2026, 4, 27)
        penpad.DATA_FILE.write_text("sunday note\n", encoding="utf-8")
        penpad.WEEK_FILE.write_text("monday through saturday\n", encoding="utf-8")
        penpad.ARCHIVE_FILE.write_text("older archive\n", encoding="utf-8")
        penpad.STATE_FILE.write_text(json.dumps({
            "today": stored.isoformat(),
            "week_start": penpad.week_start(stored).isoformat(),
        }), encoding="utf-8")
        penpad.local_today = lambda: current

        with penpad.LOCK:
            penpad.rollover_notes_locked()

        self.assertEqual(penpad.DATA_FILE.read_text("utf-8"), "")
        self.assertEqual(penpad.WEEK_FILE.read_text("utf-8"), "")
        archive = penpad.ARCHIVE_FILE.read_text("utf-8")
        self.assertIn("older archive", archive)
        self.assertIn("monday through saturday", archive)
        self.assertIn("sunday note", archive)
        self.assertIn("Week of Apr 20-26, 2026", archive)
        self.assertNotIn("\n\n\n", archive)

    def test_safe_name_rejects_path_tricks(self) -> None:
        self.assertEqual(penpad.safe_name("hello.txt"), "hello.txt")
        self.assertIsNone(penpad.safe_name("../secret"))
        self.assertIsNone(penpad.safe_name(".env"))
        self.assertIsNone(penpad.safe_name("nested/file.txt"))


if __name__ == "__main__":
    unittest.main()
