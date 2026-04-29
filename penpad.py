#!/usr/bin/env python3
"""penpad — shared notes + file drop, self-hosted. http://<host>:8767/"""
from __future__ import annotations

import http.server
import datetime as dt
import json
import mimetypes
import os
import re
import socketserver
import time
import threading
import urllib.parse
import uuid
from collections import deque
from pathlib import Path

PORT = 8767
BASE = Path(__file__).parent
__version__ = "0.2.0"

def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default

DATA_FILE = BASE / "penpad.txt"
WEEK_FILE = BASE / "penpad.week.txt"
ARCHIVE_FILE = BASE / "penpad.archive.txt"
STATE_FILE = BASE / "penpad.state.json"
CHAT_FILE = BASE / "penpad.chat.jsonl"
PRESENCE_FILE = BASE / "penpad.presence.json"
FILES_DIR = BASE / "files"
PENPAD_TOKEN = os.environ.get("PENPAD_TOKEN", "")
MAX_NOTE_BYTES = max(1, env_int("PENPAD_MAX_NOTE_MB", 5)) * 1024 * 1024
MAX_CHAT_BYTES = max(1, env_int("PENPAD_MAX_CHAT_KB", 64)) * 1024
MAX_UPLOAD_BYTES = max(1, env_int("PENPAD_MAX_UPLOAD_MB", 512)) * 1024 * 1024
DATA_FILE.touch(exist_ok=True)
WEEK_FILE.touch(exist_ok=True)
ARCHIVE_FILE.touch(exist_ok=True)
CHAT_FILE.touch(exist_ok=True)
PRESENCE_FILE.touch(exist_ok=True)
FILES_DIR.mkdir(exist_ok=True)
LOCK = threading.Lock()
SSE_SEM = threading.BoundedSemaphore(32)

NOTE_ORDER = ("today", "week", "archive")
NOTE_FILES = {"today": DATA_FILE, "week": WEEK_FILE, "archive": ARCHIVE_FILE}
NOTE_LABELS = {"today": "Today", "week": "Week (M-Su)", "archive": "Archive"}
NOTE_COLORS = {"today": "#f5c842", "week": "#68c3a3", "archive": "#c982d4"}
MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_.-]{1,64})")
TASK_STATUS_KINDS = {"claim", "working", "done", "blocked"}
TASK_ACTIVE_STATUSES = {"open", "claimed", "working", "blocked"}
CHAT_DERIVED_KINDS = {
    "react", "unreact", "pin", "unpin", "attach", "summary", "proposal",
    "metadata", *TASK_STATUS_KINDS,
}
PRIORITY_VALUES = {"low", "normal", "high", "urgent"}

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".log", ".json", ".jsonl", ".ndjson", ".xml",
    ".csv", ".tsv", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg", ".env",
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css", ".scss",
    ".sass", ".less", ".html", ".htm", ".xhtml", ".sh", ".bash", ".zsh",
    ".fish", ".sql", ".c", ".cc", ".h", ".hpp", ".cpp", ".cs", ".java", ".rb",
    ".go", ".rs", ".swift", ".kt", ".php", ".pl", ".lua", ".r", ".jl", ".tex",
    ".rst", ".adoc", ".vue", ".svelte",
}
INLINE_APP_TYPES = {"application/pdf", "application/json", "application/xml",
                    "application/javascript"}

MANIFEST = json.dumps({
    "name": "penpad",
    "short_name": "penpad",
    "description": "Self-hosted notes, file drop, and agent command room",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0e0e10",
    "theme_color": "#0e0e10",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
               "purpose": "any maskable"}],
})

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="120" fill="#0e0e10"/>
  <g transform="translate(256 256) rotate(-45) translate(-256 -256)">
    <rect x="228" y="110" width="56" height="232" rx="10" fill="#d4a373"/>
    <rect x="228" y="158" width="56" height="9" fill="#0e0e10"/>
    <polygon points="228,342 284,342 256,414" fill="#d4a373"/>
    <line x1="256" y1="342" x2="256" y2="404" stroke="#0e0e10" stroke-width="5" stroke-linecap="round"/>
  </g>
