"""Drum notation: noteheads on a 5-line staff.

A hit is identified by its notehead *shape* and its vertical *position*,
counted in half-spaces from the top line (top line = 0, lines are even numbers,
spaces odd; negative is above the staff). The key "x@-1" means an x head in the
space above the staff. Keys are mapped to General MIDI drum notes; the mapping
is editable in the UI and saved to drum_map.json.
"""
import json
import os
from dataclasses import dataclass

import cv2
import numpy as np

SHAPES = ("filled", "x", "circle_x", "circle_slash", "triangle")
SHAPE_SYMBOL = {"filled": "●", "x": "×", "circle_x": "⊗", "circle_slash": "⊘", "triangle": "▲"}

GM_DRUMS = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick", 38: "Acoustic Snare",
    39: "Hand Clap", 40: "Electric Snare", 41: "Low Floor Tom", 42: "Closed Hi-Hat",
    43: "High Floor Tom", 44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat",
    47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom",
    51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine",
    55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 59: "Ride Cymbal 2",
    75: "Claves", 76: "Hi Wood Block", 77: "Low Wood Block",
}

# Songsterr / Guitar Pro layout (matches the legend: kick space 4-5, snare space 2-3,
# low tom space 3-4, mid tom middle line, crash on the ledger above, tambourine triangle)
DEFAULT_MAP = {
    "x@-3": 57, "x@-2": 49, "circle_x@-2": 49,
    "x@-1": 42, "circle_slash@-1": 46, "circle_x@-1": 46,
    "x@0": 51, "circle_x@0": 53, "x@1": 51,
    "filled@-1": 50, "filled@0": 50, "filled@1": 50, "filled@2": 48,
    "filled@3": 38, "x@3": 37, "circle_x@3": 37,
    "filled@4": 47, "filled@5": 45, "filled@6": 43, "filled@7": 36, "filled@8": 35,
    "x@9": 44, "x@8": 44, "x@7": 44,
    "triangle@3": 54, "triangle@-1": 56, "triangle@0": 56,
}
SHAPE_FALLBACK = {"filled": 38, "x": 42, "circle_x": 46, "circle_slash": 46, "triangle": 54}

MAP_FILE = os.path.join(os.path.dirname(__file__), "..", "drum_map.json")

VEL_NORMAL, VEL_ACCENT, VEL_GHOST = 100, 124, 52


def key(shape, pos):
    return f"{shape}@{pos}"


def describe(pos):
    if pos < 0:
        n = -pos
        return "above staff" if n == 1 else f"{(n + 1) // 2} ledger{'s' if n > 2 else ''} above" if n % 2 == 0 else f"space above ledger {n // 2}"
    if pos > 8:
        return "space below staff" if pos == 9 else "below staff"
    return f"line {pos // 2 + 1}" if pos % 2 == 0 else f"space {pos // 2 + 1}–{pos // 2 + 2}"


def load_map():
    m = dict(DEFAULT_MAP)
    try:
        with open(MAP_FILE, encoding="utf-8") as f:
            m.update({k: int(v) for k, v in json.load(f).items()})
    except (OSError, ValueError):
        pass
    return m


def save_map(m):
    """Only your overrides are stored, so improved defaults still reach you."""
    diff = {k: v for k, v in m.items() if DEFAULT_MAP.get(k) != v}
    if not diff:
        if os.path.exists(MAP_FILE):
            os.remove(MAP_FILE)
        return
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(diff, f, indent=1, sort_keys=True)


def note_for(drum_map, k):
    if k in drum_map:
        return drum_map[k]
    return SHAPE_FALLBACK.get(k.split("@")[0], 38)


@dataclass
class Hit:
    x: float          # head left edge (the stem side for stem-down notes)
    cx: float
    cy: float
    pos: int
    shape: str
    ghost: bool
    box: tuple
    dotted: bool = False

    @property
    def key(self):
        return key(self.shape, self.pos)


def _strip_thin(ink):
    """Drop every pixel that is 1 px thin vertically or horizontally (lines, stems)."""
    p = np.pad(ink, 1)
    up, dn = p[:-2, 1:-1], p[2:, 1:-1]
    lf, rt = p[1:-1, :-2], p[1:-1, 2:]
    return ink & ~(~up & ~dn) & ~(~lf & ~rt)


