#!/usr/bin/env python3
"""penpad TUI — Textual-based client for the penpad server.

On the same host as the server:
    python tui.py

From another machine (over LAN, VPN, or Tailscale):
    PENPAD_URL=https://penpad.example.com python tui.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import platform
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Paste
from textual.reactive import reactive
from textual.widgets import Footer, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

PENPAD_URL = (
    os.environ.get("PENPAD_URL")
    or os.environ.get("NOTEPAD_URL")  # backward-compat
    or "http://127.0.0.1:8767"
)
__version__ = "0.2.0"
PENPAD_TOKEN = os.environ.get("PENPAD_TOKEN", "")
SAVE_DEBOUNCE_S = 0.4
POLL_INTERVAL_S = 2.0
NARROW_BREAKPOINT = 90
MAX_PREVIEW_BYTES = 64 * 1024
TEXT_EXT = {
    ".txt", ".md", ".log", ".json", ".jsonl", ".py", ".js", ".ts",
    ".css", ".html", ".sh", ".yaml", ".yml", ".toml", ".sql", ".csv",
    ".conf", ".ini", ".env", ".rs", ".go", ".rb",
}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def looks_like_path(text: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"(?<!\\)\s+", text.strip()):
        if not part:
            continue
        unescaped = re.sub(r"\\(.)", r"\1", part)
        if unescaped.startswith("file://") or unescaped.startswith("/"):
            out.append(unescaped)
    return out


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "K", "M", "G"):
        if f < 1024:
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}T"


def short_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%b %d").lower()


class PenpadTUI(App):
    CSS_PATH = "tui.tcss"
    TITLE = "penpad"

    BINDINGS = [
        Binding("c", "copy_url", "copy url"),
        Binding("d", "download", "download"),
        Binding("o", "open_downloads", "open downloads"),
        Binding("x", "delete", "delete from cloud"),
        Binding("ctrl+r", "reload", "reload"),
        Binding("slash", "filter", "filter"),
        Binding("question_mark", "help", "help"),
        Binding("f1", "help", "help", show=False),
        Binding("ctrl+q", "quit", "quit", priority=True),
    ]

    dirty: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self.files: list[dict] = []
        self.pending_upload: list[str] = []
        self.pending_delete: str | None = None
        self.selected_file: str | None = None
        self.client: httpx.AsyncClient | None = None
        self._save_timer = None
        self.current_rev: str | None = None
        self._remote_changed_notified = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="title-bar"):
            yield Static("penpad", id="title")
            yield Static("● synced", id="status", classes="-ok")
        with Horizontal(id="root"):
            with Vertical(id="pad-wrap"):
                yield TextArea(text="", id="pad", soft_wrap=True)
            with Vertical(id="files-wrap"):
                yield Static("files · 0", id="files-head")
                yield Input(placeholder="filter…", id="filter", classes="hidden")
                yield OptionList(id="files-list")
                yield Static("select a file", id="preview")
        yield Static("", id="toast")
        yield Footer()

    async def on_mount(self) -> None:
        headers = {"X-Penpad-Token": PENPAD_TOKEN} if PENPAD_TOKEN else None
        self.client = httpx.AsyncClient(base_url=PENPAD_URL, timeout=10.0,
                                        headers=headers)
        await self.load_content()
        await self.load_files()
        self._apply_narrow()
        self.query_one("#pad", TextArea).focus()
        self._poll_sync()

    async def on_unmount(self) -> None:
        if self.client:
            await self.client.aclose()

    def on_resize(self, event) -> None:
        self._apply_narrow()

    def _apply_narrow(self) -> None:
        narrow = self.size.width < NARROW_BREAKPOINT
        screen = self.screen
        if narrow:
            screen.add_class("narrow")
        else:
            screen.remove_class("narrow")

    @work(exclusive=True, group="sync")
    async def _poll_sync(self) -> None:
        while True:
            await asyncio.sleep(POLL_INTERVAL_S)
            if self.client is None:
                continue
            try:
                r = await self.client.get("/content")
                r.raise_for_status()
                rev = r.headers.get("X-Rev")
                if rev and rev != self.current_rev:
                    if not self.dirty:
                        self.current_rev = rev
                        pad = self.query_one("#pad", TextArea)
                        pad.text = r.text
                        self._move_cursor_end(pad)
                    elif not self._remote_changed_notified:
                        self._remote_changed_notified = True
                        self.notify(
                            "remote changed — your edits will win on save",
                            severity="warning",
                        )
                fr = await self.client.get("/files/")
                fr.raise_for_status()
                new_files = sorted(
                    fr.json(), key=lambda f: f.get("mtime", 0), reverse=True
                )
                if new_files != self.files:
                    self.files = new_files
                    self._render_files(self._current_filter())
            except Exception:
                pass

    # ---------- data ----------

    def _move_cursor_end(self, pad: TextArea) -> None:
        lines = pad.text.split("\n")
        last_line = max(0, len(lines) - 1)
        last_col = len(lines[-1]) if lines else 0
        try:
            pad.move_cursor((last_line, last_col))
            pad.scroll_cursor_visible()
        except Exception:
            pass

    async def load_content(self) -> None:
        try:
            r = await self.client.get("/content")
            r.raise_for_status()
            pad = self.query_one("#pad", TextArea)
            pad.text = r.text
            self.current_rev = r.headers.get("X-Rev")
            self._remote_changed_notified = False
            self.dirty = False
            self.query_one("#title", Static).update("penpad")
            self._set_status("synced", "-ok")
            self._move_cursor_end(pad)
        except Exception as e:
            self._set_status("error", "-err")
            self.notify(f"load failed: {e}", severity="error")

    async def load_files(self) -> None:
        try:
            r = await self.client.get("/files/")
            r.raise_for_status()
            self.files = sorted(
                r.json(), key=lambda f: f.get("mtime", 0), reverse=True
            )
            self._render_files(self._current_filter())
        except Exception as e:
            self.notify(f"files load: {e}", severity="error")

    def _current_filter(self) -> str:
        try:
            return self.query_one("#filter", Input).value
        except Exception:
            return ""

    def _render_files(self, filter_text: str = "") -> None:
        lst = self.query_one("#files-list", OptionList)
        lst.clear_options()
        visible = [
            f for f in self.files
            if not filter_text or filter_text.lower() in f["name"].lower()
        ]
        self.query_one("#files-head", Static).update(f"files · {len(visible)}")
        for f in visible:
            name = f["name"]
            size = human_size(f["size"])
            date = short_date(f.get("mtime", 0))
            label = f"◦ {name[:26]:<26} {size:>6}  {date}"
            lst.add_option(Option(label, id=name))

    def _set_status(self, text: str, cls: str) -> None:
        s = self.query_one("#status", Static)
        s.update(f"● {text}")
        s.set_classes(f"-{cls.lstrip('-')}")

    # ---------- actions ----------

    @work(exclusive=True, group="save")
    async def action_save(self) -> None:
        pad = self.query_one("#pad", TextArea)
        self._set_status("saving", "warn")
        try:
            r = await self.client.post("/save", content=pad.text.encode())
            r.raise_for_status()
            self.current_rev = r.headers.get("X-Rev", self.current_rev)
            self._remote_changed_notified = False
            self.dirty = False
            self.query_one("#title", Static).update("penpad")
            self._set_status("synced", "ok")
        except Exception as e:
            self._set_status("error", "err")
            self.notify(f"save failed: {e}", severity="error")

    def action_filter(self) -> None:
        inp = self.query_one("#filter", Input)
        inp.remove_class("hidden")
        inp.focus()

    async def action_reload(self) -> None:
        await self.load_content()
        await self.load_files()

    def action_help(self) -> None:
        self._show_toast(
            "[b]penpad[/b]   autosaves   tab focus   / filter   "
            "c copy url   d download   x delete   o open downloads   "
            "^r reload   ?  help   ^q quit"
        )

    def _download_path(self, name: str) -> Path:
        return Path.home() / "Downloads" / name

    def _file_url(self, name: str) -> str:
        base = PENPAD_URL.rstrip("/")
        return f"{base}/files/{quote(name, safe='')}"

    def action_copy_url(self) -> None:
        if not self.selected_file:
            self.notify("no file selected", severity="warning")
            return
        url = self._file_url(self.selected_file)
        self.copy_to_clipboard(url)
        self.notify(f"url copied: {url}")

    @work(exclusive=True, group="download")
    async def action_download(self) -> None:
        if not self.selected_file:
            self.notify("no file selected", severity="warning")
            return
        name = self.selected_file
        dest = self._download_path(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._set_status("downloading", "warn")
        try:
            async with self.client.stream(
                "GET", f"/files/{quote(name, safe='')}"
            ) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(64 * 1024):
                        f.write(chunk)
            self._set_status("synced", "ok")
            self.notify(f"downloaded → {dest}")
        except Exception as e:
            self._set_status("error", "err")
            self.notify(f"download failed: {e}", severity="error")

    def action_open_downloads(self) -> None:
        path = Path.home() / "Downloads"
        path.mkdir(parents=True, exist_ok=True)
        cmd = ["open" if platform.system() == "Darwin" else "xdg-open", str(path)]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.notify(f"no file-explorer opener for {path}",
                        severity="warning")

    def action_delete(self) -> None:
        if not self.selected_file:
            self.notify("no file selected", severity="warning")
            return
        self.pending_delete = self.selected_file
        self._show_toast(
            f"◦ delete [#c97064]{self.selected_file}[/] from cloud?   "
            f"[dim](local copies in Downloads are untouched)[/]   "
            f"[b][#f5c842]↵ yes[/][/b]   [dim]esc no[/]"
        )

    @work(exclusive=True, group="delete")
    async def _run_delete(self) -> None:
        name = self.pending_delete
        self.pending_delete = None
        self._hide_toast()
        if not name:
            return
        try:
            r = await self.client.delete(f"/files/{quote(name, safe='')}")
            r.raise_for_status()
            self.notify(f"deleted {name} from cloud")
            self.selected_file = None
            self.query_one("#preview", Static).update("select a file")
            await self.load_files()
        except Exception as e:
            self.notify(f"delete failed: {e}", severity="error")

    # ---------- events ----------

    @on(TextArea.Changed, "#pad")
    def _pad_changed(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.query_one("#title", Static).update("penpad ●")
        if self._save_timer:
            self._save_timer.stop()
        self._save_timer = self.set_timer(SAVE_DEBOUNCE_S, self.action_save)

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._render_files(event.value)

    @on(Input.Submitted, "#filter")
    def _filter_submit(self) -> None:
        self.query_one("#files-list", OptionList).focus()

    @on(OptionList.OptionSelected, "#files-list")
    async def _file_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            await self._show_preview(event.option.id)

    @on(OptionList.OptionHighlighted, "#files-list")
    async def _file_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option and event.option.id:
            self.selected_file = event.option.id
            await self._show_preview(event.option.id)

    @on(Paste)
    def _on_paste(self, event: Paste) -> None:
        # If the user is typing in the pad, let pastes through verbatim.
        # Upload intercept only fires when focus is elsewhere (files list,
        # filter input, etc.) — so you can legitimately paste a path as text.
        pad = self.query_one("#pad", TextArea)
        if pad.has_focus:
            return
        paths = looks_like_path(event.text)
        if not paths:
            return
        event.stop()
        self._prompt_upload(paths)

    def on_key(self, event) -> None:
        if self.pending_upload:
            if event.key == "enter":
                event.stop()
                self._run_upload()
                return
            if event.key == "escape":
                event.stop()
                self.pending_upload = []
                self._hide_toast()
                return
        if self.pending_delete:
            if event.key == "enter":
                event.stop()
                self._run_delete()
                return
            if event.key == "escape":
                event.stop()
                self.pending_delete = None
                self._hide_toast()
                return
        inp = self.query_one("#filter", Input)
        if event.key == "escape" and (inp.has_focus or not inp.has_class("hidden")):
            inp.value = ""
            inp.add_class("hidden")
            self._render_files()
            self.query_one("#files-list", OptionList).focus()
            event.stop()

    # ---------- upload flow ----------

    def _prompt_upload(self, paths: list[str]) -> None:
        valid, missing = [], []
        for p in paths:
            src = p[len("file://"):] if p.startswith("file://") else p
            if Path(src).exists() and Path(src).is_file():
                valid.append(src)
            else:
                missing.append(src)
        if not valid:
            name = Path(missing[0]).name if missing else "?"
            self._show_toast(
                f"[#c97064]not found locally:[/] {name}   "
                f"[dim]path must exist on the machine running the TUI[/]"
            )
            return
        self.pending_upload = valid
        names = ", ".join(Path(p).name for p in valid[:3])
        if len(valid) > 3:
            names += f" +{len(valid) - 3}"
        self._show_toast(
            f"◦ upload [#d4a373]{names}[/]?   "
            f"[b][#f5c842]↵ yes[/][/b]   [dim]esc no[/]"
        )

    @work(exclusive=True, group="upload")
    async def _run_upload(self) -> None:
        paths = self.pending_upload
        self.pending_upload = []
        self._hide_toast()

        # Batch progress: track bytes across the whole upload.
        sizes = []
        for p in paths:
            try:
                sizes.append(Path(p).stat().st_size)
            except OSError:
                sizes.append(0)
        total_bytes = sum(sizes) or 1
        sent_before = 0

        ok, fail = 0, 0
        for p, size in zip(paths, sizes):
            src = Path(p)
            try:
                name = quote(src.name, safe="")
                chunk_size = 64 * 1024
                # Local snapshot so the generator captures *this* file's offset.
                offset = sent_before

                async def chunks():
                    sent = 0
                    with src.open("rb") as f:
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            sent += len(chunk)
                            yield chunk
                            pct = min(
                                100,
                                int(100 * (offset + sent) / total_bytes),
                            )
                            self._set_status(f"uploading {pct}%", "warn")

                r = await self.client.put(
                    f"/files/{name}",
                    content=chunks(),
                    headers={"Content-Length": str(size)},
                )
                r.raise_for_status()
                sent_before += size
                ok += 1
            except Exception:
                fail += 1
        await self.load_files()
        if fail == 0:
            self._set_status("synced", "ok")
            self.notify(f"uploaded {ok}")
        else:
            self._set_status("partial", "warn")
            self.notify(f"uploaded {ok} · {fail} failed", severity="warning")

    # ---------- toast ----------

    def _show_toast(self, markup: str) -> None:
        t = self.query_one("#toast", Static)
        t.update(markup)
        t.add_class("-show")

    def _hide_toast(self) -> None:
        self.query_one("#toast", Static).remove_class("-show")

    # ---------- preview ----------

    async def _show_preview(self, name: str) -> None:
        pv = self.query_one("#preview", Static)
        meta = next((f for f in self.files if f["name"] == name), None)
        if not meta:
            pv.update("")
            return
        ext = Path(name).suffix.lower()
        size = human_size(meta["size"])
        date = short_date(meta.get("mtime", 0))
        header = f"[b]{name}[/]\n[#4d4c47]{size} · {date}[/]\n\n"
        if ext in TEXT_EXT:
            try:
                file_size = meta["size"]
                if file_size <= MAX_PREVIEW_BYTES:
                    r = await self.client.get(f"/files/{quote(name, safe='')}")
                else:
                    r = await self.client.get(
                        f"/files/{quote(name, safe='')}",
                        headers={"Range": f"bytes=0-{MAX_PREVIEW_BYTES - 1}"},
                    )
                r.raise_for_status()
                body = r.text.replace("[", r"\[")
                pv.update(header + body[:4000])
            except Exception as e:
                pv.update(header + f"[#c97064]{e}[/]")
        elif ext in IMG_EXT:
            pv.update(header + "[#7a7872]image[/]")
        else:
            pv.update(header + f"[#7a7872]{ext[1:] if ext else 'binary'}[/]")


if __name__ == "__main__":
    PenpadTUI().run()
