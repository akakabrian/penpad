# penpad — notes for agents

This file tells any agent (an LLM like Claude, a watchdog script, a
small automation) how to interact with penpad's data. Read it and
you're set — there's no schema to load, no auth to negotiate.

## What penpad is

A tiny self-hosted app: **one shared text file plus one shared
folder**, synced in real time across every device and agent on the
same host (or the same network, via the HTTP API).

## Where the data is

If penpad is installed normally, everything lives under a single
directory (default `~/penpad/`):

```
~/penpad/
├── penpad.txt     ← the shared text pad (plain UTF-8)
└── files/         ← shared file drop (one regular file per entry)
    ├── <name>
    └── …
```

If you have filesystem access to the penpad host, this is the simplest
interface. Read, write, create, delete — they just work. The server
picks up changes on disk automatically and pushes them to connected
clients.

If you're remote (no filesystem access), use the HTTP API below. The
default address is `http://<host>:8767`.

## Conventions

- **Pad text is free-form.** No required format. Users treat it as a
  scratchpad.
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

## HTTP API (remote agents)

Tiny, stable. All methods accept/return raw bytes except `/files/`
listing (JSON).

| method | path                       | purpose                                 |
|--------|----------------------------|------------------------------------------|
| GET    | `/content`                 | read pad text; `X-Rev` header = revision |
| POST   | `/save`                    | replace pad text (body = new content)    |
| GET    | `/files/`                  | list files: `[{name, size, mtime}, ...]` |
| GET    | `/files/<name>`            | fetch file (supports `Range`)            |
| GET    | `/files/<name>?download=1` | same, forces `Content-Disposition: attachment` |
| PUT    | `/files/<name>`            | upload file (body = bytes)               |
| DELETE | `/files/<name>`            | delete file                              |
| POST   | `/upload-uri`              | server-side copy from local `file://` URIs (loopback only) |

No authentication. penpad assumes you're on a trusted network (LAN,
VPN, or tailnet).

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