def _clean(ink, s):
    """Drop staff/ledger lines and stems: long runs of 1-px-thin ink. Short thin
    strokes (x arms, ring outlines) are glyph parts and stay."""
    p = np.pad(ink, 1)
    up, dn = p[:-2, 1:-1], p[2:, 1:-1]
    lf, rt = p[1:-1, :-2], p[1:-1, 2:]
    k = max(5, int(0.9 * s))
    h_thin = (ink & ~up & ~dn).astype(np.uint8)
    v_thin = (ink & ~lf & ~rt).astype(np.uint8)
    h_long = cv2.morphologyEx(h_thin, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    v_long = cv2.morphologyEx(v_thin, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    return ink & ~h_long.astype(bool) & ~v_long.astype(bool)


def classify(m, s):
    """Shape of a cleaned head mask, or None if it isn't a notehead."""
    h, w = m.shape
    if h < 0.55 * s or w < 0.55 * s or h > 1.3 * s or w > 1.4 * s:
        return None
    q = cv2.resize(m.astype(np.float32), (9, 9), interpolation=cv2.INTER_AREA)
    fill = m.mean()
    corners = [q[0:2, 0:2].mean(), q[0:2, 7:9].mean(), q[7:9, 0:2].mean(), q[7:9, 7:9].mean()]
    edge_mid = [q[0, 3:6].mean(), q[8, 3:6].mean(), q[3:6, 0].mean(), q[3:6, 8].mean()]
    centre = q[3:6, 3:6].mean()
    big = max(h, w) >= 1.05 * s

    if big and min(edge_mid) > 0.25 and max(q[0, 0], q[0, 8], q[8, 0], q[8, 8]) < 0.35:
        # ring: tell ⊗ from ⊘ by the "\" diagonal
        back = (q[2, 2] + q[6, 6]) / 2
        fwd = (q[2, 6] + q[6, 2]) / 2
        return "circle_x" if back > 0.3 and fwd > 0.3 else "circle_slash"
    if min(corners) > 0.15 and max(edge_mid) < 0.3 and centre > 0.3:
        return "x"
    # triangle: apex at top-middle, both bottom corners inked; a tilted filled
    # ellipse always leaves one bottom corner empty
    if (q[0, 3:6].mean() > 0.2 and q[0, 0] < 0.15 and q[0, 8] < 0.15
            and q[8, 0] > 0.3 and q[8, 8] > 0.3 and q[8, :].mean() > 0.6):
        return "triangle"
    if fill > 0.55 and centre > 0.8 and 0.8 <= w / h <= 1.6:
        return "filled"
    return None


def _strip_parens(m, s):
    """'(head)' drawn as one blob: peel thin columns off both sides -> (inner, ghost)."""
    cols = m.sum(0)
    lim = int(0.45 * s)
    left = next((i for i in range(1, min(lim, len(cols))) if cols[i] == 0), None)
    right = next((len(cols) - 1 - i for i in range(1, min(lim, len(cols))) if cols[-1 - i] == 0), None)
    if left is None or right is None or right - left < 0.5 * s:
        return m, False, 0
    inner = m[:, left + 1:right]
    ys = np.where(inner.any(1))[0]
    return inner[ys.min():ys.max() + 1], True, left + 1


def find_hits(gray, bg, staff, x_lo, x_hi):
    s = staff.spacing
    top, bot = staff.top, staff.bottom
    y0 = int(top - 3.4 * s)
    y1 = int(bot + 1.6 * s)
    sub = gray[y0:y1, x_lo:x_hi]
    ink = sub < bg - 40
    dark = sub < bg - 150
    hits, parens = [], []
    # pass 1: strip every 1-px-thin pixel -> clean filled / x / triangle heads
    # pass 2: strip only long thin runs -> keeps 1-px ring outlines (⊗ ⊘)
    for pass_no, m in ((1, _strip_thin(ink)), (2, _clean(ink, s))):
        n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        for i in range(1, n):
            x, y, w, h, a = (int(v) for v in st[i])
            if a < 5:
                continue
            comp = lab[y:y + h, x:x + w] == i
            if not (dark[y:y + h, x:x + w] & comp).any():
                continue           # light grey text (bar numbers, lyrics)
            if pass_no == 2:
                # trim stem stubs: 1-px-wide rows hanging off the top or bottom
                rows = comp.sum(1)
                r0, r1 = 0, h
                while r0 < r1 and rows[r0] <= 1:
                    r0 += 1
                while r1 > r0 and rows[r1 - 1] <= 1:
                    r1 -= 1
                if r1 - r0 < 2:
                    continue
                comp = comp[r0:r1]
                y, h = y + r0, r1 - r0
                cs = np.where(comp.any(0))[0]
                comp = comp[:, cs.min():cs.max() + 1]
                x, w = x + cs.min(), cs.max() - cs.min() + 1
            elif w <= 0.4 * s and h >= 0.75 * s:
                parens.append((x, y, w, h))
                continue
            ghost = False
            ox = 0
            if w > 1.3 * s:
                comp, ghost, ox = _strip_parens(comp, s)
            shape = classify(comp, s)
            if shape is None or (pass_no == 2) != shape.startswith("circle"):
                continue
            ys, xs = np.where(comp)
            hx0 = x + ox + xs.min()
            cy = y0 + y + (ys.min() + ys.max()) / 2
            if ghost:
                cy = y0 + y + h / 2
            pos = int(round((cy - top) / (s / 2)))
            if not -6 <= pos <= 11:
                continue
            hw = xs.max() - xs.min() + 1
            cx = x_lo + hx0 + hw / 2
            if pass_no == 2 and any(abs(o.cx - cx) < 0.6 * s and abs(o.cy - cy) < 0.6 * s for o in hits):
                continue
            hits.append(Hit(x=x_lo + hx0, cx=cx, cy=cy, pos=pos, shape=shape,
                            ghost=ghost, box=(x_lo + x, y0 + y, w, h)))

    # separate parentheses either side of a head -> ghost note
    for hit in hits:
        bx, by, bw, bh = hit.box
        lpar = any(abs((x_lo + px + pw) - bx) <= 0.4 * s and abs(y0 + py + ph / 2 - hit.cy) < 0.5 * s
                   for px, py, pw, ph in parens)
        rpar = any(abs((x_lo + px) - (bx + bw)) <= 0.4 * s and abs(y0 + py + ph / 2 - hit.cy) < 0.5 * s
                   for px, py, pw, ph in parens)
        if lpar and rpar:
            hit.ghost = True
            # skip past the closing parenthesis when looking for a dot
            bw += int(0.4 * s)
        # augmentation dot drawn beside the head
        dx0 = int(bx + bw - x_lo + 1)
        win = dark[max(0, int(hit.cy - y0 - 0.8 * s)):int(hit.cy - y0 + 0.4 * s), dx0:dx0 + int(0.8 * s)]
        if win.size:
            n2, _, st2, _ = cv2.connectedComponentsWithStats(win.astype(np.uint8), connectivity=8)
            hit.dotted = any(st2[j, 2] <= 4 and st2[j, 3] <= 4 and 2 <= st2[j, 4] <= 14
                             for j in range(1, n2))
    hits.sort(key=lambda h_: (h_.x, h_.pos))
    return hits


def find_accents(gray, bg, staff, x_lo, x_hi):
    """'>' marks above the staff -> list of x centres."""
    s = staff.spacing
    y0 = int(max(0, staff.top - 3.6 * s))
    y1 = int(staff.top - 1.2 * s)
    sub = gray[y0:y1, x_lo:x_hi]
    m = sub < bg - 50
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if not (0.6 * s <= w <= 1.3 * s and 0.3 * s <= h <= 0.75 * s):
            continue
        comp = lab[y:y + h, x:x + w] == i
        # '>' : two strokes meeting at the right-middle; left column has ink top and bottom
        left = comp[:, :max(1, w // 4)].any(1)
        right_mid = comp[h // 3:h - h // 3, w - max(2, w // 4):].any()
        if left[:max(1, h // 3)].any() and left[-max(1, h // 3):].any() and right_mid \
                and not comp[h // 2, :w // 3].any():
            out.append(x_lo + x + w / 2)
    return out
