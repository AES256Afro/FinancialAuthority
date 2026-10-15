"""The ten report layouts and the mascots. A template is three free choices: a layout (type, colour, chart and
table style), a palette (the layout's own, the app's checked palette, or ink only), and a mascot set. The mascots
are drawn once, as shapes, so the same creature comes out as SVG in HTML, vector shapes in PDF and a PNG in Word
and Excel. They are our own drawings: kitties, a round forest spirit, dust sprites and acorns; never people."""

from __future__ import annotations

import io
from typing import Any

# ---- layouts --------------------------------------------------------------------------------------------------

SANS = "'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"
SERIF = "Georgia, 'Times New Roman', 'Libre Baskerville', serif"
MONO = "'JetBrains Mono', 'Cascadia Mono', Consolas, 'Courier New', monospace"
ROUND = "'Nunito', 'Segoe UI Rounded', 'Arial Rounded MT Bold', 'Segoe UI', system-ui, sans-serif"
HAND = "'Patrick Hand', 'Comic Neue', 'Segoe Print', 'Segoe UI', cursive"

APP_LIGHT = {"housing": "#2a78d6", "bills": "#eb6834", "card": "#4a3aa7", "later": "#e87ba4", "aside": "#c3c2b7", "saving": "#eda100", "yours": "#1baf7a"}
APP_DARK = {"housing": "#3987e5", "bills": "#d95926", "card": "#9085e9", "later": "#d55181", "aside": "#4a505b", "saving": "#c98500", "yours": "#199e70"}

