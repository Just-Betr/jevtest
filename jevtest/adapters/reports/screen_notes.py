"""Beside a screenshot: what was on the screen as steps name it, for a person (.txt, .html) and for an agent (.json).

The .html draws a box around each element on the screenshot: pointing at one shows its names, and clicking it copies
a step that finds it.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from jevtest.domain.inspection import SHARED, ElementNotes, ScreenNotes

ABOUT = (
    "Each element on the screen in reading order, with every name a step can find it by (tap:, see:, into:). "
    "find_by is the name to use: one only this element has where there is one. bounds are [left, top, right, "
    "bottom] in the screen's units (width by height)."
)


class ScreenNotesFiles:
    """Writes `ScreenNotes` as .txt, .json and .html files beside the screenshot."""

    def write(self, screenshot: Path, notes: ScreenNotes) -> None:
        """Save the three files: the screenshot's name with .txt, .json and .html."""
        screenshot.with_suffix(".txt").write_text(notes.text(), encoding="utf-8")
        screenshot.with_suffix(".json").write_text(
            json.dumps(as_json(screenshot.name, notes), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        screenshot.with_suffix(".html").write_text(as_html(screenshot.name, notes), encoding="utf-8")


def as_json(picture: str, notes: ScreenNotes) -> dict[str, object]:
    """The notes as JSON: for an agent writing or fixing a test."""
    return {
        "about": ABOUT,
        "shared": SHARED,
        "screenshot": picture,
        "screen": {"width": notes.width, "height": notes.height, "keyboard_visible": notes.keyboard_visible},
        "elements": [
            {
                "kind": el.kind,
                "find_by": el.find_by,
                "names": list(el.names),
                "shared": el.shared,
                "bounds": list(el.bounds),
                "state": list(el.state),
            }
            for el in notes.elements
        ],
    }


def step_for(el: ElementNotes) -> str:
    """A step that finds the element, on one line to paste into a list; "" for an element with no name.

    Typing into a field, and a tap on anything else.
    """
    if not el.find_by:
        return ""
    name = el.find_by.replace("'", "''")  # YAML's single-quoted string
    return f"type: {{ text: '...', into: '{name}' }}" if "editable" in el.state else f"tap: '{name}'"


def as_html(picture: str, notes: ScreenNotes) -> str:
    """A page with the screenshot, a box around each element, and the list beside it."""
    w, h = max(notes.width, 1), max(notes.height, 1)
    boxes: list[str] = []
    rows: list[str] = []
    for i, el in enumerate(notes.elements):
        x1, y1, x2, y2 = el.bounds
        style = (
            f"left:{100 * x1 / w:.3f}%;top:{100 * y1 / h:.3f}%;"
            f"width:{100 * max(x2 - x1, 0) / w:.3f}%;height:{100 * max(y2 - y1, 0) / h:.3f}%"
        )
        step = html.escape(step_for(el))
        boxes.append(f'<div class="box" data-i="{i}" data-step="{step}" style="{style}"></div>')
        names = " | ".join(
            html.escape(f"'{n}'") + (f" <em>({el.shared[n]} on screen)</em>" if n in el.shared else "")
            for n in el.names
        )
        state = f' <span class="state">{html.escape(", ".join(el.state))}</span>' if el.state else ""
        rows.append(
            f'<li data-i="{i}" data-step="{step}"><code>{html.escape(el.kind)}</code> '
            f"{names or '<em>(no name)</em>'}{state}</li>"
        )
    return PAGE.format(
        title=html.escape(picture),
        picture=html.escape(picture),
        boxes="\n".join(boxes),
        rows="\n".join(rows),
        shared=html.escape(SHARED),
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}: what steps can name</title>
<style>
:root {{ color-scheme: light dark; --box: #ff2d55; --hot: #ffcc00; --bg: #fff; --fg: #111; --row: #f2f2f2; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #111; --fg: #eee; --row: #222; }} }}
body {{ margin: 0; padding: 16px; font: 14px/1.4 system-ui, sans-serif; background: var(--bg); color: var(--fg); }}
.wrap {{ display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap; }}
.shot {{ position: relative; flex: 0 0 auto; width: min(400px, 100%); }}
.shot img {{ display: block; width: 100%; height: auto; }}
.box {{ position: absolute; box-sizing: border-box; border: 1px solid var(--box); cursor: pointer; }}
.box.hot {{ border: 3px solid var(--hot); background: rgba(255, 204, 0, .2); z-index: 1; }}
.list {{ flex: 1 1 320px; min-width: 0; }}
ol {{ margin: 0; padding-left: 28px; }}
li {{ padding: 4px 6px; border-radius: 4px; cursor: pointer; overflow-wrap: anywhere; }}
li.hot {{ background: var(--row); outline: 2px solid var(--hot); }}
code {{ opacity: .7; }}
.state {{ font-size: 12px; opacity: .7; }}
#copied {{ position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); padding: 8px 14px;
  border-radius: 6px; background: var(--fg); color: var(--bg); opacity: 0; transition: opacity .2s;
  white-space: pre; font-family: ui-monospace, monospace; }}
#copied.on {{ opacity: 1; }}
</style>
</head>
<body>
<p>Point at an element to see its names; click it to copy a step that finds it. Any one name works.
<em>(N on screen)</em>: {shared}.</p>
<div class="wrap">
<div class="shot"><img src="{picture}" alt="screenshot">
{boxes}
</div>
<div class="list"><ol start="0">
{rows}
</ol></div>
</div>
<div id="copied"></div>
<script>
const all = i => document.querySelectorAll('[data-i="' + i + '"]');
let hot = null;
function light(i) {{
  if (hot !== null) all(hot).forEach(e => e.classList.remove("hot"));
  hot = i;
  if (i !== null) all(i).forEach(e => e.classList.add("hot"));
}}
document.querySelectorAll("[data-i]").forEach(e => {{
  e.addEventListener("mouseenter", () => {{
    light(e.dataset.i);
    const row = document.querySelector('li[data-i="' + e.dataset.i + '"]');
    if (e.tagName === "DIV") row.scrollIntoView({{block: "nearest"}});
  }});
  e.addEventListener("mouseleave", () => light(null));
  e.addEventListener("click", () => {{
    const step = e.dataset.step;
    if (!step) return;
    const shown = document.getElementById("copied");
    const say = text => {{ shown.textContent = text; shown.classList.add("on");
      setTimeout(() => shown.classList.remove("on"), 1500); }};
    (navigator.clipboard ? navigator.clipboard.writeText(step) : Promise.reject())
      .then(() => say("copied:\\n" + step), () => say(step));
  }});
}});
</script>
</body>
</html>
"""
