#!/usr/bin/env python3
"""Render TUI screenshots as SVG for the README.

Run with the venv python from the repo root:
    .venv/bin/python scripts/capture_tui.py

The penpad server should be running on http://127.0.0.1:8767 with
demo content in penpad.txt + files/ — capture is taken from a real
session, not mocked state.
"""
import asyncio
import os
import sys
from pathlib import Path

# Run the TUI from the repo root so it can find tui.tcss
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from tui import PenpadTUI

DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)


async def main() -> None:
    app = PenpadTUI()
    async with app.run_test(size=(120, 36)) as pilot:
        # Wait for initial load (HTTP fetches for /content + /files/).
        await pilot.pause(1.5)
        app.save_screenshot(filename="tui-pad.svg", path=str(DOCS))

        # Move focus to the files list so something is highlighted.
        await pilot.press("tab")
        await pilot.pause(0.3)
        # Highlight a few items down so the preview pane shows real content.
        for _ in range(3):
            await pilot.press("down")
            await pilot.pause(0.1)
        await pilot.pause(0.6)  # let the preview HTTP fetch complete
        app.save_screenshot(filename="tui-files.svg", path=str(DOCS))

    print(f"wrote {DOCS / 'tui-pad.svg'}")
    print(f"wrote {DOCS / 'tui-files.svg'}")


if __name__ == "__main__":
    asyncio.run(main())
