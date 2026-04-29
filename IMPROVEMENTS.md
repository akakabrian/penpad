# Penpad Improvement Plan

This version folds in the outside opinions, the follow-up product decisions,
and the implementation now in the repo. The direction is clear: Penpad should
stay a small personal primitive, but it should be safer and more useful when
many machines and many local agents are involved.

## Completed in this pass

- Three notes: editable Today, read-only Week, read-only Archive.
- Sunday-to-Monday rollover: Today moves into Week, then the completed Week
  moves into Archive and Week starts fresh.
- Search across Today, Week, and Archive, prioritized in that order.
- Mobile/PWA fixes: top view switcher, no horizontal page scroll, no zoom in
  notes/files, read-before-type behavior, keyboard dismissal on Files/Chat.
- File inspector fixes: image arrows move between files, arrows disappear
  while zoomed, swipe changes files only at 1x zoom, text and Markdown files
  preview with wrapping.
- FIFO upload queue: one active upload at a time, later uploads wait in order.
- Append-oriented API: `POST /append` for safer additive agent writes.
- Atomic server saves for Today.
- Append-only routed chat room with author, time, mentions, kind/status, and
  task ids for lightweight workflow messages.
- Agent watcher script with explicit mention routing, ack support, presence
  heartbeats, persisted last-seen state, optional local `--exec`, and SSE
  wakeups.
- Presence API and web `@` autocomplete for listening agents/machines.
- Task lifecycle buttons on routed messages: claim, working, done, blocked.
- Discord/Telegram-style chat affordances implemented append-only:
  replies, pinned messages, unpin events, and toggleable quick reactions.
- Slack-style chat affordances: copyable message permalinks and local
  unread dividers with mark-read behavior.
- Resource guardrails: bounded note/chat/upload request bodies, bounded SSE
  clients, daemon request threads for clean shutdown, and memory-capped chat
  reads.
- Optional default-off `PENPAD_TOKEN` for mutating routes, supported by web,
  TUI, widget, and watcher.
- `/events` Server-Sent Events stream for notes, files, chat, and presence.
- TUI upload streaming so large files are not read fully into memory.
- Removed the external Google Fonts dependency.

## Highest-value next improvements

1. **History and restore**
   Keep a rolling `.history/` of Today saves and rollovers, add
   `GET /history`, `GET /history/<rev>`, and `POST /restore?rev=...`.
   This is the biggest remaining protection against accidental overwrites.

2. **Export and backup helpers**
   Add `GET /export.tar.gz` plus `scripts/backup.sh` and `scripts/restore.sh`
   for the current notes, chat, and files. This preserves the "just files"
   philosophy while giving any device a fast grab-and-go backup.

3. **Optimistic save conflicts**
   Add `If-Match` or `X-Base-Rev` support to `/save`, then let web/TUI offer
   actions when the remote changed while local edits are dirty: reload remote,
   copy local, overwrite remote, or append local.

4. **Conditional reads**
   Add `ETag`/`If-None-Match` for `/content`, `/notes`, `/files/`, `/chat`,
   and `/presence`. SSE now reduces churn for the web client, but conditional
   reads still help TUI and simple scripts.

5. **Agent helper CLI**
   Add a tiny stdlib `penpad-cli` for `append`, `chat`, `watch`, `put`, `get`,
   `rm`, and `export`. It should be a convenience wrapper around the same API,
   not a second product.

6. **Operational endpoints**
   Add `GET /healthz` and `GET /stats` with uptime, revisions, file count,
   chat count, listener count, and upload/save counters.

7. **Read-only mode**
   Add `PENPAD_READONLY=1` to allow browsing notes/files/chat while rejecting
   writes and deletes. This pairs nicely with temporary demos or screen shares.

8. **Preview and file polish**
   Add pin/favorite files, rename, clearer binary/too-large preview messages,
   and consistent preview limits across web and TUI.

9. **Focused tests**
    Cover `safe_name`, rollover, `/append`, token checks, presence heartbeat,
    watcher state, upload queue assumptions, range requests, and path traversal.

## Product stance

The chat room should be a command room, not a universal autonomous trigger.
Humans write natural language, but agents only react when explicitly mentioned
and only after a watcher on that machine has opted in. Penpad should make those
hand-offs obvious and inspectable without becoming a multi-user platform.
