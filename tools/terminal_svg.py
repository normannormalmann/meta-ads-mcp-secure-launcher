"""Renders a wizard transcript as a terminal-window SVG (no dependencies)."""
from xml.sax.saxutils import escape

FONT_SIZE = 14
CHAR_WIDTH = 8.45
LINE_HEIGHT = 20
PADDING = 20
TITLE_BAR = 34
MIN_WIDTH = 760
FONT = "Cascadia Mono, Consolas, Menlo, 'DejaVu Sans Mono', monospace"

COLORS = {
    "background": "#0d1117",
    "frame": "#161b22",
    "border": "#30363d",
    "title": "#8b949e",
    "text": "#c9d1d9",
    "dim": "#484f58",
    "ok": "#3fb950",
    "warn": "#d29922",
    "error": "#f85149",
    "prompt": "#79c0ff",
    "answer": "#ffffff",
    "heading": "#d2a8ff",
    "json": "#a5d6ff",
}
STATUS_PREFIXES = (("[OK]", "ok"), ("[!]", "warn"), ("[x]", "error"))


def _style(text, heading_prefixes):
    stripped = text.strip()
    if stripped and set(stripped) <= {"=", "-"}:
        return "dim", False
    for prefix, color in STATUS_PREFIXES:
        if text.startswith(prefix):
            return color, False
    if text.startswith(heading_prefixes):
        return "heading", True
    if stripped.startswith(("{", "}", '"', "]", "[")):
        return "json", False
    return "text", False


def _tspan(text, color, bold=False):
    weight = ' font-weight="700"' if bold else ""
    return f'<tspan fill="{COLORS[color]}"{weight}>{escape(text)}</tspan>'


def render(transcript, title, heading_prefixes=("Step", "Schritt"), bold_first_line=True):
    rows = []
    for index, (kind, text, answer) in enumerate(transcript):
        if kind == "ask":
            *leading, last = text.split("\n")
            rows.extend((len(line), _tspan(line, "prompt")) for line in leading)
            rows.append((len(last) + len(answer or ""), _tspan(last, "prompt") + _tspan(answer or "", "answer", bold=True)))
            continue
        color, bold = _style(text, heading_prefixes)
        bold = bold or (bold_first_line and index == 0)
        rows.append((len(text), _tspan(text, "answer" if bold and color == "text" else color, bold)))

    columns = max((length for length, _ in rows), default=0)
    width = max(MIN_WIDTH, round(columns * CHAR_WIDTH + 2 * PADDING))
    height = TITLE_BAR + 2 * PADDING + len(rows) * LINE_HEIGHT
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        f'<rect width="{width}" height="{height}" rx="10" fill="{COLORS["background"]}" stroke="{COLORS["border"]}"/>',
        f'<path d="M0 10a10 10 0 0 1 10-10h{width - 20}a10 10 0 0 1 10 10v{TITLE_BAR - 10}H0z" fill="{COLORS["frame"]}"/>',
        *(f'<circle cx="{20 + i * 20}" cy="{TITLE_BAR / 2}" r="6" fill="{c}"/>' for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))),
        f'<text x="{width / 2}" y="{TITLE_BAR / 2 + 5}" text-anchor="middle" font-family="{FONT}" font-size="13" fill="{COLORS["title"]}">{escape(title)}</text>',
        f'<text font-family="{FONT}" font-size="{FONT_SIZE}" xml:space="preserve">',
    ]
    for row, (_, spans) in enumerate(rows):
        y = TITLE_BAR + PADDING + (row + 1) * LINE_HEIGHT - 5
        lines.append(f'<tspan x="{PADDING}" y="{y}">{spans}</tspan>')
    lines += ["</text>", "</svg>"]
    return "\n".join(lines) + "\n"
