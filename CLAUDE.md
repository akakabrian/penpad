# penpad — repo instructions for Claude Code

See [AGENTS.md](AGENTS.md) for the full cross-agent reference (where
the data lives, HTTP API, conventions). Everything there applies here.

## Working on this repo

- Server stays stdlib-only. No new dependencies in `penpad.py`.
- Three clients (web / widget / TUI) must keep behavior aligned for
  **copy URL / download / delete / upload**. When you change one,
  check the others.
- Warm-dark palette (`#0e0e10` / `#d4a373` / `#e6e3dc`) is the project's
  visual identity. See the top of `penpad.py` and `tui.tcss`. Don't
  drift.
- TUI deps are kept minimal: `textual` + `httpx` in `requirements.txt`.
- Log file is gitignored; don't add it by accident.
- Before committing: run `python3 -m py_compile penpad.py widget.py
  tui.py` at the least. Smoke-test the server and the TUI.

## When interacting with penpad data from this session

Today text is at `./penpad.txt`; Week and Archive rollups are at
`./penpad.week.txt` and `./penpad.archive.txt`; routed chat is at
`./penpad.chat.jsonl`; watcher presence is at `./penpad.presence.json`;
files are at `./files/`. Filesystem access is the simplest route.
Conventions in `AGENTS.md` apply — append rather than overwrite when
you're adding, use `> me:` / `> agent:` prefixes if you're sharing the
pad with a user in real time, and only react to chat messages that
explicitly mention your configured target.