LAYOUTS: dict[str, dict[str, Any]] = {
    "ledger": {"name": "Ledger", "family": "Professional", "description": "Tables first, a serif face, ruled like a ledger book.", "dark": False,
               "fonts": {"display": SERIF, "body": SERIF, "mono": MONO}, "pdf": {"display": "Times", "body": "Times"},
               "colours": {"bg": "#fffdf8", "card": "#fffdf8", "ink": "#1a1a1a", "muted": "#5b5b5b", "accent": "#1a1a1a", "rule": "#1a1a1a", "head_bg": "#fffdf8", "head_ink": "#1a1a1a", "track": "#ece8dc"},
               "parts": {"housing": "#1a1a1a", "bills": "#4d4d4d", "card": "#6f6f6f", "later": "#8f8f8f", "aside": "#c8c8c8", "saving": "#3a3a3a", "yours": "#a8a8a8"}, "charts": True, "mascot": "none"},
    "board": {"name": "Boardroom", "family": "Professional", "description": "Figure tiles across the top, clean charts, the app's checked palette.", "dark": False,
              "fonts": {"display": SANS, "body": SANS, "mono": MONO}, "pdf": {"display": "Helvetica", "body": "Helvetica"},
              "colours": {"bg": "#ffffff", "card": "#f6f8fb", "ink": "#14243a", "muted": "#5a6b80", "accent": "#2a78d6", "rule": "#d7dee8", "head_bg": "#163a66", "head_ink": "#ffffff", "track": "#e8edf3"},
              "parts": APP_LIGHT, "charts": True, "mascot": "none"},
    "dossier": {"name": "Dossier", "family": "Engineered", "description": "Dense, referenced, no charts: every row and its source.", "dark": False,
                "fonts": {"display": SANS, "body": SANS, "mono": MONO}, "pdf": {"display": "Helvetica", "body": "Helvetica"},
                "colours": {"bg": "#ffffff", "card": "#ffffff", "ink": "#111111", "muted": "#555555", "accent": "#111111", "rule": "#999999", "head_bg": "#ffffff", "head_ink": "#111111", "track": "#eeeeee"},
                "parts": APP_LIGHT, "charts": False, "mascot": "none"},
    "blueprint": {"name": "Blueprint", "family": "Engineered", "description": "Grid paper and a monospace face, drawn like a plan.", "dark": True,
                  "fonts": {"display": MONO, "body": MONO, "mono": MONO}, "pdf": {"display": "Courier", "body": "Courier"},
                  "colours": {"bg": "#0f2b4c", "card": "#12345b", "ink": "#dbe7f5", "muted": "#9fc3ea", "accent": "#9fc3ea", "rule": "#5d84ad", "head_bg": "#0b223d", "head_ink": "#dbe7f5", "track": "#1b416c"},
                  "parts": APP_DARK, "charts": True, "mascot": "none", "decor": "grid"},
    "terminal": {"name": "Terminal", "family": "Retro", "description": "Amber phosphor on black, bars drawn in blocks.", "dark": True,
                 "fonts": {"display": MONO, "body": MONO, "mono": MONO}, "pdf": {"display": "Courier", "body": "Courier"},
                 "colours": {"bg": "#0b0f0a", "card": "#0b0f0a", "ink": "#f2b134", "muted": "#b5862a", "accent": "#f2b134", "rule": "#7a5a1a", "head_bg": "#0b0f0a", "head_ink": "#f2b134", "track": "#2a2108"},
                 "parts": {"housing": "#f2b134", "bills": "#d9982a", "card": "#bf8022", "later": "#a66a1b", "aside": "#5e4410", "saving": "#ffcf6e", "yours": "#ffe0a0"}, "charts": True, "mascot": "none", "decor": "scan"},
    "receipt": {"name": "Receipt", "family": "Retro", "description": "Thermal-paper narrow column, no charts, a torn edge.", "dark": False,
                "fonts": {"display": MONO, "body": MONO, "mono": MONO}, "pdf": {"display": "Courier", "body": "Courier"},
                "colours": {"bg": "#fbfaf3", "card": "#fbfaf3", "ink": "#222222", "muted": "#666666", "accent": "#222222", "rule": "#777777", "head_bg": "#fbfaf3", "head_ink": "#222222", "track": "#e9e6d8"},
                "parts": APP_LIGHT, "charts": False, "mascot": "none", "decor": "receipt"},
    "horizon": {"name": "Horizon", "family": "Modern retro", "description": "Neon on a violet dusk with a grid to the horizon.", "dark": True,
                "fonts": {"display": SANS, "body": SANS, "mono": MONO}, "pdf": {"display": "Helvetica", "body": "Helvetica"},
                "colours": {"bg": "#1d0b3a", "card": "#2a1050", "ink": "#f3e9ff", "muted": "#c2a8ec", "accent": "#ff5fd2", "rule": "#8fe9ff", "head_bg": "#160830", "head_ink": "#ff5fd2", "track": "#3a1b66"},
                "parts": {"housing": "#8fe9ff", "bills": "#ff5fd2", "card": "#b388ff", "later": "#ff9e64", "aside": "#6b5a8e", "saving": "#ffd166", "yours": "#06d6a0"}, "charts": True, "mascot": "none", "decor": "horizon"},
    "forest": {"name": "Forest", "family": "Cute", "description": "Moss and leaf greens, a round forest spirit and dust sprites.", "dark": False,
               "fonts": {"display": HAND, "body": ROUND, "mono": MONO}, "pdf": {"display": "Helvetica", "body": "Helvetica"},
               "colours": {"bg": "#eef3e4", "card": "#ffffff", "ink": "#2f3b2d", "muted": "#5f6f59", "accent": "#3f6b3a", "rule": "#cddabf", "head_bg": "#dfe9cf", "head_ink": "#2f4a2b", "track": "#e2ead6"},
               "parts": {"housing": "#3f6b3a", "bills": "#d9a066", "card": "#7d6aa8", "later": "#e07a5f", "aside": "#cfd8c0", "saving": "#c9a227", "yours": "#7fb069"}, "charts": True, "mascot": "forest"},
    "kitties": {"name": "Black Kitties", "family": "Cute", "description": "Two black kitties, warm cream paper, paw-print bullets.", "dark": False,
                "fonts": {"display": HAND, "body": ROUND, "mono": MONO}, "pdf": {"display": "Helvetica", "body": "Helvetica"},
                "colours": {"bg": "#fff6ea", "card": "#fffdf9", "ink": "#2a2422", "muted": "#6b5e57", "accent": "#2a2422", "rule": "#e9d6c2", "head_bg": "#f7e6d2", "head_ink": "#2a2422", "track": "#f3e5d4"},
                "parts": {"housing": "#2a2422", "bills": "#e3a0a8", "card": "#9bbcd0", "later": "#d98b5f", "aside": "#d8cfc4", "saving": "#f0c95c", "yours": "#7bb08a"}, "charts": True, "mascot": "kitties"},
    "meadow": {"name": "Meadow", "family": "Cute", "description": "A watercolour sky, acorns and sprites, soft rounded type.", "dark": False,
               "fonts": {"display": HAND, "body": ROUND, "mono": MONO}, "pdf": {"display": "Helvetica", "body": "Helvetica"},
               "colours": {"bg": "#f4f9ff", "card": "#ffffff", "ink": "#3b3a33", "muted": "#6b6957", "accent": "#5a4a2e", "rule": "#d8e3cf", "head_bg": "#dcefff", "head_ink": "#5a4a2e", "track": "#eaf1e0"},
               "parts": {"housing": "#81b29a", "bills": "#e07a5f", "card": "#8e7cc3", "later": "#f2a65a", "aside": "#d8e3cf", "saving": "#f2cc8f", "yours": "#a7c957"}, "charts": True, "mascot": "acorns"},
}

