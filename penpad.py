#!/usr/bin/env python3
"""penpad — shared notes + file drop, self-hosted. http://<host>:8767/"""
import http.server
import json
import mimetypes
import socketserver
import threading
import urllib.parse
from pathlib import Path

PORT = 8767
BASE = Path(__file__).parent
DATA_FILE = BASE / "penpad.txt"
FILES_DIR = BASE / "files"
DATA_FILE.touch(exist_ok=True)
FILES_DIR.mkdir(exist_ok=True)
LOCK = threading.Lock()

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
    "description": "Self-hosted shared notes and file drop",
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0e0e10">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="penpad">
<title>penpad</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
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
    --font-mono: 'JetBrains Mono', 'SF Mono', 'Berkeley Mono', 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
  }

  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }

  html, body {
    margin: 0; height: 100%;
    background: var(--bg); color: var(--text);
    font-family: var(--font-mono);
    -webkit-font-smoothing: antialiased;
    overscroll-behavior: none;
  }

  body {
    display: flex; flex-direction: column;
    padding-top: env(safe-area-inset-top);
    padding-bottom: env(safe-area-inset-bottom);
    padding-left: env(safe-area-inset-left);
    padding-right: env(safe-area-inset-right);
  }

  /* Bottom bar */
  header#bar {
    flex: 0 0 auto;
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px;
    background: var(--bg);
    border-top: 1px solid var(--border-subtle);
    position: sticky; bottom: 0; z-index: 10;
    order: 99;
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
  }
  section.view.active { display: flex; animation: viewIn 0.24s ease; }

  @keyframes viewIn {
    from { opacity: 0; transform: translateY(3px); }
    to { opacity: 1; transform: none; }
  }

  /* Text view */
  section.view[data-view="text"] { position: relative; }
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
  @media (hover: hover) {
    #copyAll { opacity: 0; }
    section[data-view="text"]:hover #copyAll,
    section[data-view="text"]:focus-within #copyAll { opacity: 0.7; }
    #copyAll:hover { opacity: 1 !important; }
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
    background: transparent;
    color: var(--text);
    border: 0; outline: 0;
    padding: 22px 26px;
    font-family: inherit;
    font-size: 14px; line-height: 1.65;
    resize: none;
    caret-color: var(--accent);
  }
  #pad::selection { background: var(--accent-soft); color: var(--text); }
  #pad::placeholder { color: var(--text-dim); font-style: italic; }

  /* Files view — upload control lives in the bottom bar */
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
    bottom: calc(72px + env(safe-area-inset-bottom));
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
    padding: 0 14px 24px;
    -webkit-overflow-scrolling: touch;
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
    overflow: auto;
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.55;
    white-space: pre;
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
    header#bar { padding: 12px; gap: 10px; }
    .tab { padding: 9px 11px; font-size: 13px; }
    .tab.active { padding: 9px 14px 9px 11px; }
    .tab svg { width: 18px; height: 18px; flex: 0 0 18px; }
    .tab.active .label { max-width: 100px; }
    #status { font-size: 9px; letter-spacing: 0.12em; }

    #pad { padding: 18px; font-size: 16px; }

    #upload-group { margin-left: 12px; gap: 8px; }
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
    <button class="tab" data-view="text" role="tab" title="Text" aria-label="Text view">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="4.5" x2="13" y2="4.5"/><line x1="3" y1="8" x2="13" y2="8"/><line x1="3" y1="11.5" x2="9" y2="11.5"/></svg>
      <span class="label">text</span>
    </button>
    <button class="tab" data-view="files" role="tab" title="Files" aria-label="Files view">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"><path d="M2 4.5a1 1 0 0 1 1-1h3.2l1.5 1.5h5.3a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z"/></svg>
      <span class="label">files</span>
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
  <button id="copyAll" title="copy all text" aria-label="copy all text">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"><rect x="5.5" y="3" width="7.5" height="9.5" rx="1.2"/><path d="M3 5.5v8a1 1 0 0 0 1 1h6.5"/></svg>
  </button>
  <textarea id="pad" spellcheck="false" autofocus placeholder="type, paste, share…"></textarea>
</section>

<section class="view" data-view="files">
  <div id="filter-row" style="display:none">
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="4"/><path d="m10.5 10.5 3 3"/></svg>
    <input id="ffilter" type="search" placeholder="filter files…" autocomplete="off" spellcheck="false">
    <span class="count"></span>
    <button id="sortBtn" title="cycle sort">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4v8m0 0-2-2m2 2 2-2M11 12V4m0 0-2 2m2-2 2 2"/></svg>
      <span class="sort-label">date</span>
    </button>
  </div>
  <div id="flist"></div>
</section>