</svg>"""

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="theme-color" content="#0e0e10">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="penpad">
<title>penpad</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<style>
  :root {
    --bg: #0e0e10;
    --surface: #16161a;
    --surface-2: #1c1c22;
    --border: #24242a;
    --border-subtle: #1a1a1f;
    --text: #e6e3dc;
    --text-muted: #7a7872;
    --text-dim: #4d4c47;
    --accent: #d4a373;
    --accent-soft: rgba(212, 163, 115, 0.10);
    --accent-glow: rgba(212, 163, 115, 0.28);
    --success: #7fb069;
    --danger: #c97064;
    --info: #7d9eb8;
    --note-bg: #0e0e10;
    --note-accent: #f5c842;
    --font-mono: 'SF Mono', 'Berkeley Mono', 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
  }

  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }

  html, body {
    margin: 0; height: 100%;
    width: 100%; max-width: 100%;
    background: var(--note-bg); color: var(--text);
    font-family: var(--font-mono);
    -webkit-font-smoothing: antialiased;
    overscroll-behavior: none;
    overflow-x: hidden;
    -webkit-text-size-adjust: 100%;
  }

  body {
    display: flex; flex-direction: column;
    padding-top: env(safe-area-inset-top);
    padding-bottom: env(safe-area-inset-bottom);
    padding-left: env(safe-area-inset-left);
    padding-right: env(safe-area-inset-right);
    transition: background 0.2s ease;
    overflow-x: hidden;
  }
  body.note-today { --note-bg: #0e0e10; --note-accent: #f5c842; }
  body.note-week { --note-bg: #0b1614; --note-accent: #68c3a3; }
  body.note-archive { --note-bg: #171017; --note-accent: #c982d4; }

  /* Top bar */
  header#bar {
    flex: 0 0 auto;
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px;
    background: var(--bg);
    border-bottom: 1px solid var(--border-subtle);
    position: sticky; top: 0; z-index: 10;
    order: 0;
  }

  nav.tabs {
    display: flex; gap: 2px;
    padding: 0;
    background: transparent;
    border: 0;
  }

  .tab {
    display: inline-flex; align-items: center; gap: 0;
    background: transparent; border: 0;
    padding: 6px 8px;
    border-radius: 7px;
    color: var(--text-dim);
    font-family: inherit; font-size: 12px;
    cursor: pointer;
    transition: background 0.18s ease, color 0.18s ease, padding 0.22s ease, gap 0.22s ease, box-shadow 0.18s ease;
  }
  .tab:hover { color: var(--text-muted); }
  .tab.active {
    background: var(--surface-2);
    color: var(--text);
    padding: 6px 12px 6px 8px;
    gap: 8px;
    box-shadow: inset 0 0 0 1px var(--border), 0 1px 2px rgba(0, 0, 0, 0.25);
  }
  .tab svg { width: 16px; height: 16px; flex: 0 0 16px; display: block; }
  .tab .label {
    max-width: 0; overflow: hidden; white-space: nowrap; opacity: 0;
    transition: max-width 0.22s ease, opacity 0.18s ease;
    letter-spacing: 0.02em;
  }
  .tab.active .label { max-width: 80px; opacity: 1; }

  #status {
    margin-left: auto;
    margin-right: 64px;
    display: flex; align-items: center; gap: 7px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-muted);
  }
  #status::before {
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: var(--text-dim);
    transition: background 0.2s, box-shadow 0.2s;
  }
  #status.ok::before { background: var(--success); box-shadow: 0 0 8px rgba(127, 176, 105, 0.5); }
  #status.warn::before { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  #status.err::before { background: var(--danger); box-shadow: 0 0 8px rgba(201, 112, 100, 0.5); }

  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

  /* Views */
  section.view {
    flex: 1 1 auto;
    display: none; flex-direction: column;
    overflow: hidden;
    min-height: 0;
    width: 100%;
    max-width: 100vw;
  }
  section.view.active { display: flex; animation: viewIn 0.24s ease; }

  @keyframes viewIn {
    from { opacity: 0; transform: translateY(3px); }
    to { opacity: 1; transform: none; }
  }

  /* Text view */
  section.view[data-view="text"] {
    position: relative;
    background:
      radial-gradient(circle at 50% 0%, color-mix(in srgb, var(--note-accent) 12%, transparent), transparent 34%),
      var(--note-bg);
    transition: background 0.2s ease;
  }
  #copyAll {
    position: absolute;
    top: 14px; right: 16px;
    z-index: 5;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-muted);
    width: 32px; height: 32px;
    border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    transition: opacity 0.18s, color 0.15s, background 0.15s;
  }
  #copyAll svg { width: 14px; height: 14px; }
  #copyAll:hover { color: var(--accent); background: var(--surface-2); }

  #noteSwitch {
    position: absolute;
    top: 14px; left: 50%;
    transform: translateX(-50%);
    z-index: 6;
    display: flex; align-items: center; justify-content: center; gap: 10px;
    padding: 8px 10px;
    border-radius: 999px;
    background: rgba(14, 14, 16, 0.48);
    border: 1px solid rgba(230, 227, 220, 0.08);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
  }
  .note-dot {
    width: 13px; height: 13px;
    border: 0;
    border-radius: 50%;
    padding: 0;
    background: var(--dot);
    cursor: pointer;
    opacity: 0.44;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35), 0 0 0 transparent;
    transition: transform 0.16s ease, opacity 0.16s ease, box-shadow 0.16s ease;
  }
  .note-dot:hover { opacity: 0.8; transform: scale(1.08); }
  .note-dot.active {
    opacity: 1;
    transform: scale(1.2);
    box-shadow: 0 0 0 2px rgba(230, 227, 220, 0.18), 0 0 16px color-mix(in srgb, var(--dot) 55%, transparent);
  }

  #noteSearch {
    position: absolute;
    top: 14px; left: 16px;
    z-index: 7;
  }
  #noteSearchBtn {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-muted);
    width: 32px; height: 32px;
    border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    transition: color 0.15s, background 0.15s, border-color 0.15s;
  }
  #noteSearchBtn svg { width: 14px; height: 14px; }
  #noteSearchBtn:hover,
  #noteSearch.open #noteSearchBtn {
    color: var(--note-accent);
    background: var(--surface-2);
    border-color: color-mix(in srgb, var(--note-accent) 45%, var(--border));
  }
  #noteSearchPanel {
    display: none;
    position: absolute;
    top: 40px; left: 0;
    width: min(420px, calc(100vw - 32px));
    max-height: min(430px, calc(100vh - 150px));
    overflow: hidden;
    background: rgba(22, 22, 26, 0.96);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
  }
  #noteSearch.open #noteSearchPanel { display: flex; flex-direction: column; }
  #noteQuery {
    width: 100%;
    background: transparent;
    border: 0;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text);
    outline: 0;
    font: inherit;
    font-size: 13px;
    padding: 12px 14px;
  }
  #noteQuery::placeholder { color: var(--text-dim); }
  #noteResults {
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    max-height: 360px;
    padding: 6px;
  }
  .note-result {
    width: 100%;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    text-align: left;
    padding: 10px;
    border-radius: 7px;
    cursor: pointer;
    display: grid;
    gap: 5px;
  }
  .note-result:hover { background: var(--surface-2); }
  .note-result .src {
    color: var(--note-accent);
    font-size: 10px;
    letter-spacing: 0.13em;
    text-transform: uppercase;
  }
  .note-result .snippet {
    color: var(--text-muted);
    font-size: 12px;
    line-height: 1.45;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .note-result mark {
    background: color-mix(in srgb, var(--note-accent) 25%, transparent);
    color: var(--text);
    border-radius: 3px;
    padding: 0 2px;
  }
  .note-result-empty {
    color: var(--text-dim);
    font-size: 11px;
    letter-spacing: 0.08em;
    padding: 18px 14px;
    text-transform: uppercase;
  }
  @media (hover: hover) {
    #copyAll, #noteSearchBtn { opacity: 0; }
    section[data-view="text"]:hover #copyAll,
    section[data-view="text"]:focus-within #copyAll,
    section[data-view="text"]:hover #noteSearchBtn,
    section[data-view="text"]:focus-within #noteSearchBtn,
    #noteSearch.open #noteSearchBtn { opacity: 0.7; }
    #copyAll:hover, #noteSearchBtn:hover { opacity: 1 !important; }
  }

  /* Sort button */
  #sortBtn {
    background: transparent; border: 0;
    color: var(--text-muted);
    font-family: inherit; font-size: 11px;
    padding: 5px 8px;
    border-radius: 6px;
    cursor: pointer;
    display: inline-flex; align-items: center; gap: 5px;
    letter-spacing: 0.06em;
    transition: color 0.15s, background 0.15s;
    text-transform: lowercase;
  }
  #sortBtn:hover { color: var(--text); background: var(--surface-2); }
  #sortBtn svg { width: 11px; height: 11px; opacity: 0.7; }

  #pad {
    flex: 1 1 auto;
    width: 100%;
    max-width: 100%;
    min-width: 0;
    background: transparent;
    color: var(--text);
    border: 0; outline: 0;
    padding: 66px 26px 22px;
    font-family: inherit;
    font-size: 14px; line-height: 1.65;
    resize: none;
    overflow-x: hidden;
    caret-color: var(--note-accent);
  }
  #pad::selection { background: var(--accent-soft); color: var(--text); }
  #pad::placeholder { color: var(--text-dim); font-style: italic; }
  #pad[readonly] {
    color: color-mix(in srgb, var(--text) 86%, var(--note-accent));
    caret-color: transparent;
  }
  #readShield {
    display: none;
    position: absolute;
    inset: 52px 0 0 0;
    z-index: 4;
    background: transparent;
    touch-action: auto;
  }
  section.view[data-view="text"].read-mode #readShield {
    display: block;
  }

  /* Files view — upload control lives in the top bar */
  #upload-group {
    display: inline-flex; align-items: center; gap: 10px;
    margin-left: 20px;
  }
  body:not(.has-files) #upload-group { display: none; }
  .drop-hint {
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 0.04em;
  }

  .picker-label {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 8px 16px;
    background: #f5c842;
    color: #0e0e10;
    font-weight: 600;
    letter-spacing: 0.02em;
    border-radius: 7px;
    cursor: pointer;
    font-size: 12px;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
    transition: background 0.15s ease, transform 0.08s ease, box-shadow 0.18s ease;
  }
  .picker-label:hover { background: #ffd84d; box-shadow: 0 3px 12px rgba(245, 200, 66, 0.30); }
  .picker-label:active { transform: translateY(1px); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35); }
  .picker-label svg { width: 14px; height: 14px; }
  #picker { display: none; }
  #fstatus {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--text-dim);
  }
  #fstatus:empty { display: none; }

  /* Upload progress bar — shown inline in the bottom bar during a PUT.
     Fills left-to-right in the tan accent; auto-hides when idle. */
  #upprogress {
    flex: 0 0 auto;
    width: 120px;
    height: 6px;
    border-radius: 3px;
    overflow: hidden;
    background: var(--border);
    position: relative;
  }
  #upprogress .fill {
    position: absolute; inset: 0 auto 0 0;
    width: 0%;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent-glow);
    transition: width 0.18s ease;
  }
  #upprogress.hidden { display: none; }

  #filter-row {
    flex: 0 0 auto;
    margin: 0 14px 8px;
    padding: 8px 12px;
    background: var(--surface);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    display: flex; align-items: center; gap: 10px;
  }
  #filter-row svg { width: 14px; height: 14px; color: var(--text-dim); flex: 0 0 14px; }
  #ffilter {
    flex: 1; background: transparent; border: 0; outline: 0;
    color: var(--text); font-family: inherit; font-size: 13px;
    min-width: 0; padding: 2px 0;
  }
  #ffilter::placeholder { color: var(--text-dim); }
  #ffilter::-webkit-search-cancel-button { display: none; }
  #filter-row .count {
    color: var(--text-dim); font-size: 10px;
    letter-spacing: 0.1em; text-transform: uppercase; white-space: nowrap;
  }

  #counter {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--text-dim);
    white-space: nowrap;
  }
  #counter:empty { display: none; }
  body:not(.has-text) #counter { display: none; }

  #toast {
    position: fixed;
    bottom: calc(24px + env(safe-area-inset-bottom));
    left: 50%; transform: translateX(-50%) translateY(8px);
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 10px 16px;
    border-radius: 999px;
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
    opacity: 0;
    pointer-events: none;
    z-index: 10001;
    transition: opacity 0.18s ease, transform 0.22s ease;
  }
  #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  #toast .dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--success); margin-right: 8px; vertical-align: middle;
    box-shadow: 0 0 6px rgba(127, 176, 105, 0.5);
  }

  #flist {
    flex: 1 1 auto;
    overflow: auto;
    overflow-x: hidden;
    padding: 0 14px 24px;
    -webkit-overflow-scrolling: touch;
    max-width: 100%;
  }

  .row {
    display: grid;
    grid-template-columns: auto auto 1fr auto auto auto auto;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: 8px;
    margin-bottom: 2px;
    font-size: 13px;
    cursor: grab;
    min-width: 0;
    transition: background 0.15s;
    animation: rowIn 0.28s ease backwards;
  }

  .thumb {
    width: 36px; height: 36px; flex: 0 0 36px;
    border-radius: 6px;
    background: var(--surface);
    display: flex; align-items: center; justify-content: center;
    overflow: hidden;
    color: var(--text-dim);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
  }
  .thumb svg { width: 16px; height: 16px; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb.img { cursor: zoom-in; }
  .thumb.img:hover { transform: scale(1.06); box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45); }
  .row:active { cursor: grabbing; }
  .row:hover { background: var(--surface); }

  @keyframes rowIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: none; }
  }

  .row.flash { animation: flashIn 1.6s ease-out; }
  @keyframes flashIn {
    0%   { background: var(--accent-glow); box-shadow: inset 0 0 0 1px var(--accent); }
    100% { background: transparent; box-shadow: none; }
  }

  .grip { color: var(--text-dim); display: flex; }
  .grip svg { width: 10px; height: 14px; }

  .name {
    color: var(--text); text-decoration: none; cursor: pointer;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    min-width: 0;
    transition: color 0.15s;
  }
  .name:hover { color: var(--accent); }

  .meta {
    color: var(--text-dim);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    white-space: nowrap;
  }

  .iconbtn {
    background: transparent; border: 0;
    color: var(--text-muted);
    cursor: pointer;
    width: 30px; height: 30px;
    display: inline-flex; align-items: center; justify-content: center;
    border-radius: 6px;
    transition: background 0.15s, color 0.15s;
  }
  .iconbtn svg { width: 14px; height: 14px; }
  .iconbtn:hover { background: var(--surface-2); color: var(--text); }
  .iconbtn:disabled { cursor: wait; opacity: 0.55; }

  .dl:hover { color: var(--info); }
  .dl.done { color: var(--success); }
  .dl.done:hover { background: rgba(127, 176, 105, 0.10); color: var(--success); }
  .copy:hover { color: var(--accent); }
  .del:hover { background: rgba(201, 112, 100, 0.12); color: var(--danger); }

  /* Chat view */
  section.view[data-view="chat"] {
    background: var(--bg);
    border-top: 1px solid var(--border-subtle);
  }
  #chatList {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 16px 14px 18px;
    -webkit-overflow-scrolling: touch;
  }
  #chatPinned {
    display: none;
    flex: 0 0 auto;
    gap: 8px;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 9px 14px;
    border-bottom: 1px solid var(--border-subtle);
    background: rgba(245, 200, 66, 0.06);
    -webkit-overflow-scrolling: touch;
  }
  #chatPinned.open { display: flex; }
  #taskBoard {
    display: none;
    flex: 0 0 auto;
    border-bottom: 1px solid var(--border-subtle);
    background: rgba(230, 227, 220, 0.025);
    padding: 10px 14px;
    overflow-x: auto;
    overflow-y: hidden;
    gap: 8px;
    -webkit-overflow-scrolling: touch;
  }
  #taskBoard.open { display: flex; }
  .task-summary {
    flex: 0 0 auto;
    display: grid;
    gap: 3px;
    min-width: 84px;
    align-content: center;
    color: var(--text-muted);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .task-summary strong {
    color: var(--text);
    font-size: 15px;
    letter-spacing: 0;
  }
  .task-card {
    flex: 0 0 min(360px, 82vw);
    display: grid;
    gap: 7px;
    padding: 9px 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: rgba(14, 14, 16, 0.56);
  }
  .task-card.blocked { border-color: rgba(201, 112, 100, 0.28); }
  .task-card.working { border-color: rgba(125, 158, 184, 0.28); }
  .task-card.claimed { border-color: rgba(245, 200, 66, 0.22); }
  .task-row {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    min-width: 0;
  }
  .task-status,
  .task-target,
  .task-priority {
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 2px 7px;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
  }
  .task-status { color: var(--info); background: rgba(125, 158, 184, 0.08); }
  .task-priority.high,
  .task-priority.urgent { color: var(--danger); background: rgba(201, 112, 100, 0.08); }
  .task-title {
    color: var(--text);
    font-size: 12px;
    line-height: 1.4;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .task-actions {
    display: flex; flex-wrap: wrap; gap: 5px;
  }
  .task-actions button {
    border: 1px solid var(--border);
    background: rgba(230, 227, 220, 0.05);
    color: var(--text-muted);
    border-radius: 999px;
    padding: 4px 8px;
    font: inherit;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
  }
  .task-actions button:hover { color: var(--text); background: var(--surface-2); }
  .pin-card {
    flex: 0 0 min(320px, 78vw);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 4px 8px;
    align-items: center;
    border: 1px solid rgba(245, 200, 66, 0.18);
    background: rgba(14, 14, 16, 0.58);
    color: var(--text);
    border-radius: 8px;
    padding: 8px 10px;
    font: inherit;
    text-align: left;
  }
  .pin-card button {
    border: 0;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    font-size: 11px;
  }
  .pin-card button:hover { color: var(--accent); }
  .pin-card .pin-author {
    color: var(--accent);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .pin-card .pin-text {
    grid-column: 1 / -1;
    color: var(--text-muted);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .chat-msg {
    display: grid;
    gap: 6px;
    padding: 10px 12px;
    border-radius: 8px;
    margin-bottom: 4px;
    background: transparent;
    border: 1px solid transparent;
  }
  .chat-msg.targeted {
    background: rgba(125, 158, 184, 0.08);
    border-color: rgba(125, 158, 184, 0.18);
  }
  .chat-meta {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    color: var(--text-dim);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .chat-author { color: var(--accent); }
  .chat-target {
    color: var(--info);
    background: rgba(125, 158, 184, 0.12);
    border: 1px solid rgba(125, 158, 184, 0.18);
    border-radius: 999px;
    padding: 2px 7px;
    letter-spacing: 0.06em;
  }
  .chat-text {
    color: var(--text);
    font-size: 13px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .chat-text .mention {
    color: var(--info);
    background: rgba(125, 158, 184, 0.10);
    border-radius: 4px;
    padding: 0 2px;
  }
  .chat-files {
    display: flex; flex-wrap: wrap; gap: 6px;
  }
  .chat-file-chip {
    display: inline-flex; align-items: center; gap: 6px;
    max-width: 100%;
    min-width: 0;
    border: 1px solid var(--border);
    background: rgba(230, 227, 220, 0.05);
    color: var(--text-muted);
    border-radius: 999px;
    padding: 5px 8px;
    font: inherit;
    font-size: 11px;
    text-decoration: none;
    cursor: pointer;
  }
  .chat-file-chip:hover { color: var(--text); background: var(--surface-2); }
  .chat-file-chip span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chat-file-chip svg {
    flex: 0 0 auto;
    width: 13px; height: 13px;
  }
  .chat-reply {
    border-left: 2px solid var(--info);
    color: var(--text-muted);
    background: rgba(125, 158, 184, 0.07);
    border-radius: 0 6px 6px 0;
    padding: 6px 8px;
    font-size: 11px;
    line-height: 1.35;
    cursor: pointer;
  }
  .chat-reply:hover { color: var(--text); }
  .chat-reply .who {
    color: var(--info);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 10px;
    margin-right: 5px;
  }
  .chat-msg.jump {
    animation: jumpFlash 1.2s ease;
  }
  .chat-unread {
    display: flex; align-items: center; gap: 10px;
    margin: 12px 0;
    color: var(--accent);
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .chat-unread::before,
  .chat-unread::after {
    content: '';
    height: 1px;
    background: rgba(245, 200, 66, 0.26);
    flex: 1 1 auto;
  }
  .chat-unread button {
    border: 1px solid rgba(245, 200, 66, 0.22);
    background: rgba(245, 200, 66, 0.08);
    color: var(--accent);
    border-radius: 999px;
    padding: 4px 8px;
    font: inherit;
    font-size: 10px;
    cursor: pointer;
  }
  .chat-unread button:hover { background: rgba(245, 200, 66, 0.14); }
  @keyframes jumpFlash {
    0% { border-color: var(--accent); background: rgba(245, 200, 66, 0.13); }
    100% { border-color: transparent; background: transparent; }
  }
  .chat-reactions {
    display: flex; flex-wrap: wrap; gap: 5px;
  }
  .chat-reaction {
    background: rgba(230, 227, 220, 0.05);
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 999px;
    padding: 3px 7px;
    font: inherit;
    font-size: 10px;
    cursor: pointer;
  }
  .chat-reaction:hover,
  .chat-reaction.active {
    color: var(--text);
    background: rgba(125, 158, 184, 0.12);
    border-color: rgba(125, 158, 184, 0.22);
  }
  .chat-actions {
    display: flex; flex-wrap: wrap; gap: 6px;
    margin-top: 2px;
  }
  .chat-action {
    background: rgba(230, 227, 220, 0.05);
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 999px;
    padding: 5px 9px;
    font: inherit;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
  }
  .chat-action:hover { color: var(--text); background: var(--surface-2); }
  .chat-kind {
    color: var(--success);
    background: rgba(127, 176, 105, 0.10);
    border: 1px solid rgba(127, 176, 105, 0.18);
    border-radius: 999px;
    padding: 2px 7px;
    letter-spacing: 0.06em;
  }
  #chatForm {
    flex: 0 0 auto;
    display: grid;
    grid-template-columns: minmax(96px, 140px) 1fr auto;
    position: relative;
    gap: 8px;
    padding: 10px 14px calc(10px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--border-subtle);
    background: var(--bg);
  }
  #replyBar {
    display: none;
    grid-column: 1 / -1;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    background: rgba(125, 158, 184, 0.08);
    border: 1px solid rgba(125, 158, 184, 0.18);
    border-radius: 7px;
    padding: 8px 10px;
    color: var(--text-muted);
    font-size: 11px;
    min-width: 0;
  }
  #replyBar.open { display: flex; }
  #replyBar .reply-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  #replyBar button {
    border: 0;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    font-size: 14px;
  }
  #replyBar button:hover { color: var(--text); }
  #chatSuggest {
    display: none;
    position: absolute;
    left: 160px;
    right: 86px;
    bottom: calc(100% + 6px);
    max-height: 180px;
    overflow: auto;
    background: rgba(22, 22, 26, 0.98);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 14px 34px rgba(0, 0, 0, 0.42);
    padding: 5px;
    z-index: 20;
  }
  #chatSuggest.open { display: grid; gap: 3px; }
  .chat-suggest-item {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    width: 100%;
    border: 0;
    background: transparent;
    color: var(--text);
    border-radius: 6px;
    padding: 8px 9px;
    font: inherit;
    font-size: 12px;
    text-align: left;
    cursor: pointer;
  }
  .chat-suggest-item:hover,
  .chat-suggest-item.active { background: var(--surface-2); }
  .chat-suggest-meta {
    color: var(--text-dim);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  #chatAuthor, #chatText {
    min-width: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font: inherit;
    font-size: 13px;
    border-radius: 7px;
    outline: 0;
    padding: 10px 12px;
  }
  #chatAuthor::placeholder,
  #chatText::placeholder { color: var(--text-dim); }
  #chatSend {
    background: #f5c842;
    color: #0e0e10;
    border: 0;
    border-radius: 7px;
    padding: 0 16px;
    font: inherit;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }
  #chatSend:hover { background: #ffd84d; }

  .empty {
    color: var(--text-dim);
    text-align: center;
    padding: 64px 14px;
    font-size: 12px;
    display: flex; flex-direction: column; align-items: center; gap: 14px;
  }
  .empty svg { width: 36px; height: 36px; opacity: 0.4; }
  .empty .hint { font-style: italic; letter-spacing: 0.04em; }

  /* Drop overlay */
  #overlay {
    position: fixed; inset: 0;
    background: rgba(14, 14, 16, 0.82);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    display: none;
    flex-direction: column; align-items: center; justify-content: center;
    gap: 18px;
    color: var(--accent);
    z-index: 9999;
    pointer-events: none;
    box-shadow: inset 0 0 0 4px var(--accent-glow);
  }
  #overlay.show { display: flex; animation: fadeIn 0.18s ease; }
  #overlay svg { width: 64px; height: 64px; opacity: 0.95; }
  #overlay .text {
    font-size: 13px; letter-spacing: 0.32em;
    text-transform: uppercase;
    font-weight: 500;
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: scale(0.985); }
    to { opacity: 1; transform: none; }
  }

  /* Lightbox / preview modal */
  #lightbox {
    position: fixed; inset: 0;
    background: rgba(10, 10, 12, 0.92);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    display: none;
    flex-direction: column; align-items: center; justify-content: center;
    padding: 60px 24px 40px;
    gap: 14px;
    z-index: 10000;
  }
  #lightbox.show { display: flex; animation: fadeIn 0.2s ease; }

  #lightbox .lb-content {
    width: 100%;
    flex: 1 1 auto;
    min-height: 0;
    display: flex; align-items: center; justify-content: center;
    cursor: zoom-out;
    overflow: hidden;
    touch-action: none;
  }
  #lightbox .lb-content > * { cursor: default; }

  #lightbox .lb-content img,
  #lightbox .lb-content video {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    border-radius: 10px;
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.65);
    background: var(--surface);
  }
  #lightbox .lb-content img.zoomable {
    touch-action: none;
    transform-origin: center center;
    will-change: transform;
    user-select: none;
    -webkit-user-drag: none;
  }
  #lightbox.zoomed .lb-content { cursor: grab; }
  #lightbox.zoomed .lb-content:active { cursor: grabbing; }

  #lightbox .lb-content iframe {
    width: min(1100px, 92vw);
    height: 100%;
    min-height: 50vh;
    border: 0;
    border-radius: 10px;
    background: var(--surface);
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.65);
  }

  #lightbox .audio-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 30px 36px;
    display: flex; flex-direction: column; align-items: center; gap: 22px;
    min-width: min(420px, 90vw);
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.5);
  }
  #lightbox .audio-card svg {
    width: 56px; height: 56px;
    color: var(--accent);
    opacity: 0.85;
  }
  #lightbox .audio-card audio {
    width: 100%;
    min-width: min(360px, 80vw);
  }

  #lightbox .text-pre {
    background: var(--surface);
    color: var(--text);
    padding: 22px 26px;
    margin: 0;
    border-radius: 10px;
    width: min(960px, 92vw);
    max-height: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    text-align: left;
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.65);
    border: 1px solid var(--border);
    -webkit-overflow-scrolling: touch;
  }

  #lightbox .lb-msg {
    color: var(--text-muted);
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    padding: 30px;
  }

  #lightbox .lb-name {
    color: var(--text-muted);
    font-size: 11px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    max-width: 80vw;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    flex: 0 0 auto;
  }

  #lightbox .lb-close {
    position: absolute;
    top: 16px; right: 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-muted);
    width: 36px; height: 36px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
    z-index: 1;
  }
  #lightbox .lb-close:hover { background: var(--surface-2); color: var(--text); }
  #lightbox .lb-close svg { width: 14px; height: 14px; }

  #lightbox .lb-nav {
    position: absolute;
    top: 50%; transform: translateY(-50%);
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-muted);
    width: 40px; height: 40px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    transition: background 0.15s, color 0.15s, opacity 0.15s;
    z-index: 1;
  }
  #lightbox .lb-nav:hover { background: var(--surface-2); color: var(--text); }
  #lightbox .lb-nav:disabled { opacity: 0.25; cursor: default; }
  #lightbox.zoomed .lb-nav {
    opacity: 0;
    pointer-events: none;
  }
  #lightbox .lb-nav.prev { left: 16px; }
  #lightbox .lb-nav.next { right: 16px; }
  #lightbox .lb-nav svg { width: 16px; height: 16px; }

  @media (max-width: 640px) {
    #lightbox { padding: 56px 12px 30px; }
    #lightbox .lb-nav { width: 36px; height: 36px; }
    #lightbox .lb-nav.prev { left: 8px; }
    #lightbox .lb-nav.next { right: 8px; }
  }

  /* Desktop: both panes visible by default, stacked */
  @media (min-width: 641px) {
    section.view[data-view="text"] {
      flex: 1 1 58%;
      min-height: 180px;
    }
    section.view[data-view="files"] {
      flex: 1 1 42%;
      min-height: 240px;
      border-top: 1px solid var(--border-subtle);
    }
    /* On desktop, tabs act as checkboxes — both can be active */
    .tabs { gap: 3px; }
  }

  /* Mobile */
  @media (max-width: 640px) {
    header#bar {
      padding: 12px;
      gap: 8px;
      width: 100%;
      max-width: 100vw;
      overflow: hidden;
    }
    nav.tabs { flex: 0 0 auto; }
    .tab { padding: 9px 11px; font-size: 13px; }
    .tab.active { padding: 9px 14px 9px 11px; }
    .tab svg { width: 18px; height: 18px; flex: 0 0 18px; }
    .tab.active .label { max-width: 100px; }
    #status {
      margin-right: 0;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 9px;
      letter-spacing: 0.12em;
    }
    #counter {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    #pad { padding: 62px 18px 18px; font-size: 16px; }
    #noteSearch { top: 14px; left: 14px; }
    #noteSwitch { top: 14px; }
    #noteQuery, #ffilter { font-size: 16px; }

    #upload-group { margin-left: 8px; gap: 8px; min-width: 0; overflow: hidden; }
    .picker-label { padding: 8px 14px; font-size: 13px; }
    .drop-hint { display: none; }

    .row {
      grid-template-columns: auto 1fr auto auto auto;
      grid-template-rows: auto auto;
      grid-template-areas:
        "thumb name copy dl del"
        "thumb meta copy dl del";
      gap: 2px 8px;
      padding: 10px 12px;
    }
    .grip { display: none; }
    .thumb { grid-area: thumb; align-self: center; width: 42px; height: 42px; flex: 0 0 42px; }
    .name { grid-area: name; font-size: 14px; }
    .meta { grid-area: meta; font-size: 10px; }
    .copy { grid-area: copy; align-self: center; }
    .dl { grid-area: dl; align-self: center; }
    .del { grid-area: del; align-self: center; }
    .iconbtn { width: 40px; height: 40px; }
    .iconbtn svg { width: 16px; height: 16px; }
    #chatForm {
      grid-template-columns: 1fr auto;
      grid-template-areas:
        "author send"
        "text text";
    }
    #chatAuthor { grid-area: author; font-size: 16px; }
    #chatText { grid-area: text; font-size: 16px; }
    #chatSend { grid-area: send; min-height: 40px; }
    #chatSuggest {
      left: 14px;
      right: 14px;
      bottom: calc(100% + 6px);
    }

    #overlay svg { width: 80px; height: 80px; }
    #overlay .text { font-size: 13px; }
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
</style></head><body>

<header id="bar">
  <nav class="tabs" role="tablist" aria-label="View">
    <button type="button" class="tab" data-view="text" role="tab" title="Text" aria-label="Text view">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="4.5" x2="13" y2="4.5"/><line x1="3" y1="8" x2="13" y2="8"/><line x1="3" y1="11.5" x2="9" y2="11.5"/></svg>
      <span class="label">text</span>
    </button>
    <button type="button" class="tab" data-view="files" role="tab" title="Files" aria-label="Files view">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"><path d="M2 4.5a1 1 0 0 1 1-1h3.2l1.5 1.5h5.3a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/></svg>
      <span class="label">files</span>
    </button>
    <button type="button" class="tab" data-view="chat" role="tab" title="Chat" aria-label="Chat view">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"><path d="M3 4.5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v4.2a2 2 0 0 1-2 2H7l-3.5 2.8v-2.8H5a2 2 0 0 1-2-2z"/></svg>
      <span class="label">chat</span>
    </button>
  </nav>
  <div id="upload-group">
    <label class="picker-label" for="picker">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v8m-3-3 3 3 3-3M3 13h10"/></svg>
      Upload file
    </label>
    <span class="drop-hint">or drop</span>
    <input id="picker" type="file" multiple>
    <span id="fstatus"></span>
    <div id="upprogress" class="hidden" aria-hidden="true"><div class="fill"></div></div>
  </div>
  <span id="counter"></span>
  <span id="status">connecting</span>
</header>

<section class="view" data-view="text">
  <div id="noteSearch">
    <button type="button" id="noteSearchBtn" title="search notes" aria-label="search notes">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="4"/><path d="m10.5 10.5 3 3"/></svg>
    </button>
    <div id="noteSearchPanel">
      <input id="noteQuery" type="search" placeholder="search notes…" autocomplete="off" spellcheck="false">
      <div id="noteResults"></div>
    </div>
  </div>
  <div id="noteSwitch" aria-label="Notes">
    <button type="button" class="note-dot" data-note="today" title="Today" aria-label="Today" style="--dot:#f5c842"></button>
    <button type="button" class="note-dot" data-note="week" title="Week (M-Su)" aria-label="Week (M-Su)" style="--dot:#68c3a3"></button>
    <button type="button" class="note-dot" data-note="archive" title="Archive" aria-label="Archive" style="--dot:#c982d4"></button>
  </div>
  <button type="button" id="copyAll" title="copy note" aria-label="copy note">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"><rect x="5.5" y="3" width="7.5" height="9.5" rx="1.2"/><path d="M3 5.5v8a1 1 0 0 0 1 1h6.5"/></svg>
  </button>
  <div id="readShield" aria-hidden="true"></div>
  <textarea id="pad" spellcheck="false" placeholder="type, paste, share…" aria-label="Today note"></textarea>
</section>

<section class="view" data-view="files">
  <div id="filter-row" style="display:none">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="4"/><path d="m10.5 10.5 3 3"/></svg>
    <input id="ffilter" type="search" placeholder="filter files…" autocomplete="off" spellcheck="false">
    <span class="count"></span>
    <button type="button" id="sortBtn" title="cycle sort">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4v8m0 0-2-2m2 2 2-2M11 12V4m0 0-2 2m2-2 2 2"/></svg>
      <span class="sort-label">date</span>
    </button>
  </div>
  <div id="flist"></div>
</section>

<section class="view" data-view="chat">
  <div id="chatPinned"></div>
  <div id="taskBoard" aria-label="Task inbox"></div>
  <div id="chatList"></div>
  <form id="chatForm">
    <div id="replyBar">
      <span class="reply-text"></span>
      <button type="button" id="replyClear" title="cancel reply" aria-label="cancel reply">×</button>
    </div>
    <input id="chatAuthor" type="text" placeholder="from" autocomplete="nickname" spellcheck="false">
    <input id="chatText" type="text" placeholder="@machine tell an agent what to do…" autocomplete="off" spellcheck="true">
    <button type="submit" id="chatSend">Send</button>
    <div id="chatSuggest" role="listbox" aria-label="Mention targets"></div>
  </form>
</section>

<div id="overlay" aria-hidden="true">
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 33c-3.6 0-6.5-2.9-6.5-6.5 0-3.4 2.6-6.2 5.9-6.5C14.5 14.5 19 10.5 24.5 10.5c5.8 0 10.5 4.4 11 10.1 3.6.4 6.5 3.5 6.5 7.2 0 4-3.3 7.2-7.3 7.2H14z"/><path d="M24 24v14m-5-9 5-5 5 5"/></svg>
  <span class="text">drop to upload</span>
</div>

<div id="toast"><span class="dot"></span><span class="msg"></span></div>

<div id="lightbox" aria-hidden="true">
  <button type="button" class="lb-close" aria-label="close"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
  <button type="button" class="lb-nav prev" aria-label="previous"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3 5 8l5 5"/></svg></button>
  <button type="button" class="lb-nav next" aria-label="next"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 3 5 5-5 5"/></svg></button>
  <div class="lb-content"></div>
  <span class="lb-name"></span>
</div>

<script>
const pad = document.getElementById('pad');
const status = document.getElementById('status');
const fstatus = document.getElementById('fstatus');
const flist = document.getElementById('flist');
const picker = document.getElementById('picker');
const overlay = document.getElementById('overlay');
const tabs = document.querySelectorAll('.tab');
const views = document.querySelectorAll('section.view');
const textView = document.querySelector('section.view[data-view="text"]');
const noteSearch = document.getElementById('noteSearch');
const noteSearchBtn = document.getElementById('noteSearchBtn');
const noteQuery = document.getElementById('noteQuery');
const noteResults = document.getElementById('noteResults');
const noteDots = document.querySelectorAll('.note-dot');
const readShield = document.getElementById('readShield');
const chatList = document.getElementById('chatList');
const chatPinned = document.getElementById('chatPinned');
const taskBoard = document.getElementById('taskBoard');
const chatForm = document.getElementById('chatForm');
const chatAuthor = document.getElementById('chatAuthor');
const chatText = document.getElementById('chatText');
const chatSuggest = document.getElementById('chatSuggest');
const replyBar = document.getElementById('replyBar');
const replyClear = document.getElementById('replyClear');

const NOTE_ORDER = ['today', 'week', 'archive'];
const NOTE_LABEL = {today:'Today', week:'Week (M-Su)', archive:'Archive'};
const NOTE_PLACEHOLDER = {
  today: 'type, paste, share…',
  week: 'week is empty',
  archive: 'archive is empty',
};
let activeNote = 'today';
let noteTexts = {today:'', week:'', archive:''};
let noteRevs = {today:0, week:0, archive:0};
let noteDate = null;
let serverRev = 0, localDirty = false, saveTimer = null;
let chatMessages = [];
let lastChatSig = null;
let stewardState = {tasks: [], counts: {}, hooks: [], files: []};
let lastStewardSig = null;
let presenceRecords = [];
let lastPresenceSig = null;
let suggestIndex = 0;
let replyToId = null;
let pendingChatJump = null;
let eventsConnected = false;

const isMobile = () => window.matchMedia('(max-width: 640px)').matches;
const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches
  || window.navigator.standalone === true;

function authHeaders(extra = {}){
  const headers = {...extra};
  const token = localStorage.getItem('penpadToken') || '';
  if (token) headers['X-Penpad-Token'] = token;
  return headers;
}

async function authedFetch(url, opts = {}){
  const next = {...opts, headers: authHeaders(opts.headers || {})};
  let r = await fetch(url, next);
  if (r.status !== 401) return r;
  const token = prompt('Penpad token');
  if (!token) return r;
  localStorage.setItem('penpadToken', token.trim());
  next.headers = authHeaders(opts.headers || {});
  return fetch(url, next);
}

async function lockOrientation(mode){
  if (!isMobile() || !isStandalone() || !screen.orientation?.lock) return;
  try { await screen.orientation.lock(mode); } catch(e){}
}

function unlockOrientation(){
  if (!isMobile() || !isStandalone() || !screen.orientation?.unlock) return;
  try { screen.orientation.unlock(); } catch(e){}
}

function applyDefaultOrientation(){
  lockOrientation('portrait');
}

document.addEventListener('gesturestart', e => {
  if (!isMobile() || !isStandalone()) return;
  if (document.getElementById('lightbox').classList.contains('show') && previewZoom.target) {
    e.preventDefault();
    previewZoom.gestureScale = previewZoom.scale;
    return;
  }
  e.preventDefault();
}, {passive:false});
document.addEventListener('gesturechange', e => {
  if (!isMobile() || !isStandalone()) return;
  if (document.getElementById('lightbox').classList.contains('show') && previewZoom.target) {
    e.preventDefault();
    setPreviewZoom(previewZoom.gestureScale * e.scale, previewZoom.x, previewZoom.y);
    return;
  }
  e.preventDefault();
}, {passive:false});
document.addEventListener('gestureend', e => {
  if (!isMobile() || !isStandalone()) return;
  if (previewZoom.target) {
    e.preventDefault();
    settlePreviewZoom();
  }
}, {passive:false});

function hideKeyboard(){
  const el = document.activeElement;
  if (el && typeof el.blur === 'function') el.blur();
}

function activateReadMode(){
  if (!isMobile() || activeNote !== 'today') return;
  hideKeyboard();
  textView.classList.add('read-mode');
}

function disableReadMode(focus){
  textView.classList.remove('read-mode');
  if (focus && activeNote === 'today') {
    pad.focus({preventScroll:true});
  }
}

function getActive(){
  const s = new Set();
  views.forEach(v => { if (v.classList.contains('active')) s.add(v.dataset.view); });
  return s;
}

function applyActive(set){
  views.forEach(v => v.classList.toggle('active', set.has(v.dataset.view)));
  tabs.forEach(t => {
    const on = set.has(t.dataset.view);
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on);
  });
  document.body.classList.toggle('has-text', set.has('text'));
  document.body.classList.toggle('has-files', set.has('files'));
  document.body.classList.toggle('has-chat', set.has('chat'));
  if (isMobile() && (set.has('files') || set.has('chat'))) hideKeyboard();
  localStorage.setItem('views', JSON.stringify([...set]));
}

function ensureView(v){
  const s = getActive();
  if (s.has(v)) return;
  if (isMobile()) applyActive(new Set([v]));
  else { s.add(v); applyActive(s); }
}

function toggleView(v){
  const s = getActive();
  if (isMobile()){
    applyActive(new Set([v]));
    if (v === 'text') activateReadMode();
    else disableReadMode(false);
  } else {
    if (s.has(v)){
      if (s.size > 1){ s.delete(v); applyActive(s); }
    } else { s.add(v); applyActive(s); }
  }
}

function initViews(){
  let s;
  try { s = new Set(JSON.parse(localStorage.getItem('views') || '[]')); }
  catch(e){ s = new Set(); }
  if (!s.size) s = isMobile() ? new Set(['text']) : new Set(['text', 'files']);
  s = new Set([...s].filter(v => ['text', 'files', 'chat'].includes(v)));
  if (isMobile() && s.size > 1) s = new Set([s.has('text') ? 'text' : (s.has('chat') ? 'chat' : 'files')]);
  if (!s.size) s = isMobile() ? new Set(['text']) : new Set(['text', 'files']);
  applyActive(s);
}

tabs.forEach(t => t.addEventListener('click', () => toggleView(t.dataset.view)));

let wasMobile = isMobile();
window.addEventListener('resize', () => {
  const m = isMobile();
  if (m === wasMobile) return;
  wasMobile = m;
  let s = getActive();
  if (m && s.size > 1) applyActive(new Set([s.has('text') ? 'text' : (s.has('chat') ? 'chat' : 'files')]));
  else if (!m && s.size === 0) applyActive(new Set(['text', 'files']));
  else if (!m && s.size === 1) applyActive(new Set(['text', 'files']));
  if (m && getActive().has('text')) activateReadMode();
  else if (!m) disableReadMode(false);
});

initViews();

function setStatus(text, cls){ status.textContent = text; status.className = cls || ''; }
function setF(text, cls){ fstatus.textContent = text; fstatus.className = cls || ''; }

function noteIsEditable(note = activeNote){
  return note === 'today';
}

function activeText(){
  return noteTexts[activeNote] || '';
}

function applyNoteChrome(){
  document.body.classList.remove('note-today', 'note-week', 'note-archive');
  document.body.classList.add('note-' + activeNote);
  noteDots.forEach(dot => {
    const on = dot.dataset.note === activeNote;
    dot.classList.toggle('active', on);
    dot.setAttribute('aria-current', on ? 'true' : 'false');
  });
  pad.readOnly = !noteIsEditable();
  pad.placeholder = NOTE_PLACEHOLDER[activeNote] || '';
  pad.setAttribute('aria-label', NOTE_LABEL[activeNote] + ' note');
  document.getElementById('copyAll').title = 'copy ' + NOTE_LABEL[activeNote];
}

function renderActiveNote(opts = {}){
  const keepPos = opts.keepPos !== false;
  const pos = keepPos ? pad.selectionStart : 0;
  pad.value = activeText();
  const nextPos = Math.min(pos, pad.value.length);
  pad.selectionStart = pad.selectionEnd = nextPos;
  applyNoteChrome();
  updateCounter();
  updateTitle();
  if (!noteIsEditable()) setStatus(activeNote === 'week' ? 'week' : 'archive', 'ok');
  else if (!localDirty) setStatus('synced', 'ok');
}

function switchNote(note, opts = {}){
  if (!NOTE_ORDER.includes(note) || note === activeNote) return;
  if (activeNote === 'today') noteTexts.today = pad.value;
  activeNote = note;
  disableReadMode(false);
  renderActiveNote({keepPos:false});
  if (opts.focus === true) pad.focus({preventScroll:true});
  else if (isMobile() && activeNote === 'today') activateReadMode();
  renderSearchResults();
}

function switchNoteBy(delta){
  const i = NOTE_ORDER.indexOf(activeNote);
  const next = NOTE_ORDER[i + delta];
  if (next) switchNote(next);
}

noteDots.forEach(dot => {
  dot.addEventListener('click', () => switchNote(dot.dataset.note));
});

function openNoteSearch(){
  noteSearch.classList.add('open');
  noteQuery.focus();
  noteQuery.select();
  renderSearchResults();
}

function closeNoteSearch(){
  noteSearch.classList.remove('open');
  if (document.activeElement === noteQuery) noteQuery.blur();
}

noteSearchBtn.addEventListener('click', () => {
  if (noteSearch.classList.contains('open')) closeNoteSearch();
  else openNoteSearch();
});

noteQuery.addEventListener('input', renderSearchResults);
noteQuery.addEventListener('keydown', e => {
  if (e.key === 'Escape'){
    e.preventDefault();
    if (noteQuery.value) {
      noteQuery.value = '';
      renderSearchResults();
    } else {
      closeNoteSearch();
      if (!isMobile()) pad.focus({preventScroll:true});
      else if (activeNote === 'today') activateReadMode();
    }
  }
});

function searchMatches(q){
  const needle = q.trim().toLowerCase();
  if (!needle) return [];
  const out = [];
  for (const note of NOTE_ORDER){
    const text = noteTexts[note] || '';
    let offset = 0;
    for (const line of text.split('\n')){
      const at = line.toLowerCase().indexOf(needle);
      if (at >= 0){
        out.push({
          note,
          start: offset + at,
          end: offset + at + q.trim().length,
          line: line.trim() || '(empty line)',
        });
        if (out.length >= 60) return out;
      }
      offset += line.length + 1;
    }
  }
  return out;
}

function markedSnippet(line, q){
  const needle = q.trim();
  const at = line.toLowerCase().indexOf(needle.toLowerCase());
  if (at < 0) return escapeHtml(line);
  return escapeHtml(line.slice(0, at)) + '<mark>' +
    escapeHtml(line.slice(at, at + needle.length)) + '</mark>' +
    escapeHtml(line.slice(at + needle.length));
}

function renderSearchResults(){
  if (!noteSearch.classList.contains('open')) return;
  const q = noteQuery.value;
  noteResults.innerHTML = '';
  if (!q.trim()){
    noteResults.innerHTML = '<div class="note-result-empty">type to search</div>';
    return;
  }
  const matches = searchMatches(q);
  if (!matches.length){
    noteResults.innerHTML = '<div class="note-result-empty">no matches</div>';
    return;
  }
  for (const m of matches){
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'note-result';
    btn.innerHTML =
      '<span class="src">' + escapeHtml(NOTE_LABEL[m.note]) + '</span>' +
      '<span class="snippet">' + markedSnippet(m.line, q) + '</span>';
    btn.addEventListener('click', () => {
      switchNote(m.note, {focus:false});
      closeNoteSearch();
      requestAnimationFrame(() => {
        if (!isMobile()) pad.focus({preventScroll:true});
        pad.setSelectionRange(m.start, m.end);
        const lineHeight = parseFloat(getComputedStyle(pad).lineHeight) || 22;
        const before = pad.value.slice(0, m.start);
        const line = (before.match(/\n/g) || []).length;
        pad.scrollTop = Math.max(0, line * lineHeight - pad.clientHeight / 3);
        if (isMobile() && activeNote === 'today') activateReadMode();
      });
    });
    noteResults.appendChild(btn);
  }
}

let noteSwipe = null;
let shieldTouch = null;
readShield.addEventListener('touchstart', e => {
  if (e.touches.length !== 1) return;
  const t = e.touches[0];
  shieldTouch = {x: t.clientX, y: t.clientY, scrollTop: pad.scrollTop};
  e.stopPropagation();
}, {passive:false});
readShield.addEventListener('touchmove', e => {
  if (!shieldTouch || e.touches.length !== 1) return;
  const t = e.touches[0];
  const dx = t.clientX - shieldTouch.x;
  const dy = t.clientY - shieldTouch.y;
  if (Math.abs(dy) >= Math.abs(dx)){
    e.preventDefault();
    pad.scrollTop = Math.max(0, shieldTouch.scrollTop - dy);
  }
  e.stopPropagation();
}, {passive:false});
readShield.addEventListener('touchend', e => {
  if (!shieldTouch) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - shieldTouch.x;
  const dy = t.clientY - shieldTouch.y;
  shieldTouch = null;
  e.preventDefault();
  e.stopPropagation();
  if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.4){
    switchNoteBy(dx < 0 ? 1 : -1);
    return;
  }
  if (Math.abs(dx) < 10 && Math.abs(dy) < 10){
    disableReadMode(true);
  }
}, {passive:false});
textView.addEventListener('touchstart', e => {
  if (document.getElementById('lightbox').classList.contains('show')) return;
  if (e.touches.length !== 1) return;
  if (e.target.closest('#noteSearch, #noteSwitch, #copyAll')) return;
  const t = e.touches[0];
  noteSwipe = {x: t.clientX, y: t.clientY};
}, {passive:true});
textView.addEventListener('touchend', e => {
  if (!noteSwipe || document.getElementById('lightbox').classList.contains('show')) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - noteSwipe.x;
  const dy = t.clientY - noteSwipe.y;
  noteSwipe = null;
  if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
  switchNoteBy(dx < 0 ? 1 : -1);
}, {passive:true});

function fmtSize(n){
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}
function fmtTime(ts){
  const d = new Date(ts * 1000), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})
    : d.toLocaleDateString([], {month:'short', day:'numeric'});
}

function escapeHtml(s){
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function chatMentions(text){
  return [...new Set([...text.matchAll(/(^|\s)@([A-Za-z0-9_.-]{1,64})/g)].map(m => m[2]))];
}

function chatFiles(text){
  const known = new Set(lastItems.map(it => it.name));
  const out = [];
  const candidates = [
    ...text.matchAll(/(?:^|\s)file:([A-Za-z0-9._-]{1,255})/g),
    ...text.matchAll(/\/files\/([A-Za-z0-9._%+-]{1,255})/g),
  ].map(m => decodeURIComponent(m[1]));
  for (const name of candidates){
    if (/^[A-Za-z0-9._-]{1,255}$/.test(name) && known.has(name) && !out.includes(name)) {
      out.push(name);
    }
  }
  return out;
}

function chatTime(ts){
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}

function highlightMentions(text){
  return escapeHtml(text).replace(/(^|\s)@([A-Za-z0-9_.-]{1,64})/g,
    (_, lead, name) => lead + '<span class="mention">@' + escapeHtml(name) + '</span>');
}

function knownChatTargets(){
  const map = new Map();
  for (const base of ['all', 'agents']) map.set(base, {id: base, status: 'route'});
  for (const msg of chatMessages){
    if (msg.author) map.set(String(msg.author).split(/\s+/)[0], {id: String(msg.author).split(/\s+/)[0], status: 'seen'});
    for (const m of msg.mentions || []) map.set(String(m), {id: String(m), status: 'seen'});
  }
  for (const rec of presenceRecords){
    if (rec.id) map.set(rec.id, rec);
    for (const t of rec.targets || []) map.set(t, {...rec, id: t});
  }
  return [...map.values()]
    .filter(rec => /^[A-Za-z0-9_.-]{1,64}$/.test(rec.id))
    .sort((a, b) => a.id.localeCompare(b.id));
}

function mentionContext(){
  if (!chatText) return null;
  const pos = chatText.selectionStart || 0;
  const left = chatText.value.slice(0, pos);
  const m = left.match(/(^|\s)@([A-Za-z0-9_.-]{0,64})$/);
  if (!m) return null;
  return {start: pos - m[2].length - 1, end: pos, query: m[2].toLowerCase()};
}

function insertMention(id){
  const ctx = mentionContext();
  if (!ctx) return;
  chatText.value = chatText.value.slice(0, ctx.start) + '@' + id + ' ' + chatText.value.slice(ctx.end);
  chatText.focus();
  const pos = ctx.start + id.length + 2;
  chatText.setSelectionRange(pos, pos);
  renderChatSuggest();
}

function renderChatSuggest(){
  const ctx = mentionContext();
  if (!ctx){
    chatSuggest.classList.remove('open');
    chatSuggest.innerHTML = '';
    return;
  }
  const matches = knownChatTargets()
    .filter(rec => rec.id.toLowerCase().startsWith(ctx.query))
    .slice(0, 8);
  if (!matches.length){
    chatSuggest.classList.remove('open');
    chatSuggest.innerHTML = '';
    return;
  }
  suggestIndex = Math.max(0, Math.min(suggestIndex, matches.length - 1));
  chatSuggest.innerHTML = '';
  for (const [i, rec] of matches.entries()){
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chat-suggest-item' + (i === suggestIndex ? ' active' : '');
    btn.innerHTML = '<span>@' + escapeHtml(rec.id) + '</span><span class="chat-suggest-meta">' +
      escapeHtml(presenceMeta(rec)) + '</span>';
    btn.addEventListener('mousedown', e => {
      e.preventDefault();
      insertMention(rec.id);
    });
    chatSuggest.appendChild(btn);
  }
  chatSuggest.classList.add('open');
}

async function refreshPresence(force){
  try {
    const r = await fetch('/presence', {cache:'no-store'});
    if (!r.ok) return;
    const payload = await r.json();
    const agents = payload.agents || [];
    const sig = agents.map(a => a.id + ':' + a.seen + ':' + a.status + ':' +
      (a.capabilities || []).join(',') + ':' + (a.cwd || '')).join('|');
    if (!force && sig === lastPresenceSig) return;
    lastPresenceSig = sig;
    presenceRecords = agents;
    renderChatSuggest();
  } catch(e){ /* presence is optional */ }
}

async function sendChat(payload){
  const r = await authedFetch('/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

const TASK_STATUS_KINDS = new Set(['claim', 'working', 'done', 'blocked']);
const CHAT_META_KINDS = new Set(['react', 'unreact', 'pin', 'unpin', 'attach', 'summary', 'proposal', 'metadata', ...TASK_STATUS_KINDS]);
const QUICK_REACTIONS = ['+1', 'seen', 'need-info'];

function baseChatMessages(){
  return chatMessages.filter(msg => !CHAT_META_KINDS.has(msg.kind || 'message'));
}

function chatMessageMap(){
  const map = new Map();
  for (const msg of baseChatMessages()) map.set(msg.id, msg);
  return map;
}

function chatSnippet(msg, len = 90){
  const text = (msg?.text || '').replace(/\s+/g, ' ').trim();
  return text.length > len ? text.slice(0, len - 1) + '…' : text;
}

function presenceMeta(rec){
  const bits = [];
  if (rec.stale) bits.push('stale');
  else if (rec.status) bits.push(rec.status);
  if (Array.isArray(rec.capabilities) && rec.capabilities.length) {
    bits.push(rec.capabilities.slice(0, 3).join('/'));
  } else if (rec.role) {
    bits.push(rec.role);
  }
  if (rec.host) bits.push(rec.host);
  return bits.filter(Boolean).join(' · ') || 'online';
}

function findFileItem(name){
  return lastItems.find(it => it.name === name) || null;
}

function chatFileChips(files){
  if (!Array.isArray(files) || !files.length) return '';
  const chips = files.slice(0, 8).map(name => {
    const item = findFileItem(name);
    const type = item ? fileType(item.name) : null;
    const icon = TYPE_ICON[type || 'binary'] || TYPE_ICON.binary;
    const meta = item ? ' · ' + fmtSize(item.size) : '';
    return '<a class="chat-file-chip" data-file="' + escapeHtml(name) + '" href="/files/' +
      encodeURIComponent(name) + '"><span class="file-icon">' + icon + '</span><span>' +
      escapeHtml(name + meta) + '</span></a>';
  }).join('');
  return '<div class="chat-files">' + chips + '</div>';
}

function authorName(){
  return chatAuthor.value.trim() || 'me';
}

function currentAuthor(){
  const author = chatAuthor.value.trim() || 'me';
  localStorage.setItem('chatAuthor', author);
  return author;
}

function reactionSummary(msgId){
  const byReaction = new Map();
  for (const event of chatMessages){
    if (!['react', 'unreact'].includes(event.kind) || event.task_id !== msgId || !event.reaction) continue;
    if (!byReaction.has(event.reaction)) byReaction.set(event.reaction, new Set());
    if (event.kind === 'react') byReaction.get(event.reaction).add(event.author || 'anon');
    if (event.kind === 'unreact') byReaction.get(event.reaction).delete(event.author || 'anon');
  }
  for (const [reaction, authors] of byReaction.entries()){
    if (!authors.size) byReaction.delete(reaction);
  }
  return byReaction;
}

async function toggleReaction(msg, reaction){
  const author = currentAuthor();
  const active = reactionSummary(msg.id).get(reaction)?.has(author);
  const kind = active ? 'unreact' : 'react';
  await postChatMeta(kind, msg, {
    reaction,
    text: author + ' ' + (active ? 'removed ' : 'reacted ') + reaction + ' ' + msg.id.slice(0, 8),
  });
}

function pinnedMessages(){
  const pinned = new Map();
  const messages = chatMessageMap();
  for (const event of chatMessages){
    if (!event.task_id) continue;
    if (event.kind === 'pin') pinned.set(event.task_id, event);
    if (event.kind === 'unpin') pinned.delete(event.task_id);
  }
  return [...pinned.keys()].map(id => messages.get(id)).filter(Boolean);
}

function activeTasks(){
  const tasks = Array.isArray(stewardState.tasks) ? stewardState.tasks : [];
  return tasks.filter(t => ['open', 'claimed', 'working', 'blocked'].includes(t.status));
}

function renderTaskBoard(){
  if (!taskBoard) return;
  const tasks = activeTasks();
  const counts = stewardState.counts || {};
  taskBoard.innerHTML = '';
  taskBoard.classList.toggle('open', tasks.length > 0);
  if (!tasks.length) return;

  const summary = document.createElement('div');
  summary.className = 'task-summary';
  const activeCount = tasks.length;
  summary.innerHTML = '<strong>' + activeCount + '</strong><span>active</span>' +
    '<span>' + escapeHtml(String(counts.blocked || 0)) + ' blocked</span>';
  taskBoard.appendChild(summary);

  const byWeight = {blocked: 0, working: 1, claimed: 2, open: 3};
  const cards = [...tasks].sort((a, b) => (byWeight[a.status] ?? 9) - (byWeight[b.status] ?? 9));
  for (const task of cards.slice(0, 8)){
    const source = chatMessageMap().get(task.id) || task;
    const card = document.createElement('div');
    card.className = 'task-card ' + escapeHtml(task.status || 'open');
    const target = (task.targets && task.targets[0]) || task.target || 'agents';
    const priority = task.priority || 'normal';
    card.innerHTML =
      '<div class="task-row">' +
        '<span class="task-status">' + escapeHtml(task.status || 'open') + '</span>' +
        '<span class="task-target">@' + escapeHtml(target) + '</span>' +
        '<span class="task-priority ' + escapeHtml(priority) + '">' + escapeHtml(priority) + '</span>' +
      '</div>' +
      '<div class="task-title">' + escapeHtml(chatSnippet(task, 130)) + '</div>' +
      '<div class="task-actions"></div>';
    const actions = card.querySelector('.task-actions');
    const jump = document.createElement('button');
    jump.type = 'button';
    jump.textContent = 'jump';
    jump.addEventListener('click', () => jumpToChat(task.id));
    actions.appendChild(jump);
    for (const action of ['working', 'done', 'blocked']){
      if (task.status === action) continue;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = action;
      btn.addEventListener('click', () => postTaskState(source, action));
      actions.appendChild(btn);
    }
    taskBoard.appendChild(card);
  }
}

function jumpToChat(id){
  const row = chatList.querySelector('[data-id="' + CSS.escape(id) + '"]');
  if (!row) return;
  row.scrollIntoView({block: 'center', behavior: 'smooth'});
  row.classList.remove('jump');
  requestAnimationFrame(() => row.classList.add('jump'));
}

function chatPermalink(msg){
  return location.origin + location.pathname + '#chat-' + msg.id;
}

async function copyChatLink(msg){
  const permalink = chatPermalink(msg);
  history.replaceState(null, '', '#chat-' + msg.id);
  const ok = await copyToClipboard(permalink);
  if (ok) {
    setStatus('link copied', 'ok');
  } else {
    setStatus('link in URL', 'ok');
  }
}

function handleChatHash(){
  const m = location.hash.match(/^#chat-([A-Fa-f0-9]{32})$/);
  if (!m) return;
  pendingChatJump = m[1];
  ensureView('chat');
  requestAnimationFrame(() => jumpToChat(pendingChatJump));
}

function lastReadableChatId(messages = baseChatMessages()){
  return messages.length ? messages[messages.length - 1].id : '';
}

function markChatRead(){
  const id = lastReadableChatId();
  if (id) localStorage.setItem('chatLastReadId', id);
  renderChat();
}

function maybeInsertUnreadDivider(msg, messages, state){
  if (!state.stored) {
    const last = lastReadableChatId(messages);
    if (last) localStorage.setItem('chatLastReadId', last);
    return;
  }
  if (state.inserted || !state.seenRead) return;
  state.inserted = true;
  const div = document.createElement('div');
  div.className = 'chat-unread';
  div.innerHTML = '<span>new</span><button type="button">mark read</button>';
  div.querySelector('button').addEventListener('click', markChatRead);
  chatList.appendChild(div);
}

function renderPinned(){
  if (!chatPinned) return;
  const pins = pinnedMessages();
  chatPinned.innerHTML = '';
  chatPinned.classList.toggle('open', pins.length > 0);
  for (const msg of pins){
    const card = document.createElement('div');
    card.className = 'pin-card';
    card.innerHTML =
      '<span class="pin-author">' + escapeHtml(msg.author || 'anon') + '</span>' +
      '<button type="button">unpin</button>' +
      '<span class="pin-text">' + escapeHtml(chatSnippet(msg, 120)) + '</span>';
    card.addEventListener('click', e => {
      if (e.target.tagName === 'BUTTON') return;
      jumpToChat(msg.id);
    });
    card.querySelector('button').addEventListener('click', e => {
      e.stopPropagation();
      postChatMeta('unpin', msg, {text: currentAuthor() + ' unpinned ' + msg.id.slice(0, 8)});
    });
    chatPinned.appendChild(card);
  }
}

function renderReplyBar(){
  if (!replyToId){
    replyBar.classList.remove('open');
    replyBar.querySelector('.reply-text').textContent = '';
    return;
  }
  const msg = chatMessageMap().get(replyToId);
  if (!msg){
    replyToId = null;
    renderReplyBar();
    return;
  }
  replyBar.querySelector('.reply-text').textContent =
    'replying to ' + (msg.author || 'anon') + ': ' + chatSnippet(msg, 130);
  replyBar.classList.add('open');
}

function setReply(msg){
  replyToId = msg.id;
  renderReplyBar();
  chatText.focus();
}

replyClear.addEventListener('click', () => {
  replyToId = null;
  renderReplyBar();
  chatText.focus();
});

async function postChatMeta(kind, msg, extra = {}){
  try {
    const author = authorName();
    const text = extra.text || (author + ' ' + kind + ' ' + String(msg.id || '').slice(0, 8));
    await sendChat({
      author,
      text,
      kind,
      task_id: msg.id || '',
      reaction: extra.reaction || '',
    });
    await refreshChat(true);
    setStatus(kind, 'ok');
  } catch(e){
    setStatus('chat failed', 'err');
  }
}

function taskReplyText(msg, action){
  const author = currentAuthor();
  const mention = (msg.author && /^[A-Za-z0-9_.-]+$/.test(msg.author)) ? '@' + msg.author + ' ' : '';
  return mention + author + ' ' + action + ' ' + String(msg.id || '').slice(0, 8);
}

async function postTaskState(msg, action){
  try {
    const author = currentAuthor();
    await sendChat({
      author,
      text: taskReplyText(msg, action),
      mentions: msg.author && /^[A-Za-z0-9_.-]+$/.test(msg.author) ? [msg.author] : [],
      kind: action,
      task_id: msg.id || '',
      status: action,
    });
    await refreshChat(true);
    setStatus(action, 'ok');
  } catch(e){
    setStatus('chat failed', 'err');
  }
}

function renderChat(){
  if (!chatList) return;
  renderPinned();
  renderTaskBoard();
  renderReplyBar();
  const messages = baseChatMessages();
  const messageMap = chatMessageMap();
  if (!messages.length){
    chatList.innerHTML = '<div class="empty">' + ICON.empty +
      '<span class="hint">no chat yet · use @machine or @agent to route</span></div>';
    return;
  }
  chatList.innerHTML = '';
  const storedReadId = localStorage.getItem('chatLastReadId') || '';
  const unreadState = {
    stored: storedReadId,
    seenRead: !!storedReadId && !messages.some(m => m.id === storedReadId),
    inserted: false,
  };
  for (const msg of messages){
    maybeInsertUnreadDivider(msg, messages, unreadState);
    const mentions = msg.mentions || [];
    const row = document.createElement('div');
    row.dataset.id = msg.id || '';
    row.id = 'chat-' + (msg.id || '');
    row.className = 'chat-msg' + (mentions.length ? ' targeted' : '');
    const targets = mentions.map(m => '<span class="chat-target">@' + escapeHtml(m) + '</span>').join('');
    const kind = msg.kind && msg.kind !== 'message'
      ? '<span class="chat-kind">' + escapeHtml(msg.kind) + '</span>'
      : '';
    const replied = msg.reply_to ? messageMap.get(msg.reply_to) : null;
    const replyHtml = replied
      ? '<div class="chat-reply" data-reply="' + escapeHtml(replied.id) + '">' +
          '<span class="who">' + escapeHtml(replied.author || 'anon') + '</span>' +
          escapeHtml(chatSnippet(replied, 120)) +
        '</div>'
      : '';
    row.innerHTML =
      '<div class="chat-meta">' +
        '<span class="chat-author">' + escapeHtml(msg.author || 'anon') + '</span>' +
        '<span>' + escapeHtml(chatTime(msg.ts)) + '</span>' +
        kind +
        targets +
      '</div>' +
      replyHtml +
      '<div class="chat-text">' + highlightMentions(msg.text || '') + '</div>' +
      chatFileChips(msg.files || []);
    const replyEl = row.querySelector('.chat-reply');
    if (replyEl) replyEl.addEventListener('click', () => jumpToChat(replied.id));
    row.querySelectorAll('.chat-file-chip').forEach(chip => {
      chip.addEventListener('click', e => {
        const name = chip.dataset.file || '';
        const item = findFileItem(name);
        if (item && isPreviewable(item.name)) {
          e.preventDefault();
          openPreview(item);
        }
      });
    });

    const reactions = reactionSummary(msg.id);
    const reactionRow = document.createElement('div');
    reactionRow.className = 'chat-reactions';
    const author = currentAuthor();
    for (const [reaction, authors] of reactions.entries()){
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chat-reaction' + (authors.has(author) ? ' active' : '');
      btn.textContent = reaction + ' ' + authors.size;
      btn.title = [...authors].join(', ');
      btn.addEventListener('click', () => toggleReaction(msg, reaction));
      reactionRow.appendChild(btn);
    }
    if (reactionRow.children.length) row.appendChild(reactionRow);

    const actions = document.createElement('div');
    actions.className = 'chat-actions';
    for (const action of ['reply', 'pin', 'link']){
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chat-action';
      btn.textContent = action;
      if (action === 'reply') btn.addEventListener('click', () => setReply(msg));
      if (action === 'pin') btn.addEventListener('click', () => postChatMeta('pin', msg, {
        text: currentAuthor() + ' pinned ' + msg.id.slice(0, 8),
      }));
      if (action === 'link') btn.addEventListener('click', () => copyChatLink(msg));
      actions.appendChild(btn);
    }
    for (const reaction of QUICK_REACTIONS){
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chat-action';
      btn.textContent = reaction;
      btn.addEventListener('click', () => toggleReaction(msg, reaction));
      actions.appendChild(btn);
    }
    if (mentions.length && (!msg.kind || msg.kind === 'message')){
      for (const action of ['claim', 'working', 'done', 'blocked']){
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'chat-action';
        btn.textContent = action;
        btn.addEventListener('click', () => postTaskState(msg, action));
        actions.appendChild(btn);
      }
    }
    row.appendChild(actions);
    chatList.appendChild(row);
    if (msg.id === storedReadId) unreadState.seenRead = true;
  }
  if (pendingChatJump) {
    const id = pendingChatJump;
    pendingChatJump = null;
    requestAnimationFrame(() => jumpToChat(id));
  } else if (!unreadState.inserted) {
    chatList.scrollTop = chatList.scrollHeight;
  }
}

async function refreshChat(force){
  try {
    const r = await fetch('/chat?limit=200', {cache:'no-store'});
    if (!r.ok) throw new Error('chat fetch failed');
    const payload = await r.json();
    const messages = payload.messages || [];
    const sig = messages.map(m => m.id).join('|');
    if (!force && sig === lastChatSig) return;
    lastChatSig = sig;
    chatMessages = messages;
    renderChat();
    refreshPresence(false);
    refreshSteward(true);
  } catch(e){ /* keep last chat */ }
}

async function refreshSteward(force){
  try {
    const r = await fetch('/steward', {cache:'no-store'});
    if (!r.ok) throw new Error('steward fetch failed');
    const payload = await r.json();
    const sig = JSON.stringify({
      tasks: (payload.tasks || []).map(t => [t.id, t.status, t.assignee, t.updated_ts, t.files]),
      hooks: (payload.hooks || []).map(h => h.id),
      files: (payload.files || []).map(f => [f.name, f.summary_status, f.linked_tasks]),
    });
    if (!force && sig === lastStewardSig) return;
    lastStewardSig = sig;
    stewardState = payload;
    renderTaskBoard();
  } catch(e){ /* steward state is derived; chat remains usable */ }
}

chatAuthor.value = localStorage.getItem('chatAuthor') || '';
chatAuthor.addEventListener('input', () => {
  localStorage.setItem('chatAuthor', chatAuthor.value.trim());
});
chatText.addEventListener('input', () => {
  suggestIndex = 0;
  renderChatSuggest();
});
chatText.addEventListener('keydown', e => {
  if (!chatSuggest.classList.contains('open')) return;
  const items = [...chatSuggest.querySelectorAll('.chat-suggest-item')];
  if (!items.length) return;
  if (e.key === 'ArrowDown'){
    e.preventDefault();
    suggestIndex = (suggestIndex + 1) % items.length;
    renderChatSuggest();
  } else if (e.key === 'ArrowUp'){
    e.preventDefault();
    suggestIndex = (suggestIndex - 1 + items.length) % items.length;
    renderChatSuggest();
  } else if (e.key === 'Tab' || e.key === 'Enter'){
    if (mentionContext()){
      e.preventDefault();
      const target = knownChatTargets().filter(rec => rec.id.toLowerCase().startsWith(mentionContext().query))[suggestIndex];
      if (target) insertMention(target.id);
    }
  } else if (e.key === 'Escape'){
    chatSuggest.classList.remove('open');
  }
});
chatForm.addEventListener('submit', async e => {
  e.preventDefault();
  const text = chatText.value.trim();
  if (!text) return;
  const author = currentAuthor();
  const mentions = chatMentions(text);
  const files = chatFiles(text);
  try {
    await sendChat({author, text, mentions, files, reply_to: replyToId || ''});
    chatText.value = '';
    replyToId = null;
    renderReplyBar();
    renderChatSuggest();
    await refreshChat(true);
    setStatus(mentions.length ? 'routed' : 'chat', 'ok');
  } catch(err){
    setStatus('chat failed', 'err');
  }
});
window.addEventListener('hashchange', handleChatHash);

const ICON = {
  grip: '<svg viewBox="0 0 12 16" fill="currentColor"><circle cx="4" cy="3" r="1"/><circle cx="4" cy="8" r="1"/><circle cx="4" cy="13" r="1"/><circle cx="8" cy="3" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="8" cy="13" r="1"/></svg>',
  download: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v8m-3-3 3 3 3-3M3 13h10"/></svg>',
  check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m3.5 8.5 3 3 6-6"/></svg>',
  x: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  copy: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"><rect x="5.5" y="3" width="7.5" height="9.5" rx="1.2"/><path d="M3 5.5v8a1 1 0 0 0 1 1h6.5"/></svg>',
  spinner: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="5" stroke-dasharray="6 6"><animateTransform attributeName="transform" type="rotate" from="0 8 8" to="360 8 8" dur="0.9s" repeatCount="indefinite"/></circle></svg>',
  empty: '<svg viewBox="0 0 36 36" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"><path d="M5 11a2 2 0 0 1 2-2h7l3 3h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/></svg>'
};

// Toast
const toastEl = document.getElementById('toast');
let toastTimer = null;
function toast(msg){
  toastEl.querySelector('.msg').textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 1600);
}

async function copyToClipboard(text){
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch(e){}
  // iOS-friendly fallback: navigator.clipboard requires a secure context,
  // which http-over-tailnet isn't, so Safari/iOS reliably land here.
  // ta.select() alone is unreliable on iOS — use a Range + setSelectionRange.
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.readOnly = true;
  ta.contentEditable = 'true';
  ta.style.position = 'fixed';
  ta.style.top = '0'; ta.style.left = '0';
  ta.style.width = '1px'; ta.style.height = '1px';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  const range = document.createRange();
  range.selectNodeContents(ta);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch(e){}
  sel.removeAllRanges();
  ta.remove();
  return ok;
}

function updateTitle(){
  document.title = (localDirty ? '● ' : '') + NOTE_LABEL[activeNote] + ' · penpad';
}

async function pull(){
  try {
    const r = await fetch('/notes', {cache:'no-store'});
    if (!r.ok) throw new Error('fetch failed');
    const payload = await r.json();
    noteDate = payload.today || noteDate;
    for (const note of payload.notes || []){
      if (!NOTE_ORDER.includes(note.id)) continue;
      noteRevs[note.id] = Number(note.rev || 0);
      if (note.id === 'today' && localDirty) {
        noteTexts.today = activeNote === 'today' ? pad.value : noteTexts.today;
      } else {
        noteTexts[note.id] = note.text || '';
      }
    }
    serverRev = noteRevs.today || 0;
    renderActiveNote({keepPos:true});
    renderSearchResults();
  } catch(e){ setStatus('offline', 'err'); }
}

async function push(){
  noteTexts.today = activeNote === 'today' ? pad.value : noteTexts.today;
  try {
    const r = await authedFetch('/save?note=today', {method:'POST', body: noteTexts.today});
    if (!r.ok) throw new Error(await r.text());
    const rev = Number(r.headers.get('X-Rev') || 0);
    serverRev = noteRevs.today = rev;
    localDirty = false;
    if (activeNote === 'today') setStatus('saved', 'ok');
    updateTitle();
    renderSearchResults();
  } catch(e){ setStatus('save failed', 'err'); }
}

function downloadKey(it){ return 'dl:' + it.name + ':' + it.mtime; }
function isDownloaded(it){ return sessionStorage.getItem(downloadKey(it)) === '1'; }
function markDownloaded(it){ sessionStorage.setItem(downloadKey(it), '1'); }

const TYPE_RX = {
  image: /\.(jpe?g|png|gif|webp|avif|svg|bmp|ico|heic|heif)$/i,
  video: /\.(mp4|webm|ogv|mov|m4v)$/i,
  audio: /\.(mp3|wav|ogg|m4a|aac|flac|opus|weba)$/i,
  pdf:   /\.pdf$/i,
  text:  /\.(txt|md|markdown|log|json|jsonl|ndjson|xml|csv|tsv|yaml|yml|toml|ini|conf|cfg|env|py|js|jsx|mjs|cjs|ts|tsx|css|scss|sass|less|html?|xhtml|sh|bash|zsh|fish|sql|c|cc|h|hpp|cpp|cs|java|rb|go|rs|swift|kt|php|pl|lua|r|jl|tex|rst|adoc|vue|svelte|gitignore|dockerfile|makefile)$/i,
};
const BARE_TEXT = /^(dockerfile|makefile|readme|license|changelog|notice|authors|todo)$/i;
function fileType(name){
  for (const t of ['image','video','audio','pdf','text']) {
    if (TYPE_RX[t].test(name)) return t;
  }
  if (BARE_TEXT.test(name)) return 'text';
  return null;
}
function isImage(name){ return fileType(name) === 'image'; }
function isPreviewable(name){ return fileType(name) !== null; }

const TYPE_ICON = {
  video: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><rect x="2" y="4" width="12" height="8" rx="1.5"/><path d="m7 7 3 1.5L7 10z" fill="currentColor"/></svg>',
  audio: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"><path d="M3 6.5h2L8 4v8L5 9.5H3z"/><path d="M11 5.7a3 3 0 0 1 0 4.6"/></svg>',
  pdf:   '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"><path d="M4 2h6l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M10 2v3h3"/><path d="M5.5 11h5"/></svg>',
  text:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"><path d="M4 2h6l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M10 2v3h3"/><path d="M5.5 9h5M5.5 11.5h3.5"/></svg>',
  binary:'<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"><path d="M4 2h6l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M10 2v3h3"/></svg>',
};

let lastItems = [];
let lastFilesSig = null;
let lastFilterQ = '';

const filterRow = document.getElementById('filter-row');
const ffilter = document.getElementById('ffilter');
const filterCount = filterRow.querySelector('.count');

ffilter.value = sessionStorage.getItem('ffilter') || '';
ffilter.addEventListener('input', () => {
  sessionStorage.setItem('ffilter', ffilter.value);
  renderFiles();
});
ffilter.addEventListener('keydown', e => {
  if (e.key === 'Escape' && ffilter.value){
    e.preventDefault();
    ffilter.value = '';
    sessionStorage.removeItem('ffilter');
    renderFiles();
  }
});

const SORT_ORDER = ['date', 'name', 'size'];
let sortKey = SORT_ORDER.includes(localStorage.getItem('sortKey')) ? localStorage.getItem('sortKey') : 'date';
const sortBtn = document.getElementById('sortBtn');
sortBtn.querySelector('.sort-label').textContent = sortKey;
sortBtn.onclick = () => {
  const i = SORT_ORDER.indexOf(sortKey);
  sortKey = SORT_ORDER[(i + 1) % SORT_ORDER.length];
  localStorage.setItem('sortKey', sortKey);
  sortBtn.querySelector('.sort-label').textContent = sortKey;
  renderFiles();
};

function sortItems(items){
  const a = [...items];
  if (sortKey === 'name') return a.sort((x, y) => x.name.localeCompare(y.name));
  if (sortKey === 'size') return a.sort((x, y) => y.size - x.size);
  return a.sort((x, y) => y.mtime - x.mtime);
}

function renderFiles(){
  const items = sortItems(lastItems);
  const q = ffilter.value.trim().toLowerCase();
  const filtered = q ? items.filter(it => it.name.toLowerCase().includes(q)) : items;

  filterRow.style.display = items.length ? 'flex' : 'none';
  filterCount.textContent = q ? `${filtered.length} of ${items.length}` : `${items.length}`;

  if (!items.length){
    flist.innerHTML = '<div class="empty">' + ICON.empty +
      '<span class="hint">no files yet · drop something here</span></div>';
    return;
  }
  if (!filtered.length){
    flist.innerHTML = '<div class="empty">' + ICON.empty +
      '<span class="hint">no match for "' + escapeHtml(q) + '"</span></div>';
    return;
  }

  const animate = !window.__filesRendered;
  window.__filesRendered = true;
  flist.innerHTML = '';
  filtered.forEach((it, i) => {
      const row = document.createElement('div');
      row.className = 'row';
      // Outbound drag intentionally disabled: WebKitGTK's drag source leaks
      // pointer grabs when the drop target is an external app, which can lock
      // the whole X session. Use the ⬇ button to save instead.
      row.draggable = false;
      const previewable = isPreviewable(it.name);
      row.title = previewable
        ? 'click to preview · ⬇ to save'
        : 'click ⬇ to download';
      if (animate) row.style.animationDelay = (i * 25) + 'ms';
      else row.style.animation = 'none';
      if (flashNames.has(it.name)){
        flashNames.delete(it.name);
        row.classList.add('flash');
        setTimeout(() => row.classList.remove('flash'), 1700);
      }
      const url = '/files/' + encodeURIComponent(it.name);
      const absUrl = location.origin + url;
      const done = isDownloaded(it);
      const type = fileType(it.name);
      let thumb;
      if (type === 'image') {
        thumb = '<a class="thumb img" data-preview="1"><img loading="lazy" decoding="async" src="' + url + '" alt=""></a>';
      } else if (previewable) {
        thumb = '<a class="thumb img" data-preview="1">' + (TYPE_ICON[type] || TYPE_ICON.binary) + '</a>';
      } else {
        thumb = '<span class="thumb">' + TYPE_ICON.binary + '</span>';
      }
      row.innerHTML =
        '<span class="grip">' + ICON.grip + '</span>' +
        thumb +
        '<a class="name">' + escapeHtml(it.name) + '</a>' +
        '<span class="meta">' + fmtSize(it.size) + ' · ' + fmtTime(it.mtime) + '</span>' +
        '<button type="button" class="iconbtn copy" title="copy URL" aria-label="copy URL">' + ICON.copy + '</button>' +
        '<button type="button" class="iconbtn dl' + (done ? ' done' : '') + '" title="download" aria-label="download">' +
          (done ? ICON.check : ICON.download) + '</button>' +
        '<button type="button" class="iconbtn del" title="delete" aria-label="delete">' + ICON.x + '</button>';
      const dlBtn = row.querySelector('.dl');
      dlBtn.onclick = () => downloadFile(it, dlBtn);
      const onName = previewable
        ? (e) => { e.preventDefault(); openPreview(it); }
        : () => downloadFile(it, dlBtn);
      row.querySelector('.name').onclick = onName;
      row.querySelector('.del').onclick = () => deleteFile(it.name);
      row.querySelector('.copy').onclick = async () => {
        const ok = await copyToClipboard(absUrl);
        toast(ok ? 'url copied' : 'copy failed');
      };
      const thumbEl = row.querySelector('.thumb.img');
      if (thumbEl) thumbEl.onclick = (e) => { e.preventDefault(); openPreview(it); };
      flist.appendChild(row);
  });
}

async function refreshFiles(force){
  try {
    const r = await fetch('/files/', {cache:'no-store'});
    const items = await r.json();
    const sig = items.map(it => it.name + ':' + it.mtime + ':' + it.size).join('|');
    if (!force && sig === lastFilesSig) return;
    lastFilesSig = sig;
    lastItems = items;
    renderFiles();
    renderChat();
    refreshSteward(true);
  } catch(e){ /* keep last view */ }
}

let previewIndex = -1;
const previewZoom = {
  target: null,
  scale: 1,
  x: 0,
  y: 0,
  gestureScale: 1,
  touch: null,
};

function clamp(n, lo, hi){
  return Math.max(lo, Math.min(hi, n));
}

function applyPreviewZoom(){
  const lb = document.getElementById('lightbox');
  if (!previewZoom.target) {
    lb.classList.remove('zoomed');
    return;
  }
  const scale = previewZoom.scale;
  previewZoom.target.style.transform =
    `translate(${previewZoom.x}px, ${previewZoom.y}px) scale(${scale})`;
  lb.classList.toggle('zoomed', scale > 1.01);
}

function setPreviewZoom(scale, x = previewZoom.x, y = previewZoom.y){
  previewZoom.scale = clamp(scale, 1, 5);
  if (previewZoom.scale <= 1.01) {
    previewZoom.scale = 1;
    previewZoom.x = 0;
    previewZoom.y = 0;
  } else {
    const maxX = (window.innerWidth * (previewZoom.scale - 1)) / 2;
    const maxY = (window.innerHeight * (previewZoom.scale - 1)) / 2;
    previewZoom.x = clamp(x, -maxX, maxX);
    previewZoom.y = clamp(y, -maxY, maxY);
  }
  applyPreviewZoom();
}

function settlePreviewZoom(){
  if (previewZoom.scale <= 1.05) setPreviewZoom(1, 0, 0);
  else setPreviewZoom(previewZoom.scale, previewZoom.x, previewZoom.y);
}

function resetPreviewZoom(target = null){
  previewZoom.target = target;
  previewZoom.scale = 1;
  previewZoom.x = 0;
  previewZoom.y = 0;
  previewZoom.gestureScale = 1;
  previewZoom.touch = null;
  if (target) target.style.transform = '';
  document.getElementById('lightbox').classList.remove('zoomed');
}

function dist(a, b){
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

function currentPreviewItems(){
  const q = ffilter.value.trim().toLowerCase();
  return sortItems(lastItems)
    .filter(it => !q || it.name.toLowerCase().includes(q))
    .filter(it => isPreviewable(it.name));
}

async function openPreview(it){
  const lb = document.getElementById('lightbox');
  const content = lb.querySelector('.lb-content');
  const url = '/files/' + encodeURIComponent(it.name);
  const type = fileType(it.name);

  const previewItems = currentPreviewItems();
  previewIndex = previewItems.findIndex(x => x.name === it.name);
  updateNavButtons();

  lb.querySelector('.lb-name').textContent = it.name;
  content.innerHTML = '<div class="lb-msg">loading…</div>';
  resetPreviewZoom();
  lb.classList.add('show');
  lb.setAttribute('aria-hidden', 'false');
  applyDefaultOrientation();

  if (type === 'image') {
    content.innerHTML = '';
    const img = new Image();
    img.alt = '';
    img.className = 'zoomable';
    img.draggable = false;
    img.onload = () => {
      resetPreviewZoom(img);
      if (img.naturalWidth > img.naturalHeight) unlockOrientation();
      else applyDefaultOrientation();
    };
    img.src = url;
    content.appendChild(img);
  } else if (type === 'video') {
    content.innerHTML =
      '<video controls autoplay playsinline preload="metadata" src="' + url + '"></video>';
  } else if (type === 'audio') {
    content.innerHTML =
      '<div class="audio-card">' + TYPE_ICON.audio.replace('viewBox="0 0 16 16"', 'viewBox="0 0 16 16" style="transform:scale(1)"') +
      '<audio controls autoplay preload="metadata" src="' + url + '"></audio>' +
      '</div>';
  } else if (type === 'pdf') {
    content.innerHTML =
      '<iframe src="' + url + '#view=FitH" title="' + escapeHtml(it.name) + '"></iframe>';
  } else if (type === 'text') {
    if (it.size > 2 * 1024 * 1024) {
      content.innerHTML = '<div class="lb-msg">file too large to preview · download instead</div>';
    } else {
      try {
        const r = await fetch(url);
        if (!r.ok) throw new Error('fetch failed');
        const text = await r.text();
        const pre = document.createElement('pre');
        pre.className = 'text-pre';
        pre.textContent = text;
        content.innerHTML = '';
        content.appendChild(pre);
      } catch(e) {
        content.innerHTML = '<div class="lb-msg">failed to load</div>';
      }
    }
  } else {
    content.innerHTML = '<div class="lb-msg">no preview · download to view</div>';
  }
}

function closePreview(){
  const lb = document.getElementById('lightbox');
  lb.classList.remove('show');
  lb.setAttribute('aria-hidden', 'true');
  const content = lb.querySelector('.lb-content');
  // pause any media before clearing to release resources
  content.querySelectorAll('video,audio').forEach(m => { try { m.pause(); } catch(e){} });
  content.innerHTML = '';
  previewIndex = -1;
  resetPreviewZoom();
  applyDefaultOrientation();
}

function navPreview(delta){
  if (previewIndex < 0) return;
  if (previewZoom.scale > 1.01) return;
  const items = currentPreviewItems();
  const i = previewIndex + delta;
  if (i < 0 || i >= items.length) return;
  openPreview(items[i]);
}

function updateNavButtons(){
  const lb = document.getElementById('lightbox');
  const prev = lb.querySelector('.lb-nav.prev');
  const next = lb.querySelector('.lb-nav.next');
  const items = currentPreviewItems();
  prev.disabled = previewIndex <= 0;
  next.disabled = previewIndex < 0 || previewIndex >= items.length - 1;
}

async function downloadFile(it, btn){
  if (btn.disabled) return;
  btn.disabled = true;
  const prev = btn.innerHTML;
  btn.innerHTML = ICON.spinner;
  try {
    // Server returns Content-Disposition: attachment when ?download=1.
    // Using a plain anchor (no blob, no a.download) works on iOS Safari too
    // — iOS honors the server's Content-Disposition and shows the native
    // download flow. On desktop browsers this triggers the normal Save As.
    const a = document.createElement('a');
    a.href = '/files/' + encodeURIComponent(it.name) + '?download=1';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    markDownloaded(it);
    btn.innerHTML = ICON.check;
    btn.classList.add('done');
    setF('downloaded ' + it.name, 'ok');
  } catch(e){
    btn.innerHTML = prev;
    setF('download failed', 'err');
  } finally {
    btn.disabled = false;
  }
}

const flashNames = new Set();
const upprog = document.getElementById('upprogress');
const upfill = upprog.querySelector('.fill');
const uploadQueue = [];
let uploadRunning = false;
let uploadDone = 0;
let uploadFail = 0;
let uploadTotal = 0;
let uploadProgressTimer = null;

function setProgress(pct){
  clearTimeout(uploadProgressTimer);
  if (pct == null) {
    upprog.classList.add('hidden');
    upfill.style.width = '0%';
    return;
  }
  upprog.classList.remove('hidden');
  upfill.style.width = pct + '%';
}

function uploadOne(f, onTotalProgress, retried = false){
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/files/' + encodeURIComponent(f.name));
    const token = localStorage.getItem('penpadToken') || '';
    if (token) xhr.setRequestHeader('X-Penpad-Token', token);
    xhr.upload.onprogress = e => {
      if (!e.lengthComputable) return;
      const pct = Math.round(100 * e.loaded / e.total);
      if (onTotalProgress) onTotalProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
        return;
      }
      if (xhr.status === 401 && !retried) {
        const token = prompt('Penpad token');
        if (token) {
          localStorage.setItem('penpadToken', token.trim());
          uploadOne(f, onTotalProgress, true).then(resolve, reject);
          return;
        }
      }
      reject(new Error('http ' + xhr.status));
    };
    xhr.onerror = () => reject(new Error('network'));
    xhr.send(f);
  });
}

async function uploadUriOne(uri){
  const r = await authedFetch('/upload-uri', {
    method: 'POST',
    headers: {'Content-Type': 'text/uri-list'},
    body: uri,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function enqueueUploadItems(items){
  if (!items.length) return;
  uploadQueue.push(...items);
  if (uploadRunning) {
    uploadTotal += items.length;
    setF(`queued ${items.length} · ${uploadQueue.length} waiting`, 'warn');
    return;
  }
  uploadDone = 0;
  uploadFail = 0;
  uploadTotal = uploadQueue.length;
  processUploadQueue();
}

function uploadFiles(files){
  const incoming = Array.from(files || []).filter(Boolean);
  enqueueUploadItems(incoming.map(file => ({kind: 'file', file, name: file.name})));
}

function uploadFileUris(uris){
  const incoming = Array.from(uris || []).filter(Boolean);
  enqueueUploadItems(incoming.map(uri => ({
    kind: 'uri',
    uri,
    name: decodeURIComponent(uri.split('/').pop() || 'file'),
  })));
}

async function processUploadQueue(){
  if (uploadRunning) return;
  uploadRunning = true;
  setProgress(0);

  while (uploadQueue.length){
    const item = uploadQueue.shift();
    const n = uploadDone + uploadFail + 1;
    setF(`uploading ${n}/${uploadTotal} · ${item.name}`, 'warn');
    try {
      if (item.kind === 'file') {
        await uploadOne(item.file, (loaded, total) => {
          const pct = total
            ? Math.min(100, Math.round(100 * loaded / total))
            : null;
          if (pct != null) setProgress(pct);
        });
        flashNames.add(item.file.name);
      } else {
        setProgress(null);
        const j = await uploadUriOne(item.uri);
        const copied = j.copied || [];
        if (!copied.length) throw new Error('skipped');
        copied.forEach(name => flashNames.add(name));
      }
      uploadDone++;
    } catch(e){
      uploadFail++;
    }
    refreshFiles(true);
  }

  uploadRunning = false;
  setProgress(100);
  uploadProgressTimer = setTimeout(() => setProgress(null), 500);
  if (uploadFail) setF(`uploaded ${uploadDone} · ${uploadFail} failed`, uploadFail === uploadTotal ? 'err' : 'warn');
  else if (uploadDone > 1) setF(`uploaded ${uploadDone} files`, 'ok');
  else setF('uploaded', 'ok');
  uploadTotal = 0;
}

// Recursive directory walk for dropped folders (Chromium/Firefox/Safari support webkitGetAsEntry)
async function readDirEntries(reader){
  return new Promise((res, rej) => reader.readEntries(res, rej));
}
async function entryToFiles(entry, prefix){
  const out = [];
  if (entry.isFile){
    const file = await new Promise((res, rej) => entry.file(res, rej));
    const name = (prefix + file.name).replace(/[\\/]+/g, '__');
    out.push(new File([file], name, {type: file.type, lastModified: file.lastModified}));
  } else if (entry.isDirectory){
    const reader = entry.createReader();
    let batch;
    while ((batch = await readDirEntries(reader)).length){
      for (const child of batch){
        const sub = await entryToFiles(child, prefix + entry.name + '/');
        out.push(...sub);
      }
    }
  }
  return out;
}

async function filesFromDataTransfer(dt){
  const items = dt.items ? Array.from(dt.items) : [];
  const result = [];
  let usedEntries = false;
  for (const it of items){
    if (it.kind !== 'file') continue;
    const entry = it.webkitGetAsEntry?.();
    if (entry){
      usedEntries = true;
      const fs = await entryToFiles(entry, '');
      result.push(...fs);
    }
  }
  if (!usedEntries){
    return Array.from(dt.files || []);
  }
  return result;
}

async function deleteFile(name){
  if (!confirm('Delete ' + name + '?')) return;
  try {
    const r = await authedFetch('/files/' + encodeURIComponent(name), {method:'DELETE'});
    if (!r.ok) throw new Error(await r.text());
    setF('deleted', 'ok');
  } catch(e){ setF('delete failed', 'err'); }
  refreshFiles(true);
}

document.getElementById('copyAll').onclick = async () => {
  const text = activeText();
  if (!text){ toast('nothing to copy'); return; }
  const ok = await copyToClipboard(text);
  toast(ok ? 'copied · ' + text.length.toLocaleString() + ' chars' : 'copy failed');
};

const counterEl = document.getElementById('counter');
function updateCounter(){
  const v = activeText();
  if (!v.length) { counterEl.textContent = NOTE_LABEL[activeNote]; return; }
  const lines = (v.match(/\n/g) || []).length + 1;
  counterEl.textContent = NOTE_LABEL[activeNote] + ' · ' + v.length.toLocaleString() + ' ch · ' + lines + ' ln';
}

pad.addEventListener('input', () => {
  if (!noteIsEditable()) return;
  noteTexts.today = pad.value;
  localDirty = true;
  setStatus('editing', 'warn');
  updateCounter();
  updateTitle();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(push, 400);
});

document.addEventListener('keydown', e => {
  if (document.getElementById('lightbox').classList.contains('show')) return;
  const inField = e.target === pad || e.target.tagName === 'INPUT';
  if ((e.metaKey || e.ctrlKey) && (e.key === 'f' || e.key === 'F')){
    e.preventDefault();
    ensureView('text');
    openNoteSearch();
    return;
  }
  // Cmd/Ctrl+S → force save now
  if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')){
    e.preventDefault();
    clearTimeout(saveTimer);
    if (localDirty) push();
    else toast('already saved');
    return;
  }
  // Cmd/Ctrl+1/2/3 → switch tabs
  if ((e.metaKey || e.ctrlKey) && ['1', '2', '3'].includes(e.key)){
    if (inField) return;
    e.preventDefault();
    toggleView(e.key === '1' ? 'text' : e.key === '2' ? 'files' : 'chat');
    return;
  }
  // "/" → focus filter (when not typing)
  if (e.key === '/' && !inField){
    e.preventDefault();
    if (getActive().has('files')) {
      ensureView('files');
      ffilter.focus();
      ffilter.select();
    } else {
      ensureView('text');
      openNoteSearch();
    }
  }
});

picker.addEventListener('change', () => {
  if (picker.files.length) uploadFiles(picker.files);
  picker.value = '';
});

let dragDepth = 0;
function hasFiles(e){
  const t = e.dataTransfer; if (!t) return false;
  const types = t.types ? Array.from(t.types) : [];
  if (types.includes('Files')) return true;
  if (types.includes('application/x-moz-file')) return true;
  // WebKitGTK external file-manager drops arrive as text/uri-list.
  if (types.includes('text/uri-list')) return true;
  return false;
}
// Unconditionally cancel default drag/drop on the page so the browser never
// navigates to the dropped file. We decide what to do (upload or ignore) in drop.
window.addEventListener('dragenter', e => {
  e.preventDefault();
  if (hasFiles(e)){
    dragDepth++;
    overlay.classList.add('show');
  }
});
window.addEventListener('dragover', e => {
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = hasFiles(e) ? 'copy' : 'none';
});
window.addEventListener('dragleave', e => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) overlay.classList.remove('show');
});
window.addEventListener('drop', async e => {
  e.preventDefault();
  dragDepth = 0;
  overlay.classList.remove('show');
  if (!e.dataTransfer) return;
  let files = [];
  try { files = await filesFromDataTransfer(e.dataTransfer); }
  catch(err){ files = Array.from(e.dataTransfer.files || []); }
  if (files.length){
    ensureView('files');
    uploadFiles(files);
    return;
  }
  // WebKitGTK (and some other Linux setups) deliver external file-manager
  // drops as text/uri-list — `file:///path/...` lines, with no File objects
  // in dataTransfer.files. The local-only /upload-uri endpoint copies them
  // server-side.
  const uriList = e.dataTransfer.getData('text/uri-list')
                || e.dataTransfer.getData('text/plain') || '';
  const fileUris = uriList.split(/\r?\n/)
                          .map(s => s.trim())
                          .filter(s => s.startsWith('file://'));
  if (!fileUris.length) return;
  ensureView('files');
  uploadFileUris(fileUris);
});

document.addEventListener('paste', e => {
  const files = Array.from(e.clipboardData?.files || []);
  if (files.length){ ensureView('files'); uploadFiles(files); }
});

// Preview/lightbox controls
const lb = document.getElementById('lightbox');
const lbContent = lb.querySelector('.lb-content');
lbContent.addEventListener('touchstart', e => {
  if (!lb.classList.contains('show')) return;
  if (e.touches.length === 2 && previewZoom.target) {
    e.preventDefault();
    previewZoom.touch = {
      mode: 'pinch',
      dist: dist(e.touches[0], e.touches[1]),
      scale: previewZoom.scale,
      x: previewZoom.x,
      y: previewZoom.y,
    };
    return;
  }
  if (e.touches.length === 1) {
    const t = e.touches[0];
    previewZoom.touch = {
      mode: 'drag',
      sx: t.clientX,
      sy: t.clientY,
      x: previewZoom.x,
      y: previewZoom.y,
      scale: previewZoom.scale,
    };
  }
}, {passive:false});
lbContent.addEventListener('touchmove', e => {
  if (!lb.classList.contains('show') || !previewZoom.touch) return;
  if (e.touches.length === 2 && previewZoom.touch.mode === 'pinch' && previewZoom.target) {
    e.preventDefault();
    const nextScale = previewZoom.touch.scale *
      (dist(e.touches[0], e.touches[1]) / Math.max(1, previewZoom.touch.dist));
    setPreviewZoom(nextScale, previewZoom.x, previewZoom.y);
    return;
  }
  if (e.touches.length === 1 && previewZoom.touch.mode === 'drag') {
    const t = e.touches[0];
    const dx = t.clientX - previewZoom.touch.sx;
    const dy = t.clientY - previewZoom.touch.sy;
    if (previewZoom.target && previewZoom.scale > 1.01) {
      e.preventDefault();
      setPreviewZoom(previewZoom.scale, previewZoom.touch.x + dx, previewZoom.touch.y + dy);
      return;
    }
    if (Math.abs(dx) > Math.abs(dy) * 1.3) {
      e.preventDefault();
    }
  }
}, {passive:false});
lbContent.addEventListener('touchend', e => {
  if (!lb.classList.contains('show') || !previewZoom.touch) return;
  const touch = previewZoom.touch;
  previewZoom.touch = null;
  if (touch.mode === 'pinch') {
    e.preventDefault();
    settlePreviewZoom();
    return;
  }
  if (touch.mode !== 'drag') return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touch.sx;
  const dy = t.clientY - touch.sy;
  if (previewZoom.target && previewZoom.scale > 1.01) {
    e.preventDefault();
    settlePreviewZoom();
    return;
  }
  if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.4) {
    e.preventDefault();
    navPreview(dx < 0 ? 1 : -1);
  }
}, {passive:false});
lbContent.addEventListener('dblclick', e => {
  if (!previewZoom.target) return;
  e.preventDefault();
  if (previewZoom.scale > 1.01) setPreviewZoom(1, 0, 0);
  else setPreviewZoom(2.5, 0, 0);
});
lb.addEventListener('click', e => {
  const navBtn = e.target.closest('.lb-nav');
  if (navBtn) {
    e.preventDefault();
    e.stopPropagation();
    if (!navBtn.disabled) navPreview(navBtn.classList.contains('prev') ? -1 : 1);
    return;
  }
  if (e.target === lb) return closePreview();
  if (e.target.closest('.lb-close')) {
    e.preventDefault();
    e.stopPropagation();
    return closePreview();
  }
  if (e.target.closest('.lb-content') === lb.querySelector('.lb-content')
      && e.target === lb.querySelector('.lb-content')) {
    return closePreview();
  }
});
document.addEventListener('keydown', e => {
  if (!lb.classList.contains('show')) return;
  if (['Escape', 'ArrowRight', 'ArrowLeft'].includes(e.key)){
    e.preventDefault();
    e.stopPropagation();
  }
  if (e.key === 'Escape') closePreview();
  else if (e.key === 'ArrowRight') navPreview(1);
  else if (e.key === 'ArrowLeft') navPreview(-1);
});

function startEvents(){
  if (!window.EventSource) return;
  try {
    const es = new EventSource('/events');
    es.addEventListener('ready', () => { eventsConnected = true; });
    es.addEventListener('notes', () => pull());
    es.addEventListener('files', () => { refreshFiles(true); refreshSteward(true); });
    es.addEventListener('chat', () => { refreshChat(true); refreshSteward(true); });
    es.addEventListener('presence', () => { refreshPresence(true); refreshSteward(true); });
    es.onerror = () => { eventsConnected = false; };
  } catch(e){ /* polling remains the fallback */ }
}

setStatus('connecting');
applyDefaultOrientation();
renderActiveNote({keepPos:false});
if (isMobile() && getActive().has('text')) activateReadMode();
pull();
refreshFiles();
refreshChat();
refreshSteward();
refreshPresence();
handleChatHash();
startEvents();
setInterval(() => { if (!eventsConnected) pull(); }, 2500);
setInterval(() => { if (!eventsConnected) refreshFiles(); }, 5000);
setInterval(() => { if (!eventsConnected) refreshChat(); }, 4000);
setInterval(() => { if (!eventsConnected) refreshPresence(); }, 8000);
</script>
</body></html>
"""