PALETTES = {"layout": "The layout's own colours", "app": "The app's checked palette", "ink": "Ink only, for printing"}

# ---- mascots, as shapes in their own box ---------------------------------------------------------------------

INK = "#1d1b1b"
PINK = "#e3a0a8"
GOLD = "#f0c95c"

KITTY = {"box": (100, 110), "shapes": [
    ("line", [(74, 86), (86, 78), (88, 66), (80, 56)], INK, 7),
    ("ellipse", (50, 82, 27, 20), INK),
    ("poly", [(28, 36), (34, 8), (49, 30)], INK), ("poly", [(72, 36), (66, 8), (51, 30)], INK),
    ("poly", [(34, 31), (36, 17), (43, 28)], PINK), ("poly", [(66, 31), (64, 17), (57, 28)], PINK),
    ("ellipse", (50, 48, 25, 25), INK),
    ("ellipse", (40, 48, 7, 8), GOLD), ("ellipse", (60, 48, 7, 8), GOLD),
    ("ellipse", (40.5, 49, 3, 5.5), INK), ("ellipse", (60.5, 49, 3, 5.5), INK),
    ("ellipse", (42.5, 45, 1.8, 1.8), "#ffffff"), ("ellipse", (62.5, 45, 1.8, 1.8), "#ffffff"),
    ("poly", [(47, 57), (53, 57), (50, 61)], PINK),
    ("line", [(22, 56), (36, 58)], "#9a9790", 1.2), ("line", [(22, 62), (36, 61)], "#9a9790", 1.2),
    ("line", [(78, 56), (64, 58)], "#9a9790", 1.2), ("line", [(78, 62), (64, 61)], "#9a9790", 1.2),
    ("ellipse", (38, 100, 8, 4), INK), ("ellipse", (62, 100, 8, 4), INK),
]}
SPIRIT = {"box": (120, 130), "shapes": [
    ("ellipse", (40, 26, 7, 20), "#7f8f88"), ("ellipse", (80, 26, 7, 20), "#7f8f88"),
    ("ellipse", (60, 78, 46, 50), "#7f8f88"),
    ("ellipse", (60, 92, 32, 30), "#ece9d8"),
    ("line", [(44, 84), (50, 79), (56, 84)], "#9aa59e", 2.2), ("line", [(60, 84), (66, 79), (72, 84)], "#9aa59e", 2.2),
    ("line", [(38, 98), (44, 93), (50, 98)], "#9aa59e", 2.2), ("line", [(54, 98), (60, 93), (66, 98)], "#9aa59e", 2.2), ("line", [(70, 98), (76, 93), (82, 98)], "#9aa59e", 2.2),
    ("ellipse", (44, 58, 6, 6), "#ffffff"), ("ellipse", (76, 58, 6, 6), "#ffffff"),
    ("ellipse", (45, 58, 3, 3), INK), ("ellipse", (77, 58, 3, 3), INK),
    ("poly", [(56, 66), (64, 66), (60, 71)], "#2f2f2f"),
    ("line", [(20, 62), (40, 65)], "#5f6a65", 1.5), ("line", [(20, 70), (40, 69)], "#5f6a65", 1.5),
    ("line", [(100, 62), (80, 65)], "#5f6a65", 1.5), ("line", [(100, 70), (80, 69)], "#5f6a65", 1.5),
    ("poly", [(60, 30), (68, 14), (84, 8), (96, 20), (84, 22), (72, 27), (63, 36)], "#6aa868"),
]}
SOOT = {"box": (40, 40), "shapes": [
    ("line", [(20, 4), (20, 9)], INK, 2), ("line", [(20, 36), (20, 31)], INK, 2), ("line", [(4, 20), (9, 20)], INK, 2), ("line", [(36, 20), (31, 20)], INK, 2),
    ("line", [(8, 8), (12, 12)], INK, 2), ("line", [(32, 8), (28, 12)], INK, 2), ("line", [(8, 32), (12, 28)], INK, 2), ("line", [(32, 32), (28, 28)], INK, 2),
    ("ellipse", (20, 20, 12, 12), INK),
    ("ellipse", (16, 19, 3.2, 3.2), "#ffffff"), ("ellipse", (24, 19, 3.2, 3.2), "#ffffff"),
    ("ellipse", (16.5, 19.5, 1.4, 1.4), INK), ("ellipse", (24.5, 19.5, 1.4, 1.4), INK),
]}
ACORN = {"box": (40, 50), "shapes": [
    ("ellipse", (20, 30, 11, 15), "#b97a3c"),
    ("poly", [(6, 22), (12, 15), (20, 12), (28, 15), (34, 22), (20, 26)], "#6e4a26"),
    ("poly", [(18.5, 6), (21.5, 6), (21.5, 14), (18.5, 14)], "#6e4a26"),
]}