<div id="overlay" aria-hidden="true">
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 33c-3.6 0-6.5-2.9-6.5-6.5 0-3.4 2.6-6.2 5.9-6.5C14.5 14.5 19 10.5 24.5 10.5c5.8 0 10.5 4.4 11 10.1 3.6.4 6.5 3.5 6.5 7.2 0 4-3.3 7.2-7.3 7.2H14z"/><path d="M24 24v14m-5-9 5-5 5 5"/></svg>
  <span class="text">drop to upload</span>
</div>

<div id="toast"><span class="dot"></span><span class="msg"></span></div>

<div id="lightbox" aria-hidden="true">
  <button class="lb-close" aria-label="close"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
  <button class="lb-nav prev" aria-label="previous"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3 5 8l5 5"/></svg></button>
  <button class="lb-nav next" aria-label="next"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 3 5 5-5 5"/></svg></button>
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

let serverRev = 0, localDirty = false, saveTimer = null;

const isMobile = () => window.matchMedia('(max-width: 640px)').matches;

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
  if (isMobile() && s.size > 1) s = new Set([s.has('text') ? 'text' : 'files']);
  applyActive(s);
}

tabs.forEach(t => t.addEventListener('click', () => toggleView(t.dataset.view)));

let wasMobile = isMobile();
window.addEventListener('resize', () => {
  const m = isMobile();
  if (m === wasMobile) return;
  wasMobile = m;
  let s = getActive();
  if (m && s.size > 1) applyActive(new Set([s.has('text') ? 'text' : 'files']));
  else if (!m && s.size === 0) applyActive(new Set(['text', 'files']));
  else if (!m && s.size === 1) applyActive(new Set(['text', 'files']));
});

initViews();

function setStatus(text, cls){ status.textContent = text; status.className = cls || ''; }
function setF(text, cls){ fstatus.textContent = text; fstatus.className = cls || ''; }

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
  document.title = (localDirty ? '● ' : '') + 'penpad';
}

async function pull(){
  try {
    const r = await fetch('/content', {cache:'no-store'});
    const rev = Number(r.headers.get('X-Rev') || 0);
    const text = await r.text();
    if (!localDirty && rev !== serverRev){
      const pos = pad.selectionStart;
      pad.value = text;
      pad.selectionStart = pad.selectionEnd = Math.min(pos, text.length);
      serverRev = rev;
      setStatus('synced', 'ok');
      updateCounter();
      updateTitle();
    }
  } catch(e){ setStatus('offline', 'err'); }
}

async function push(){
  try {
    const r = await fetch('/save', {method:'POST', body: pad.value});
    const rev = Number(r.headers.get('X-Rev') || 0);
    serverRev = rev;
    localDirty = false;
    setStatus('saved', 'ok');
    updateTitle();
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
        '<button class="iconbtn copy" title="copy URL" aria-label="copy URL">' + ICON.copy + '</button>' +
        '<button class="iconbtn dl' + (done ? ' done' : '') + '" title="download" aria-label="download">' +
          (done ? ICON.check : ICON.download) + '</button>' +
        '<button class="iconbtn del" title="delete" aria-label="delete">' + ICON.x + '</button>';
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
  } catch(e){ /* keep last view */ }
}

let previewIndex = -1;

async function openPreview(it){
  const lb = document.getElementById('lightbox');
  const content = lb.querySelector('.lb-content');
  const url = '/files/' + encodeURIComponent(it.name);
  const type = fileType(it.name);

  previewIndex = lastItems.findIndex(x => x.name === it.name);
  updateNavButtons();

  lb.querySelector('.lb-name').textContent = it.name;
  content.innerHTML = '<div class="lb-msg">loading…</div>';
  lb.classList.add('show');

  if (type === 'image') {
    content.innerHTML = '';
    const img = new Image();
    img.alt = '';
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
  const content = lb.querySelector('.lb-content');
  // pause any media before clearing to release resources
  content.querySelectorAll('video,audio').forEach(m => { try { m.pause(); } catch(e){} });
  content.innerHTML = '';
  previewIndex = -1;
}

function navPreview(delta){
  if (previewIndex < 0) return;
  let i = previewIndex + delta;
  while (i >= 0 && i < lastItems.length && !isPreviewable(lastItems[i].name)) i += delta;
  if (i < 0 || i >= lastItems.length) return;
  openPreview(lastItems[i]);
}

function updateNavButtons(){
  const lb = document.getElementById('lightbox');
  const prev = lb.querySelector('.lb-nav.prev');
  const next = lb.querySelector('.lb-nav.next');
  let hasPrev = false, hasNext = false;
  for (let i = previewIndex - 1; i >= 0; i--) if (isPreviewable(lastItems[i].name)) { hasPrev = true; break; }
  for (let i = previewIndex + 1; i < lastItems.length; i++) if (isPreviewable(lastItems[i].name)) { hasNext = true; break; }
  prev.disabled = !hasPrev;
  next.disabled = !hasNext;
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

function setProgress(pct){
  if (pct == null) {
    upprog.classList.add('hidden');
    upfill.style.width = '0%';
    return;
  }
  upprog.classList.remove('hidden');
  upfill.style.width = pct + '%';
}

function uploadOne(f, onTotalProgress){
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/files/' + encodeURIComponent(f.name));
    xhr.upload.onprogress = e => {
      if (!e.lengthComputable) return;
      const pct = Math.round(100 * e.loaded / e.total);
      setF('uploading ' + f.name + ' · ' + pct + '%', 'warn');
      if (onTotalProgress) onTotalProgress(e.loaded, e.total);
    };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300)
      ? resolve()
      : reject(new Error('http ' + xhr.status));
    xhr.onerror = () => reject(new Error('network'));
    xhr.send(f);
  });
}

