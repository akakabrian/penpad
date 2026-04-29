# Changelog

## Unreleased

### Added

- Derived `/steward` state for task inboxes, file/task links, and
  deterministic model hook jobs.
- Chat message metadata for attached files, priority, due dates, and
  steward safety flags.
- Watcher presence capabilities and working-directory metadata.

### Changed

- Chat task state events now drive a compact task inbox instead of
  needing humans to infer state from the message stream.
- Local `.env` files are ignored for API keys and other secrets.

## 0.2.0 - 2026-04-29

### Added

- Three-note workflow: editable Today, read-only Week (M-Su), and
  read-only Archive.
- Automatic rollover: Today moves into Week daily; after Sunday, the
  completed Week moves into Archive and Week starts clean.
- Search across Today, Week, and Archive.
- `POST /append` for safer additive agent workflows.
- Routed append-only chat room with mentions, replies, pins/unpins,
  toggleable reactions, task states, permalinks, local unread dividers,
  and watcher presence.
- `scripts/penpad-agent-watch.py` for opt-in local agent listeners.
- `/presence` and `/events` APIs.
- Optional `PENPAD_TOKEN` for mutating routes.
- Configurable body/upload limits:
  `PENPAD_MAX_NOTE_MB`, `PENPAD_MAX_CHAT_KB`, `PENPAD_MAX_UPLOAD_MB`.
- Regression tests under `tests/`.

### Changed

- Mobile/PWA navigation moved to the top so it is not covered by the
  keyboard.
- Mobile text mode supports read-before-type behavior so scrolling does
  not summon the keyboard.
- File uploads are queued FIFO in the PWA.
- TUI uploads stream from disk instead of loading whole files into memory.
- Server saves and uploads use safer atomic write paths.
- Web clients use SSE wakeups with polling fallback.
- External Google Fonts dependency removed.

### Fixed

- Full-screen file inspector arrows navigate previous/next previewable
  file instead of switching back to notes.
- Image inspector arrows disappear while zoomed.
- Text and Markdown file previews wrap and scroll inside the inspector.
- Expected client disconnects no longer print noisy server tracebacks.
- Watcher backs off when the server is offline instead of noisy hot
  looping.