# A set is a list of (figure, size in points, mirrored).
MASCOTS: dict[str, dict[str, Any]] = {
    "none": {"name": "None", "figures": []},
    "kitties": {"name": "Black kitties", "figures": [(KITTY, 54, False), (KITTY, 42, True)]},
    "forest": {"name": "Forest spirit and sprites", "figures": [(SPIRIT, 56, False), (SOOT, 20, False), (SOOT, 15, False)]},
    "sprites": {"name": "Dust sprites", "figures": [(SOOT, 22, False), (SOOT, 17, False), (SOOT, 13, False)]},
    "acorns": {"name": "Acorns", "figures": [(ACORN, 24, False), (ACORN, 18, False), (SOOT, 16, False)]},
}


def _flip(points: list[tuple[float, float]], w: float, mirror: bool) -> list[tuple[float, float]]:
    return [(w - x, y) if mirror else (x, y) for x, y in points]


def svg(fig: dict, size: float, mirror: bool = False) -> str:
    w, h = fig["box"]
    parts = []
    for shape in fig["shapes"]:
        kind = shape[0]
        if kind == "ellipse":
            cx, cy, rx, ry = shape[1]
            cx = w - cx if mirror else cx
            parts.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{shape[2]}"/>')
        elif kind == "poly":
            pts = " ".join(f"{x},{y}" for x, y in _flip(shape[1], w, mirror))
            parts.append(f'<polygon points="{pts}" fill="{shape[2]}"/>')
        elif kind == "line":
            pts = " ".join(f"{x},{y}" for x, y in _flip(shape[1], w, mirror))
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{shape[2]}" stroke-width="{shape[3]}" stroke-linecap="round" stroke-linejoin="round"/>')
    height = size * h / w
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{size:.0f}" height="{height:.0f}" aria-hidden="true">{"".join(parts)}</svg>'