def clean_note_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)

def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    tmp.replace(path)
    try:
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)

def atomic_write_text(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))

def append_note_block(path: Path, block: str, heading: str | None = None) -> None:
    block = clean_note_text(block)
    if not block:
        return
    if heading:
        block = f"## {heading}\n\n{block}"
    current = path.read_text("utf-8", errors="replace")
    pieces = [current.rstrip(), block] if current.strip() else [block]
    atomic_write_text(path, "\n\n".join(p for p in pieces if p).rstrip() + "\n")

def local_today() -> dt.date:
    return dt.date.today()

def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())

def format_day(day: dt.date) -> str:
    return day.strftime("%A, %b %-d, %Y") if os.name != "nt" else day.strftime("%A, %b %#d, %Y")

def format_week(start: dt.date) -> str:
    end = start + dt.timedelta(days=6)
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b')} {start.day}-{end.day}, {end.year}"
    if start.year == end.year:
        return f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {end.year}"
    return f"{start.isoformat()} to {end.isoformat()}"

def load_note_state() -> dict:
    today = local_today()
    default = {"today": today.isoformat(), "week_start": week_start(today).isoformat()}
    try:
        state = json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    try:
        dt.date.fromisoformat(state.get("today", ""))
        dt.date.fromisoformat(state.get("week_start", ""))
    except (TypeError, ValueError):
        return default
    return {"today": state["today"], "week_start": state["week_start"]}