async function uploadFiles(files){
  let ok = 0, fail = 0;
  // Compute overall total so the bar tracks the batch, not per-file.
  const totalBytes = files.reduce((n, f) => n + (f.size || 0), 0);
  let sentBefore = 0;
  setProgress(0);
  for (const f of files){
    setF('uploading ' + f.name, 'warn');
    try {
      await uploadOne(f, (loaded, total) => {
        const pct = totalBytes
          ? Math.min(100, Math.round(100 * (sentBefore + loaded) / totalBytes))
          : null;
        if (pct != null) setProgress(pct);
      });
      sentBefore += f.size || 0;
      flashNames.add(f.name);
      ok++;
    } catch(e){
      fail++;
    }
  }
  // Brief settle at 100% so the fill is visible on small files, then hide.
  setProgress(100);
  setTimeout(() => setProgress(null), 500);
  if (fail) setF(`uploaded ${ok} · ${fail} failed`, fail === files.length ? 'err' : 'warn');
  else if (ok > 1) setF(`uploaded ${ok} files`, 'ok');
  else setF('uploaded', 'ok');
  refreshFiles(true);
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
    const r = await fetch('/files/' + encodeURIComponent(name), {method:'DELETE'});
    if (!r.ok) throw new Error(await r.text());
    setF('deleted', 'ok');
  } catch(e){ setF('delete failed', 'err'); }
  refreshFiles(true);
}

document.getElementById('copyAll').onclick = async () => {
  if (!pad.value){ toast('nothing to copy'); return; }
  const ok = await copyToClipboard(pad.value);
  toast(ok ? 'copied · ' + pad.value.length.toLocaleString() + ' chars' : 'copy failed');
};

const counterEl = document.getElementById('counter');
function updateCounter(){
  const v = pad.value;
  if (!v.length) { counterEl.textContent = ''; return; }
  const lines = (v.match(/\n/g) || []).length + 1;
  counterEl.textContent = v.length.toLocaleString() + ' ch · ' + lines + ' ln';
}

pad.addEventListener('input', () => {
  localDirty = true;
  setStatus('editing', 'warn');
  updateCounter();
  updateTitle();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(push, 400);
});

document.addEventListener('keydown', e => {
  const inField = e.target === pad || e.target.tagName === 'INPUT';
  // Cmd/Ctrl+S → force save now
  if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')){
    e.preventDefault();
    clearTimeout(saveTimer);
    if (localDirty) push();
    else toast('already saved');
    return;
  }
  // Cmd/Ctrl+1/2 → switch tabs
  if ((e.metaKey || e.ctrlKey) && (e.key === '1' || e.key === '2')){
    if (inField) return;
    e.preventDefault();
    toggleView(e.key === '1' ? 'text' : 'files');
    return;
  }
  // "/" → focus filter (when not typing)
  if (e.key === '/' && !inField){
    e.preventDefault();
    ensureView('files');
    ffilter.focus();
    ffilter.select();
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
  setF('uploading ' + fileUris.length + ' file' + (fileUris.length>1?'s':'…'), 'warn');
  try {
    const r = await fetch('/upload-uri', {
      method: 'POST',
      headers: {'Content-Type': 'text/uri-list'},
      body: fileUris.join('\n'),
    });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    const n = (j.copied || []).length;
    const skipped = (j.skipped || []).length;
    if (n) setF('uploaded ' + n, 'ok');
    else if (skipped) setF('skipped ' + skipped, 'err');
    (j.copied || []).forEach(name => flashNames.add(name));
    refreshFiles(true);
  } catch(err){
    setF('upload failed', 'err');
  }
});

document.addEventListener('paste', e => {
  const files = Array.from(e.clipboardData?.files || []);
  if (files.length){ ensureView('files'); uploadFiles(files); }
});