def set_svg(mascot: str) -> str:
    return "".join(svg(f, s, m) for f, s, m in MASCOTS.get(mascot, MASCOTS["none"])["figures"])


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def draw_pdf(pdf, fig: dict, x: float, y: float, size: float, mirror: bool = False) -> None:
    """Draw a mascot with fpdf2 shapes, `size` wide, its top-left at (x, y)."""
    w, h = fig["box"]
    k = size / w
    for shape in fig["shapes"]:
        kind = shape[0]
        if kind == "ellipse":
            cx, cy, rx, ry = shape[1]
            cx = w - cx if mirror else cx
            pdf.set_fill_color(*_rgb(shape[2]))
            pdf.ellipse(x + (cx - rx) * k, y + (cy - ry) * k, 2 * rx * k, 2 * ry * k, style="F")
        elif kind == "poly":
            pdf.set_fill_color(*_rgb(shape[2]))
            pdf.polygon([(x + px * k, y + py * k) for px, py in _flip(shape[1], w, mirror)], style="F")
        elif kind == "line":
            pdf.set_draw_color(*_rgb(shape[2]))
            pdf.set_line_width(max(0.2, shape[3] * k))
            pts = [(x + px * k, y + py * k) for px, py in _flip(shape[1], w, mirror)]
            for a, b in zip(pts, pts[1:]):
                pdf.line(a[0], a[1], b[0], b[1])
    pdf.set_line_width(0.2)


def png(mascot: str, height: int = 120, background: str | None = None) -> bytes | None:
    """The mascot set side by side as one PNG, for Word and Excel. None when the set is empty."""
    from PIL import Image, ImageDraw

    figures = MASCOTS.get(mascot, MASCOTS["none"])["figures"]
    if not figures:
        return None
    scale = height / max(s * f["box"][1] / f["box"][0] for f, s, _ in figures)
    widths = [int(s * scale) for _, s, _ in figures]
    gap = int(height * 0.08)
    img = Image.new("RGBA", (sum(widths) + gap * (len(widths) - 1) + 4, height + 4), (0, 0, 0, 0) if background is None else _rgb(background) + (255,))
    d = ImageDraw.Draw(img)
    x0 = 2
    for (fig, s, mirror), wpx in zip(figures, widths):
        w, h = fig["box"]
        k = wpx / w
        top = height + 2 - h * k
        for shape in fig["shapes"]:
            kind = shape[0]
            if kind == "ellipse":
                cx, cy, rx, ry = shape[1]
                cx = w - cx if mirror else cx
                d.ellipse([x0 + (cx - rx) * k, top + (cy - ry) * k, x0 + (cx + rx) * k, top + (cy + ry) * k], fill=shape[2])
            elif kind == "poly":
                d.polygon([(x0 + px * k, top + py * k) for px, py in _flip(shape[1], w, mirror)], fill=shape[2])
            elif kind == "line":
                d.line([(x0 + px * k, top + py * k) for px, py in _flip(shape[1], w, mirror)], fill=shape[2], width=max(1, int(shape[3] * k)), joint="curve")
        x0 += wpx + gap
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def part_colours(layout: dict, palette: str) -> dict[str, str]:
    if palette == "app":
        return APP_DARK if layout.get("dark") else APP_LIGHT
    if palette == "ink":
        ink = layout["colours"]["ink"]
        return {k: ink for k in APP_LIGHT}
    return layout["parts"]


def catalog() -> dict:
    return {
        "layouts": [{"id": k, "name": v["name"], "family": v["family"], "description": v["description"], "dark": v["dark"], "charts": v["charts"], "mascot": v["mascot"], "swatch": [v["colours"]["bg"], v["colours"]["accent"], v["colours"]["ink"]]} for k, v in LAYOUTS.items()],
        "mascots": [{"id": k, "name": v["name"]} for k, v in MASCOTS.items()],
        "palettes": [{"id": k, "name": v} for k, v in PALETTES.items()],
    }