def save_note_state(state: dict) -> None:
    atomic_write_text(STATE_FILE, json.dumps(state, sort_keys=True) + "\n")

def rollover_notes_locked() -> dict:
    state = load_note_state()
    current = local_today()
    try:
        stored = dt.date.fromisoformat(state["today"])
    except (KeyError, ValueError):
        stored = current
    try:
        stored_week = dt.date.fromisoformat(state["week_start"])
    except (KeyError, ValueError):
        stored_week = week_start(stored)

    if stored >= current:
        if stored > current:
            state = {"today": current.isoformat(), "week_start": week_start(current).isoformat()}
            save_note_state(state)
        return state

    today_text = DATA_FILE.read_text("utf-8", errors="replace")
    append_note_block(WEEK_FILE, today_text, format_day(stored))
    atomic_write(DATA_FILE, b"")

    # Sunday night rollover: after Sunday Today moves into Week, the completed
    # Monday-Sunday week is appended to Archive and Week starts clean.
    if stored.weekday() == 6 or week_start(stored) < week_start(current):
        week_text = WEEK_FILE.read_text("utf-8", errors="replace")
        append_note_block(ARCHIVE_FILE, week_text, f"Week of {format_week(stored_week)}")
        atomic_write(WEEK_FILE, b"")

    state = {"today": current.isoformat(), "week_start": week_start(current).isoformat()}
    save_note_state(state)
    return state

