#!/usr/bin/env python3
"""Watch routed penpad chat messages for this machine/agent.

By default this prints matching messages as JSON. If --exec is provided, the
command receives each matching message JSON on stdin. The chat text is never
executed by this script; local agent harnesses decide what to do with it.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def auth_headers(token: str | None, extra: dict | None = None) -> dict:
    headers = dict(extra or {})
    if token:
        headers["X-Penpad-Token"] = token
    return headers


def fetch_chat(url: str, since: str | None) -> list[dict]:
    query = f"?limit=200&since={urllib.request.quote(since)}" if since else "?limit=200"
    with urllib.request.urlopen(url.rstrip("/") + "/chat" + query, timeout=15) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return payload.get("messages", [])


def post_chat(url: str, author: str, text: str, token: str | None,
              kind: str = "message", task_id: str = "") -> None:
    body = json.dumps({
        "author": author,
        "text": text,
        "kind": kind,
        "task_id": task_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/chat",
        data=body,
        headers=auth_headers(token, {"Content-Type": "application/json"}),
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15).read()


def post_presence(url: str, identity: str, targets: set[str], token: str | None,
                  status: str) -> None:
    body = json.dumps({
        "id": identity,
        "name": identity,
        "host": socket.gethostname().split(".")[0],
        "role": "agent",
        "status": status,
        "targets": sorted(targets),
    }).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/presence",
        data=body,
        headers=auth_headers(token, {"Content-Type": "application/json"}),
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15).read()


def stream_events(url: str):
    req = urllib.request.Request(
        url.rstrip("/") + "/events",
        headers={"Accept": "text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        event = "message"
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                event = "message"
                continue
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event:
                yield event


def read_state(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("last_id") if isinstance(data, dict) else None
    return str(value) if value else None


def write_state(path: Path, last_id: str | None) -> None:
    if not last_id:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"last_id": last_id}, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def matches(msg: dict, targets: set[str]) -> bool:
    mentions = {str(m).lower().lstrip("@") for m in msg.get("mentions", [])}
    return bool(mentions & targets)


def process_messages(messages: list[dict], targets: set[str], args, last_id: str | None) -> str | None:
    if messages:
        last_id = messages[-1].get("id", last_id)
        if not args.no_state:
            write_state(args.state_path, last_id)
    for msg in messages:
        if not matches(msg, targets):
            continue
        encoded = json.dumps(msg, ensure_ascii=False)
        print(encoded, flush=True)
        if args.ack:
            author = msg.get("author") or "there"
            post_chat(args.url, args.identity,
                      f"@{author} {args.identity} saw {msg.get('id', '')[:8]}",
                      args.token, kind="ack", task_id=msg.get("id", ""))
        if args.command:
            subprocess.run(args.command, input=encoded, text=True, shell=True, check=False)
    return last_id


def main() -> int:
    host = socket.gethostname().split(".")[0]
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.environ.get("PENPAD_URL", "http://127.0.0.1:8767"))
    p.add_argument("--identity", default=os.environ.get("PENPAD_AGENT", host))
    p.add_argument("--token", default=os.environ.get("PENPAD_TOKEN", ""))
    p.add_argument("--target", action="append", default=[],
                   help="Mention this watcher should react to. Defaults to identity, hostname, all, agents.")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--status", default="listening")
    p.add_argument("--state", default="",
                   help="Path for last-seen state. Defaults to ~/.penpad-agent-watch-<identity>.json")
    p.add_argument("--no-state", action="store_true",
                   help="Do not persist the last processed chat id.")
    p.add_argument("--sse", action="store_true",
                   help="Use /events as a wakeup signal, with polling as a fallback.")
    p.add_argument("--replay", action="store_true", help="Process existing matching messages on startup.")
    p.add_argument("--exec", dest="command",
                   help="Optional local command to run for each matching message; message JSON is stdin.")
    p.add_argument("--ack", action="store_true", help="Post a lightweight acknowledgement when a message is received.")
    args = p.parse_args()

    targets = {args.identity.lower(), host.lower(), "all", "agents", *[t.lower().lstrip("@") for t in args.target]}
    if args.no_state:
        args.state_path = Path(os.devnull)
        last_id = None
    else:
        args.state_path = Path(args.state or f"~/.penpad-agent-watch-{args.identity}.json").expanduser()
        last_id = read_state(args.state_path)
    if args.replay:
        last_id = None
    initialized = bool(last_id) or args.replay
    last_presence = 0.0
    fail_sleep = args.interval

    def heartbeat():
        nonlocal last_presence
        now = time.monotonic()
        if now - last_presence < 15:
            return
        post_presence(args.url, args.identity, targets, args.token, args.status)
        last_presence = now

    while True:
        try:
            heartbeat()
            messages = fetch_chat(args.url, last_id)
            if messages:
                last_id = messages[-1].get("id", last_id)
                if not args.no_state:
                    write_state(args.state_path, last_id)
            if not initialized and not args.replay:
                initialized = True
                time.sleep(args.interval)
                continue
            initialized = True
            last_id = process_messages(messages, targets, args, last_id)
            if args.sse:
                for event in stream_events(args.url):
                    heartbeat()
                    if event not in ("chat", "ready"):
                        continue
                    messages = fetch_chat(args.url, last_id)
                    last_id = process_messages(messages, targets, args, last_id)
            fail_sleep = args.interval
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[penpad-agent-watch] {e}", file=sys.stderr, flush=True)
            time.sleep(fail_sleep)
            fail_sleep = min(30.0, max(args.interval, fail_sleep * 1.8))
            continue
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