// Preview/lightbox controls
const lb = document.getElementById('lightbox');
lb.addEventListener('click', e => {
  if (e.target === lb) return closePreview();
  if (e.target.closest('.lb-close')) return closePreview();
  if (e.target.closest('.lb-content') === lb.querySelector('.lb-content')
      && e.target === lb.querySelector('.lb-content')) {
    return closePreview();
  }
  const navBtn = e.target.closest('.lb-nav');
  if (navBtn) { e.stopPropagation(); navPreview(navBtn.classList.contains('prev') ? -1 : 1); }
});
document.addEventListener('keydown', e => {
  if (!lb.classList.contains('show')) return;
  if (e.key === 'Escape') closePreview();
  else if (e.key === 'ArrowRight') navPreview(1);
  else if (e.key === 'ArrowLeft') navPreview(-1);
});

setStatus('connecting');
pull();
refreshFiles();
setInterval(pull, 1500);
setInterval(refreshFiles, 3000);
</script>
</body></html>
"""

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
        if " /content" in msg or " /files/ " in msg or " /files HTTP" in msg:
            return
        import sys as _sys
        print(f"[server] {self.client_address[0]} {msg}", file=_sys.stderr, flush=True)

    def _rev(self):
        return int(DATA_FILE.stat().st_mtime_ns)

    def _send_text(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body: self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send_text(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/manifest.json":
            self._send_text(200, MANIFEST, "application/manifest+json",
                            {"Cache-Control": "no-store"})
        elif self.path == "/icon.svg":
            self._send_text(200, ICON_SVG, "image/svg+xml",
                            {"Cache-Control": "no-store"})
        elif self.path == "/favicon.ico":
            # alias to svg icon
            self._send_text(200, ICON_SVG, "image/svg+xml",
                            {"Cache-Control": "no-store"})
        elif self.path == "/content":
            with LOCK:
                data = DATA_FILE.read_bytes()
                rev = self._rev()
            self._send_text(200, data, "text/plain; charset=utf-8",
                            {"Cache-Control": "no-store", "X-Rev": str(rev)})
        elif self.path == "/files/" or self.path == "/files":
            items = []
            with LOCK:
                for p in FILES_DIR.iterdir():
                    if p.is_file():
                        st = p.stat()
                        items.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
            self._send_text(200, json.dumps(items), "application/json",
                            {"Cache-Control": "no-store"})
        elif self.path.startswith("/files/"):
            parsed = urllib.parse.urlparse(self.path)
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
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            with LOCK:
                DATA_FILE.write_bytes(body)
                rev = self._rev()
            self._send_text(200, b"", headers={"X-Rev": str(rev)})
        elif self.path == "/upload-uri":
            # Localhost-only: WebKitGTK delivers external file drops as
            # text/uri-list; the local widget posts those URIs here so the
            # server can copy the files server-side (same machine).
            host = self.client_address[0]
            if host not in ("127.0.0.1", "::1", "localhost"):
                return self.send_error(403, "localhost only")
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            import sys as _sys
            print(f"[server] /upload-uri from {host} body={body!r}",
                  file=_sys.stderr, flush=True)
            copied, skipped = [], []
            for raw in body.splitlines():
                line = raw.strip()
                if not line.startswith("file://"): continue
                src_path = urllib.parse.unquote(line[len("file://"):])
                src = Path(src_path)
                if not src.is_file():
                    skipped.append(src.name); continue
                name = safe_name(src.name)
                if not name:
                    skipped.append(src.name); continue
                dst = FILES_DIR / name
                tmp = dst.with_suffix(dst.suffix + ".part")
                try:
                    with LOCK:
                        with src.open("rb") as fin, tmp.open("wb") as fout:
                            while True:
                                chunk = fin.read(64 * 1024)
                                if not chunk: break
                                fout.write(chunk)
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
        if not self.path.startswith("/files/"):
            return self.send_error(404)
        name = safe_name(self.path[len("/files/"):])
        if not name: return self.send_error(400, "bad name")
        target = FILES_DIR / name
        try:
            target.resolve().parent.relative_to(FILES_DIR.resolve())
        except ValueError:
            return self.send_error(400, "bad path")
        length = int(self.headers.get("Content-Length", "0"))
        tmp = target.with_suffix(target.suffix + ".part")
        remaining = length
        with LOCK:
            with tmp.open("wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk: break
                    f.write(chunk)
                    remaining -= len(chunk)
            tmp.replace(target)
        self._send_text(201, b"ok")

    def do_DELETE(self):
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

if __name__ == "__main__":
    with ReusableTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"penpad serving on :{PORT}  (text: {DATA_FILE}, files: {FILES_DIR})")
        httpd.serve_forever()