def note_rev(note: str) -> int:
    path = NOTE_FILES[note]
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0

def note_from_query(path: str, default: str = "today") -> str | None:
    parsed = urllib.parse.urlparse(path)
    note = urllib.parse.parse_qs(parsed.query).get("note", [default])[0]
    return note if note in NOTE_FILES else None

def notes_payload_locked(state: dict) -> dict:
    return {
        "today": state["today"],
        "week_start": state["week_start"],
        "notes": [
            {
                "id": note,
                "label": NOTE_LABELS[note],
                "text": NOTE_FILES[note].read_text("utf-8", errors="replace"),
                "rev": note_rev(note),
                "editable": note == "today",
                "color": NOTE_COLORS[note],
            }
            for note in NOTE_ORDER
        ],
    }

def parse_mentions(text: str) -> list[str]:
    seen = set()
    out = []
    for match in MENTION_RE.finditer(text):
        name = match.group(1)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out

def clean_chat_author(author: object) -> str:
    text = str(author or "anon").strip()
    text = re.sub(r"[^A-Za-z0-9_. -]+", "", text).strip()
    return text[:48] or "anon"

def clean_chat_kind(kind: object) -> str:
    text = str(kind or "message").strip().lower()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")
    return text[:32] or "message"

def clean_chat_file_ref(value: object) -> str | None:
    name = urllib.parse.unquote(str(value or "")).replace("\x00", "").strip()
    if not name or name in (".", "..") or name.startswith("."):
        return None
    if "/" in name or "\\" in name or len(name) > 255:
        return None
    return name

