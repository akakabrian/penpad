---
name: penpad
description: Interact with the user's penpad instance — a self-hosted shared text pad and file drop. Use when the user says "drop it in penpad", "put it on my pad", "add to penpad", "check penpad", "share to penpad", references a file they uploaded to penpad, asks you to write a reply into the pad, or wants to move something between their devices via penpad. Reads/writes `~/penpad/penpad.txt` and `~/penpad/files/` directly when running on the penpad host; otherwise uses the HTTP API (default `http://localhost:8767`, override with `$PENPAD_URL`).
---

# penpad

The user's penpad is a tiny self-hosted app that syncs a single text
file and a single folder across every device they own. Think "shared
Notes app, but it's just a file on their disk." Details:
<https://github.com/akakabrian/penpad>.

## What to do

When the user refers to penpad, don't invent APIs or guess locations.
Check both access paths:

1. **Filesystem**: `~/penpad/penpad.txt` (the pad) and `~/penpad/files/`
   (uploads). If these exist, you're on the penpad host — read/write
   them directly. Prefer this when it works; it's simpler and has no
   network hop.
2. **HTTP**: if filesystem access isn't available, the HTTP API is
   at `$PENPAD_URL` (default `http://localhost:8767`). See below.

## Common tasks

**Read the pad text:**
```bash
cat ~/penpad/penpad.txt
```
or, over HTTP:
```bash
curl -s "$PENPAD_URL/content"
```

**Append to the pad** (safer than overwriting — concurrent edits are
last-writer-wins):
```bash
echo "> agent: finished the thing" >> ~/penpad/penpad.txt
```
over HTTP, fetch the current text first, concatenate, POST back:
```bash
body="$(curl -s "$PENPAD_URL/content")"$'\n> agent: finished the thing\n'
curl -s -X POST "$PENPAD_URL/save" --data-binary "$body"
```

**List files:**
```bash
ls -la ~/penpad/files/
```
or:
```bash
curl -s "$PENPAD_URL/files/" | jq .
```

**Drop a file into penpad:**
```bash
cp /tmp/report.pdf ~/penpad/files/
```
or:
```bash
curl -X PUT "$PENPAD_URL/files/report.pdf" --data-binary @/tmp/report.pdf
```

**Fetch a file the user uploaded:**
```bash
cat ~/penpad/files/receipt.pdf
# or
curl -o /tmp/receipt.pdf "$PENPAD_URL/files/receipt.pdf?download=1"
```

## Conventions

- Keep the pad text **free-form**. Never rewrite the whole file unless
  you're explicitly asked to.
- When replying to the user in the pad, prefix your line with
  `> agent:` (or your own name). It makes the conversation readable
  when the user checks it from their phone.
- File names: stick to ASCII letters, digits, `._-`. The server
  sanitizes, but some characters get stripped.
- **Watch for changes** with `inotifywait` (local) or by polling
  `GET /content` and comparing the `X-Rev` response header.

## Do not

- Don't delete `penpad.txt`. Truncate it (`echo -n > penpad.txt`) if
  the user asks to clear the pad.
- Don't write giant files (>100 MB) unless the user specifically
  asks. Clients will try to preview them.
- Don't use penpad as a substitute for a proper chat app; keep
  back-and-forth short and purposeful.
