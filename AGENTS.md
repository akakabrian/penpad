# penpad — notes for agents

This file tells any agent (an LLM like Claude, a watchdog script, a
small automation) how to interact with penpad's data. Read it and
you're set — there's no schema to load, no auth to negotiate.

## What penpad is

A tiny self-hosted app: **three note files plus one shared folder**,
synced in real time across every device and agent on the same host
(or the same network, via the HTTP API).

## Where the data is

If penpad is installed normally, everything lives under a single
directory (default `~/penpad/`):

```
~/penpad/
├── penpad.txt          ← Today note (plain UTF-8)
├── penpad.week.txt     ← Week rollup
├── penpad.archive.txt  ← Archive rollup
└── files/         ← shared file drop (one regular file per entry)
    ├── <name>
    └── …
```

`penpad.txt` is Today, and it is the only note users edit directly.
At the Sunday-to-Monday midnight rollover, Today is appended into Week,
then the completed Monday-Sunday Week is appended into Archive and Week
starts clean. If you have filesystem access to the penpad host, this is
the simplest interface. Read, write, create, delete — they just work.
The server picks up changes on disk automatically and pushes them to
connected clients.

If you're remote (no filesystem access), use the HTTP API below. The
default address is `http://<host>:8767`.

## Conventions

- **Today text is free-form.** No required format. Users treat it as a
  scratchpad. Week and Archive are rollups.
- **Appending is safer than overwriting.** If you're making additions
  (notes, replies, results), append to the end of `penpad.txt` rather
  than rewriting the whole file. Concurrent edits are last-writer-wins.
- **Use one-line prefixes for multi-party pads.** If you're sharing the
  pad with a human, a convention like `> me:` / `> agent:` lets both
  sides see who wrote what. No schema; just a convention.
- **Files are stored by filename.** Use short, safe names (ASCII
  letters, digits, `._-`). The server sanitizes with `safe_name()`,
  so exotic characters get stripped.
- **Atomic writes.** When writing `penpad.txt`, write to a temp file
  and `rename()` it over the target to avoid half-written reads.

## Watching for changes

- **Filesystem-local:** `inotifywait -m ~/penpad/penpad.txt ~/penpad/files/`
  gives you real-time events.
- **Over HTTP:** `GET /content` returns an `X-Rev` header. Poll every
  few seconds, compare the rev, fetch if changed. That's how the
  built-in clients stay in sync.
- **Chat routing:** `GET /chat?since=<id>` returns append-only chat
  messages. Only react to messages that mention your configured
  `@machine`, `@agent`, `@all`, or `@agents` target.
- **Chat metadata:** replies, pins, unpins, reactions, unreact events,
  acknowledgements, and task states are all append-only chat messages. Use `kind`,
  `task_id`, `reply_to`, `reaction`, and `status` fields instead of
  editing old messages.
- **Permalinks:** web chat messages can be addressed as `#chat-<id>`.
  This is a browser affordance; agents should still route by mention and
  message id.
- **Presence:** watchers can `POST /presence` so humans get `@` mention
  autocomplete and can see which agent identities are currently listening.
- **Events:** `GET /events` is a Server-Sent Events stream for notes,
  files, chat, and presence changes. Polling remains fine as a fallback.

## HTTP API (remote agents)

Tiny, stable. All methods accept/return raw bytes except `/files/`
listing (JSON).

| method | path                       | purpose                                 |
|--------|----------------------------|------------------------------------------|
| GET    | `/notes`                   | read Today, Week, and Archive as JSON     |
| GET    | `/content`                 | read Today text; `X-Rev` header = revision |
| GET    | `/content?note=week`       | read Week text                            |
| GET    | `/content?note=archive`    | read Archive text                         |
| POST   | `/save`                    | replace Today text (body = new content)   |
| POST   | `/append`                  | append to Today text                      |
| GET    | `/chat?limit=200`          | read append-only chat messages             |
| GET    | `/chat?since=<id>`         | read messages after a known message id     |
| POST   | `/chat`                    | append chat or metadata event              |
| GET    | `/presence`                | list known watcher identities              |
| POST   | `/presence`                | heartbeat your identity and targets        |
| GET    | `/events`                  | SSE stream for notes/files/chat/presence   |
| GET    | `/files/`                  | list files: `[{name, size, mtime}, ...]` |
| GET    | `/files/<name>`            | fetch file (supports `Range`)            |
| GET    | `/files/<name>?download=1` | same, forces `Content-Disposition: attachment` |
| PUT    | `/files/<name>`            | upload file (body = bytes)               |
| DELETE | `/files/<name>`            | delete file                              |
| POST   | `/upload-uri`              | server-side copy from local `file://` URIs (loopback only) |

By default there is no authentication. If the server is started with
`PENPAD_TOKEN`, mutating requests must include `X-Penpad-Token: <token>`
or `Authorization: Bearer <token>`. Reads remain open so simple local
inspection stays frictionless.

Mutating bodies are bounded to avoid accidental device pain:
`PENPAD_MAX_NOTE_MB` defaults to `5`, `PENPAD_MAX_CHAT_KB` defaults to
`64`, and `PENPAD_MAX_UPLOAD_MB` defaults to `512`.

## Minimal examples

**Read the pad (filesystem):**
```python
text = Path("~/penpad/penpad.txt").expanduser().read_text()
```

**Append a line (filesystem):**
```python
p = Path("~/penpad/penpad.txt").expanduser()
with p.open("a") as f:
    f.write("\n> agent: finished the thing\n")
```

**Append a line (HTTP):**
```python
httpx.post("http://localhost:8767/append",
           content=b"\n> agent: finished the thing\n")
```

**Post a routed chat message (HTTP):**
```python
httpx.post("http://localhost:8767/chat",
           json={"author": "agent-mini",
                 "text": "@brian finished the smoke test"})
```

**Watch routed chat from a machine:**
```sh
PENPAD_AGENT=mini python3 scripts/penpad-agent-watch.py --target mini --ack --sse
```

The watcher skips old messages on first run unless `--replay` is passed,
persists its last seen id in `~/.penpad-agent-watch-<name>.json`, posts
presence heartbeats, and can hand matching message JSON to a local agent
harness with `--exec`.

**Read the pad (HTTP):**
```python
import httpx
r = httpx.get("http://localhost:8767/content")
text, rev = r.text, r.headers["X-Rev"]
```

**Drop a file (filesystem):**
```python
shutil.copy("/tmp/report.pdf", "~/penpad/files/report.pdf")
```

**Drop a file (HTTP):**
```python
httpx.put("http://localhost:8767/files/report.pdf",
          content=Path("/tmp/report.pdf").read_bytes())
```

## What not to do

- **Don't delete `penpad.txt`.** Truncate it (`""`) if you need to
  empty it; the server expects the file to exist.
- **Don't write gigabyte files into `files/` casually.** No size
  limit is enforced, but clients will happily OOM trying to render a
  huge text preview.
- **Don't bypass sanitization.** When creating files, stick to safe
  names. The server rejects path-traversal attempts (`..`, absolute
  paths), but writing directly to the FS you're on your own.