def clean_chat_files(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    files = []
    for item in value[:20]:
        name = clean_chat_file_ref(item)
        if name:
            files.append(name)
    return list(dict.fromkeys(files))

def clean_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def clean_flags(payload: dict) -> dict:
    flags = {}
    raw = payload.get("flags")
    if isinstance(raw, dict):
        for key, value in raw.items():
            name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(key).strip().lower()).strip("-")
            if name:
                flags[name[:40]] = clean_bool(value)
    for key in ("needs_model", "needs_user", "safe_apply", "destructive"):
        if key in payload:
            flags[key] = clean_bool(payload.get(key))
    return flags

def chat_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def read_chat_messages_locked(limit: int = 200, since: str | None = None) -> list[dict]:
    messages = deque(maxlen=max(1, limit))
    found_since = since is None
    try:
        lines = CHAT_FILE.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with lines:
        for line in lines:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if found_since:
                messages.append(msg)
            else:
                messages.append(msg)
                if msg.get("id") == since:
                    found_since = True
                    messages.clear()
    return list(messages)

def append_chat_message_locked(payload: dict) -> dict:
    text = str(payload.get("text", "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("empty message")
    if len(text) > 20_000:
        raise ValueError("message too long")
    mentions = payload.get("mentions")
    if not isinstance(mentions, list):
        mentions = parse_mentions(text)
    else:
        mentions = [str(m).strip().lstrip("@")[:64] for m in mentions if str(m).strip()]
        mentions = list(dict.fromkeys(m for m in mentions if re.match(r"^[A-Za-z0-9_.-]+$", m)))
    msg = {
        "id": uuid.uuid4().hex,
        "ts": chat_now(),
        "author": clean_chat_author(payload.get("author")),
        "text": text,
        "mentions": mentions,
        "target": mentions[0] if mentions else "",
        "kind": clean_chat_kind(payload.get("kind")),
    }
    for key in ("task_id", "status", "reply_to", "due"):
        if payload.get(key):
            msg[key] = str(payload.get(key))[:128]
    priority = str(payload.get("priority") or "").strip().lower()
    if priority in PRIORITY_VALUES:
        msg["priority"] = priority
    files = clean_chat_files(payload.get("files") or payload.get("attachments"))
    if files:
        msg["files"] = files
    flags = clean_flags(payload)
    if flags:
        msg["flags"] = flags
    if payload.get("reaction"):
        reaction = str(payload.get("reaction")).strip()
        reaction = re.sub(r"[^A-Za-z0-9_.+-]+", "-", reaction)[:32].strip("-")
        if reaction:
            msg["reaction"] = reaction
    with CHAT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return msg

def clean_presence_id(value: object) -> str:
    text = str(value or "").strip().lstrip("@")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "", text)
    return text[:64]

def clean_capabilities(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    caps = []
    for item in value[:16]:
        text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(item).strip().lower()).strip("-")
        if text:
            caps.append(text[:40])
    return list(dict.fromkeys(caps))

def load_presence_locked() -> dict:
    try:
        data = json.loads(PRESENCE_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}

def save_presence_locked(data: dict) -> None:
    atomic_write_text(PRESENCE_FILE, json.dumps(data, sort_keys=True) + "\n")

def upsert_presence_locked(payload: dict) -> dict:
    ident = clean_presence_id(payload.get("id") or payload.get("identity") or payload.get("name"))
    if not ident:
        raise ValueError("missing identity")
    targets = payload.get("targets") or []
    if not isinstance(targets, list):
        targets = []
    targets = [clean_presence_id(t) for t in targets]
    targets = [t for t in dict.fromkeys(targets) if t]
    rec = {
        "id": ident,
        "name": str(payload.get("name") or ident)[:80],
        "host": str(payload.get("host") or "")[:80],
        "role": str(payload.get("role") or "agent")[:40],
        "status": str(payload.get("status") or "online")[:40],
        "targets": targets,
        "capabilities": clean_capabilities(payload.get("capabilities")),
        "seen": chat_now(),
    }
    cwd = str(payload.get("cwd") or "").strip()
    if cwd:
        rec["cwd"] = cwd[:240]
    data = load_presence_locked()
    data[ident.lower()] = rec
    save_presence_locked(data)
    return rec

def presence_payload_locked() -> dict:
    data = load_presence_locked()
    now = dt.datetime.now(dt.timezone.utc)
    agents = []
    for rec in data.values():
        if not isinstance(rec, dict):
            continue
        seen_raw = str(rec.get("seen") or "")
        stale = True
        try:
            seen = dt.datetime.fromisoformat(seen_raw.replace("Z", "+00:00"))
            stale = (now - seen).total_seconds() > 45
        except ValueError:
            pass
        out = dict(rec)
        out["stale"] = stale
        agents.append(out)
    agents.sort(key=lambda r: (bool(r.get("stale")), str(r.get("id", "")).lower()))
    return {"agents": agents}

def file_rev_locked() -> int:
    rev = 0
    for p in FILES_DIR.iterdir():
        if p.is_file():
            try:
                rev = max(rev, int(p.stat().st_mtime_ns))
            except OSError:
                pass
    return rev

def file_items_locked() -> list[dict]:
    items = []
    for p in FILES_DIR.iterdir():
        if p.is_file():
            try:
                st = p.stat()
            except OSError:
                continue
            items.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return items

def status_from_kind(kind: str) -> str:
    return "claimed" if kind == "claim" else kind

def is_task_message(msg: dict) -> bool:
    kind = str(msg.get("kind") or "message")
    if kind == "task":
        return True
    if kind != "message" or msg.get("reply_to"):
        return False
    return bool(msg.get("mentions"))

def steward_state_locked(messages: list[dict] | None = None,
                         files: list[dict] | None = None) -> dict:
    messages = messages if messages is not None else read_chat_messages_locked(limit=1000)
    files = files if files is not None else file_items_locked()
    presence = presence_payload_locked()
    tasks: dict[str, dict] = {}
    task_order: list[str] = []
    task_summaries: set[str] = set()
    file_summaries: set[str] = set()

    for msg in messages:
        if not isinstance(msg, dict) or not is_task_message(msg):
            continue
        task_id = str(msg.get("id") or "")
        if not task_id:
            continue
        flags = msg.get("flags") if isinstance(msg.get("flags"), dict) else {}
        task = {
            "id": task_id,
            "message_id": task_id,
            "created_ts": msg.get("ts", ""),
            "updated_ts": msg.get("ts", ""),
            "author": msg.get("author", "anon"),
            "text": msg.get("text", ""),
            "targets": msg.get("mentions", []) if isinstance(msg.get("mentions"), list) else [],
            "target": msg.get("target", ""),
            "status": "open",
            "assignee": "",
            "priority": msg.get("priority", "normal"),
            "due": msg.get("due", ""),
            "files": msg.get("files", []) if isinstance(msg.get("files"), list) else [],
            "flags": flags,
            "needs_model": bool(flags.get("needs_model", True)),
            "needs_user": bool(flags.get("needs_user", False)),
            "safe_apply": bool(flags.get("safe_apply", True)),
            "destructive": bool(flags.get("destructive", False)),
            "history": [],
        }
        tasks[task_id] = task
        task_order.append(task_id)

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        kind = str(msg.get("kind") or "message")
        task_id = str(msg.get("task_id") or "")
        if kind == "summary":
            if task_id:
                task_summaries.add(task_id)
            for name in msg.get("files", []) if isinstance(msg.get("files"), list) else []:
                file_summaries.add(name)
        if task_id and task_id in tasks:
            task = tasks[task_id]
            if kind in TASK_STATUS_KINDS:
                task["status"] = status_from_kind(kind)
                task["assignee"] = msg.get("author", "")
                task["updated_ts"] = msg.get("ts", task.get("updated_ts", ""))
                task["history"].append({
                    "id": msg.get("id", ""),
                    "kind": kind,
                    "status": task["status"],
                    "author": msg.get("author", ""),
                    "ts": msg.get("ts", ""),
                    "text": msg.get("text", ""),
                })
            if kind == "attach" or msg.get("files"):
                for name in msg.get("files", []) if isinstance(msg.get("files"), list) else []:
                    if name not in task["files"]:
                        task["files"].append(name)

    tasks_list = [tasks[task_id] for task_id in task_order if task_id in tasks]
    counts = {"open": 0, "claimed": 0, "working": 0, "blocked": 0, "done": 0}
    for task in tasks_list:
        counts[task["status"]] = counts.get(task["status"], 0) + 1

    linked_by_file: dict[str, list[str]] = {}
    for task in tasks_list:
        for name in task.get("files", []):
            linked_by_file.setdefault(name, []).append(task["id"])

    file_meta = []
    for item in sorted(files, key=lambda it: it.get("mtime", 0), reverse=True):
        name = item.get("name", "")
        file_meta.append({
            **item,
            "linked_tasks": linked_by_file.get(name, []),
            "summary_status": "ready" if name in file_summaries else "pending",
            "needs_model": name not in file_summaries,
        })

    hooks = []
    for task in tasks_list:
        if task["status"] in TASK_ACTIVE_STATUSES and task.get("needs_model", True):
            hooks.append({
                "id": f"task:{task['id']}:intent",
                "trigger": "chat.task.open",
                "action": "model.extract_task_intent",
                "task_id": task["id"],
                "safe_apply": True,
                "destructive": False,
                "idempotency_key": f"task-intent:{task['id']}",
            })
        if task["status"] == "done" and task["id"] not in task_summaries:
            hooks.append({
                "id": f"task:{task['id']}:closeout",
                "trigger": "chat.task.done",
                "action": "model.summarize_task_result",
                "task_id": task["id"],
                "safe_apply": True,
                "destructive": False,
                "idempotency_key": f"task-closeout:{task['id']}",
            })
    for item in file_meta:
        if item.get("needs_model"):
            hooks.append({
                "id": f"file:{item['name']}:summary",
                "trigger": "file.added",
                "action": "model.summarize_file",
                "file": item["name"],
                "linked_tasks": item.get("linked_tasks", []),
                "safe_apply": True,
                "destructive": False,
                "idempotency_key": f"file-summary:{item['name']}:{item.get('mtime', 0)}:{item.get('size', 0)}",
            })

    return {
        "tasks": tasks_list,
        "counts": counts,
        "files": file_meta,
        "hooks": hooks,
        "presence": presence.get("agents", []),
        "modes": ["observe", "suggest", "apply-safe"],
    }

def safe_name(raw: str) -> str | None:
    name = urllib.parse.unquote(raw)
    name = name.replace("\x00", "").strip()
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name:
        return None
    if name.startswith("."):
        return None
    if len(name) > 255:
        return None
    return name

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress noisy GET /content + /files polls but log everything else.
        msg = fmt % args
        if (" /content" in msg or " /chat" in msg or " /events" in msg
                or " /steward" in msg
                or " /presence" in msg
                or (" /files/" in msg and ('" 200 ' in msg or '" 201 ' in msg or '" 206 ' in msg))
                or (" /upload-uri " in msg and '" 200 ' in msg)
                or " /files/ " in msg or " /files HTTP" in msg):
            return
        import sys as _sys
        print(f"[server] {self.client_address[0]} {msg}", file=_sys.stderr, flush=True)

    def _rev(self, note="today"):
        return note_rev(note)

    def _send_text(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body: self.wfile.write(body)

    def _authorized(self):
        if not PENPAD_TOKEN:
            return True
        token = self.headers.get("X-Penpad-Token", "")
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        if token == PENPAD_TOKEN:
            return True
        self._send_text(401, "unauthorized\n", headers={"WWW-Authenticate": "Bearer"})
        return False

    def _content_length(self) -> int | None:
        try:
            return int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "bad content length")
            return None

    def _within_limit(self, length: int, limit: int, label: str) -> bool:
        if length <= limit:
            return True
        self.send_error(413, f"{label} too large")
        return False

    def _send_event(self, event: str, payload: dict) -> None:
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(("data: " + json.dumps(payload, separators=(",", ":")) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _events(self):
        if not SSE_SEM.acquire(blocking=False):
            self.send_error(503, "too many event streams")
            return
        self.send_response(200)
        try:
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            with LOCK:
                last_notes = max(note_rev(n) for n in NOTE_ORDER)
                last_files = file_rev_locked()
                last_chat = int(CHAT_FILE.stat().st_mtime_ns)
                last_presence = int(PRESENCE_FILE.stat().st_mtime_ns)
            self._send_event("ready", {"ts": chat_now()})
            for i in range(60 * 60):
                time.sleep(1)
                with LOCK:
                    notes = max(note_rev(n) for n in NOTE_ORDER)
                    files = file_rev_locked()
                    chat = int(CHAT_FILE.stat().st_mtime_ns)
                    presence = int(PRESENCE_FILE.stat().st_mtime_ns)
                try:
                    if notes != last_notes:
                        last_notes = notes
                        self._send_event("notes", {"rev": notes})
                    if files != last_files:
                        last_files = files
                        self._send_event("files", {"rev": files})
                    if chat != last_chat:
                        last_chat = chat
                        self._send_event("chat", {"rev": chat})
                    if presence != last_presence:
                        last_presence = presence
                        self._send_event("presence", {"rev": presence})
                    if i and i % 15 == 0:
                        self._send_event("ping", {"ts": chat_now()})
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        finally:
            SSE_SEM.release()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_text(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/manifest.json":
            self._send_text(200, MANIFEST, "application/manifest+json",
                            {"Cache-Control": "no-store"})
        elif path == "/icon.svg":
            self._send_text(200, ICON_SVG, "image/svg+xml",
                            {"Cache-Control": "no-store"})
        elif path == "/favicon.ico":
            # alias to svg icon
            self._send_text(200, ICON_SVG, "image/svg+xml",
                            {"Cache-Control": "no-store"})
        elif path == "/content":
            note = note_from_query(self.path)
            if not note: return self.send_error(400, "bad note")
            with LOCK:
                state = rollover_notes_locked()
                data = NOTE_FILES[note].read_bytes()
                rev = self._rev(note)
            self._send_text(200, data, "text/plain; charset=utf-8",
                            {"Cache-Control": "no-store", "X-Rev": str(rev),
                             "X-Note": note, "X-Note-Date": state["today"]})
        elif path == "/notes":
            with LOCK:
                state = rollover_notes_locked()
                payload = notes_payload_locked(state)
            self._send_text(200, json.dumps(payload), "application/json",
                            {"Cache-Control": "no-store"})
        elif path == "/events":
            self._events()
        elif path == "/presence":
            with LOCK:
                payload = presence_payload_locked()
                rev = int(PRESENCE_FILE.stat().st_mtime_ns)
            self._send_text(200, json.dumps(payload), "application/json",
                            {"Cache-Control": "no-store", "X-Rev": str(rev)})
        elif path == "/chat":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(1000, max(1, int(query.get("limit", ["200"])[0])))
            except ValueError:
                limit = 200
            since = query.get("since", [""])[0] or None
            with LOCK:
                messages = read_chat_messages_locked(limit=limit, since=since)
                rev = int(CHAT_FILE.stat().st_mtime_ns)
            self._send_text(200, json.dumps({"messages": messages}), "application/json",
                            {"Cache-Control": "no-store", "X-Rev": str(rev)})
        elif path == "/steward":
            with LOCK:
                messages = read_chat_messages_locked(limit=1000)
                files = file_items_locked()
                payload = steward_state_locked(messages, files)
                rev = max(
                    int(CHAT_FILE.stat().st_mtime_ns),
                    file_rev_locked(),
                    int(PRESENCE_FILE.stat().st_mtime_ns),
                )
            self._send_text(200, json.dumps(payload), "application/json",
                            {"Cache-Control": "no-store", "X-Rev": str(rev)})
        elif path == "/files/" or path == "/files":
            with LOCK:
                items = file_items_locked()
            self._send_text(200, json.dumps(items), "application/json",
                            {"Cache-Control": "no-store"})
        elif path.startswith("/files/"):
            raw_name = parsed.path[len("/files/"):]
            query = urllib.parse.parse_qs(parsed.query)
            force_download = query.get("download", ["0"])[0] == "1"
            name = safe_name(raw_name)
            if not name: return self.send_error(400, "bad name")
            target = FILES_DIR / name
            if not target.is_file(): return self.send_error(404)
            try:
                target.resolve().relative_to(FILES_DIR.resolve())
            except ValueError:
                return self.send_error(400, "bad path")
            size = target.stat().st_size
            ext = Path(name).suffix.lower()
            mtype, _ = mimetypes.guess_type(name)
            if not mtype and ext in TEXT_EXT:
                mtype = "text/plain; charset=utf-8"
            inline = bool(mtype and (
                mtype.startswith(("image/", "video/", "audio/", "text/"))
                or mtype in INLINE_APP_TYPES
            ))
            if force_download:
                inline = False
            ctype = mtype if inline else "application/octet-stream"
            disp = "inline" if inline else "attachment"
            disp_header = f"{disp}; filename*=UTF-8\'\'{urllib.parse.quote(name)}"

            rng = self.headers.get("Range", "")
            start, end = 0, size - 1
            partial = False
            if rng.startswith("bytes="):
                try:
                    spec = rng[6:].split(",", 1)[0]
                    s, _, e = spec.partition("-")
                    if s == "":
                        # suffix range: bytes=-N
                        n = int(e)
                        start, end = max(0, size - n), size - 1
                    else:
                        start = int(s)
                        end = int(e) if e else size - 1
                    if start < 0 or end >= size or start > end:
                        raise ValueError()
                    partial = True
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return

            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Disposition", disp_header)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with target.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self._authorized():
            return
        if path == "/save":
            note = note_from_query(self.path)
            if note != "today":
                return self.send_error(403, "only Today is editable")
            length = self._content_length()
            if length is None or not self._within_limit(length, MAX_NOTE_BYTES, "note"):
                return
            body = self.rfile.read(length)
            with LOCK:
                rollover_notes_locked()
                atomic_write(DATA_FILE, body)
                rev = self._rev("today")
            self._send_text(200, b"", headers={"X-Rev": str(rev)})
        elif path == "/append":
            note = note_from_query(self.path)
            if note != "today":
                return self.send_error(403, "only Today is editable")
            length = self._content_length()
            if length is None or not self._within_limit(length, MAX_NOTE_BYTES, "append"):
                return
            body = self.rfile.read(length)
            with LOCK:
                rollover_notes_locked()
                with DATA_FILE.open("ab") as f:
                    f.write(body)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                rev = self._rev("today")
            self._send_text(200, b"", headers={"X-Rev": str(rev)})
        elif path == "/chat":
            length = self._content_length()
            if length is None or not self._within_limit(length, MAX_CHAT_BYTES, "chat"):
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"text": raw.decode("utf-8", errors="replace")}
            if not isinstance(payload, dict):
                return self.send_error(400, "bad chat payload")
            try:
                with LOCK:
                    msg = append_chat_message_locked(payload)
                    rev = int(CHAT_FILE.stat().st_mtime_ns)
            except ValueError as e:
                return self.send_error(400, str(e))
            self._send_text(201, json.dumps({"message": msg}), "application/json",
                            {"Cache-Control": "no-store", "X-Rev": str(rev)})
        elif path == "/presence":
            length = self._content_length()
            if length is None or not self._within_limit(length, MAX_CHAT_BYTES, "presence"):
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self.send_error(400, "bad presence payload")
            if not isinstance(payload, dict):
                return self.send_error(400, "bad presence payload")
            try:
                with LOCK:
                    rec = upsert_presence_locked(payload)
                    rev = int(PRESENCE_FILE.stat().st_mtime_ns)
            except ValueError as e:
                return self.send_error(400, str(e))
            self._send_text(200, json.dumps({"agent": rec}), "application/json",
                            {"Cache-Control": "no-store", "X-Rev": str(rev)})
        elif path == "/upload-uri":
            # Localhost-only: WebKitGTK delivers external file drops as
            # text/uri-list; the local widget posts those URIs here so the
            # server can copy the files server-side (same machine).
            host = self.client_address[0]
            if host not in ("127.0.0.1", "::1", "localhost"):
                return self.send_error(403, "localhost only")
            length = self._content_length()
            if length is None or not self._within_limit(length, 1024 * 1024, "uri list"):
                return
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            copied, skipped = [], []
            for raw in body.splitlines():
                line = raw.strip()
                if not line.startswith("file://"): continue
                src_path = urllib.parse.unquote(line[len("file://"):])
                src = Path(src_path)
                if not src.is_file():
                    skipped.append(src.name); continue
                try:
                    if src.stat().st_size > MAX_UPLOAD_BYTES:
                        skipped.append(src.name); continue
                except OSError:
                    skipped.append(src.name); continue
                name = safe_name(src.name)
                if not name:
                    skipped.append(src.name); continue
                dst = FILES_DIR / name
                tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.part")
                try:
                    with src.open("rb") as fin, tmp.open("wb") as fout:
                        while True:
                            chunk = fin.read(64 * 1024)
                            if not chunk: break
                            fout.write(chunk)
                        fout.flush()
                        try:
                            os.fsync(fout.fileno())
                        except OSError:
                            pass
                    with LOCK:
                        tmp.replace(dst)
                    copied.append(name)
                except OSError:
                    skipped.append(src.name)
                    if tmp.exists():
                        try: tmp.unlink()
                        except OSError: pass
            self._send_text(200, json.dumps({"copied": copied, "skipped": skipped}),
                            "application/json")
        else:
            self.send_error(404)

    def do_PUT(self):
        if not self._authorized():
            return
        if not self.path.startswith("/files/"):
            return self.send_error(404)
        name = safe_name(self.path[len("/files/"):])
        if not name: return self.send_error(400, "bad name")
        target = FILES_DIR / name
        try:
            target.resolve().parent.relative_to(FILES_DIR.resolve())
        except ValueError:
            return self.send_error(400, "bad path")
        length = self._content_length()
        if length is None or not self._within_limit(length, MAX_UPLOAD_BYTES, "upload"):
            return
        tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.part")
        remaining = length
        with tmp.open("wb") as f:
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk: break
                f.write(chunk)
                remaining -= len(chunk)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        if remaining != 0:
            try:
                tmp.unlink()
            except OSError:
                pass
            return self.send_error(400, "incomplete upload")
        with LOCK:
            tmp.replace(target)
        self._send_text(201, b"ok")

    def do_DELETE(self):
        if not self._authorized():
            return
        if not self.path.startswith("/files/"):
            return self.send_error(404)
        name = safe_name(self.path[len("/files/"):])
        if not name: return self.send_error(400, "bad name")
        target = FILES_DIR / name
        try:
            target.resolve().relative_to(FILES_DIR.resolve())
        except ValueError:
            return self.send_error(400, "bad path")
        if not target.is_file(): return self.send_error(404)
        with LOCK:
            target.unlink()
        self._send_text(200, b"ok")

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)

if __name__ == "__main__":
    with ReusableTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"penpad serving on :{PORT}  (text: {DATA_FILE}, files: {FILES_DIR})")
        httpd.serve_forever()
