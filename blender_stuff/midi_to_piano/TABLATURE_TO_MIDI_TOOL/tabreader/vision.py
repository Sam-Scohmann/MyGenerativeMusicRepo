"""Computer-vision pass over a tablature screenshot (Songsterr-style layout).

Everything is measured relative to the string spacing `s` of each staff, so
the same code works at any zoom level.

Layout conventions the reader relies on:
  * strings are thin, evenly spaced horizontal lines (4 for bass, 6 for guitar)
  * bar lines are verticals spanning top to bottom string
  * bar numbers are small grey digits just above the top string, right of a bar line
  * fret numbers are bold dark digits sitting on a string (they break the line)
  * rhythm sits under the staff: one stem per beat column,
      no stem = whole, short stem = half, full stem = quarter,
      each beam layer at the stem foot halves it, a dot right of the stem adds 50%
  * a stem with no fret above it is a tie continuation; "(n)" is a tie too
  * small digits are grace notes
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import drums, glyphs


# ---------------------------------------------------------------- data model

@dataclass
class Fret:
    x: float            # glyph centre x
    y: float            # glyph centre y
    string: int         # 0 = top line (highest pitched string)
    fret: int
    text: str
    box: tuple          # x, y, w, h
    tied: bool = False  # drawn as "(n)"
    grace: bool = False
    conf: float = 1.0
    dead: bool = False  # "X": muted / dead strum


@dataclass
class Strum:
    x: float
    y0: int             # arrow extent (top, bottom)
    y1: int
    direction: str      # "down": pick moves toward the floor, low strings sound first
                        # (arrowhead at the TOP of the tab); "up": the reverse
    strings: tuple = () # (first, last) string index the arrow covers
    outside: bool = False  # head pokes beyond the staff (so it can't be a bar line)


@dataclass
class Stem:
    x: float
    top: int
    bottom: int
    beams: int = 0
    dotted: bool = False
    short: bool = False  # half-note stem
    grace: bool = False
    tuplet: float = 1.0  # 2/3 under a triplet bracket


@dataclass
class Bar:
    number: int          # as read from the image (or inferred)
    x0: int
    x1: int
    number_read: bool = False
    frets: list = field(default_factory=list)
    stems: list = field(default_factory=list)
    hits: list = field(default_factory=list)      # drum noteheads
    accents: list = field(default_factory=list)   # x of '>' marks
    strums: list = field(default_factory=list)    # strum arrows


@dataclass
class Staff:
    index: int
    lines: list          # y of each string line, top to bottom
    spacing: float
    x0: int
    x1: int
    barlines: list       # x centres
    bars: list = field(default_factory=list)
    crop: tuple = (0, 0, 0, 0)   # x, y, w, h of the whole system incl. rhythm

    @property
    def top(self):
        return self.lines[0]

    @property
    def bottom(self):
        return self.lines[-1]

    @property
    def n_strings(self):
        return len(self.lines)


@dataclass
class Page:
    width: int
    height: int
    staffs: list
    tempo: int = None
    time_sig: tuple = (4, 4)
    warnings: list = field(default_factory=list)
    kind: str = "tab"     # "tab" or "drums"


# ---------------------------------------------------------------- helpers

def _runs(mask_1d):
    """(start, end_exclusive) of every True run in a 1-D bool array."""
    m = np.concatenate([[False], mask_1d.astype(bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _background(gray):
    hist = np.bincount(gray.ravel(), minlength=256)
    hist[:40] = 0   # ignore black screenshot borders
    return int(np.argmax(hist))


def _page_mask(gray):
    """False on the black frame a screenshot may have around the page."""
    dark = (gray < 15).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark, connectivity=4)
    h, w = gray.shape
    mask = np.ones_like(gray, bool)
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        touches = x == 0 or y == 0 or x + bw == w or y + bh == h
        if touches and area > 200:
            mask[lab == i] = False
    return mask


# ---------------------------------------------------------------- staffs

def find_staffs(gray, ink):
    h, w = gray.shape
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    hl = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, k)
    counts = hl.sum(1)
    cand = counts >= max(60, int(0.03 * w))

    # thin bands only: string lines are 1-2 px, beams are thicker
    lines = []
    for a, b in _runs(cand):
        if b - a <= 2:
            y = (a + b - 1) / 2
            xs = np.where(hl[a:b].any(0))[0]
            lines.append((y, xs.min(), xs.max()))

    # every evenly spaced chain of 4..8 lines is a candidate staff
    cands = []
    for i in range(len(lines)):
        for j in range(i + 1, min(i + 3, len(lines))):
            d = lines[j][0] - lines[i][0]
            if not 5 <= d <= 40:
                continue
            chain = [i, j]
            while True:
                nxt = [k2 for k2 in range(chain[-1] + 1, len(lines))
                       if abs(lines[k2][0] - lines[chain[-1]][0] - d) <= 1.2]
                if not nxt:
                    break
                chain.append(nxt[0])
            if 4 <= len(chain) <= 8:
                x0 = int(max(lines[k2][1] for k2 in chain))
                x1 = int(min(lines[k2][2] for k2 in chain))
                cands.append((x1 - x0, chain))

    # widest first; tie arcs over chords can mimic short line groups, so drop
    # anything overlapping an accepted staff
    staffs = []
    taken = []
    for width, chain in sorted(cands, key=lambda c: (-c[0], -len(c[1]))):
        ys = [lines[k2][0] for k2 in chain]
        if width < 6 * (ys[1] - ys[0]):
            continue
        if any(ys[0] <= b + 2 and ys[-1] >= a - 2 for a, b in taken):
            continue
        taken.append((ys[0], ys[-1]))
        x0 = int(min(lines[k2][1] for k2 in chain))
        x1 = int(max(lines[k2][2] for k2 in chain))
        staffs.append((ys, float(np.mean(np.diff(ys))), x0, x1))
    staffs.sort(key=lambda st: st[0][0])
    return staffs


def find_barlines(gray, bg, ink, ys, s, x0, x1):
    top, bot = int(round(ys[0])), int(round(ys[-1]))
    lo = max(0, x0 - 3)
    band = ink[top:bot + 1, lo:x1 + 4]
    frac = band.mean(0)
    # big dark glyphs (time signature) also fill whole columns; mask them out
    tall = np.zeros(band.shape[1], bool)
    dark = gray[int(top - s):int(bot + s), lo:x1 + 4] < bg - 150
    comps, _ = _components(dark)
    for (cx, cy, w, h, a, i) in comps:
        if h >= 1.5 * s:
            tall[max(0, cx - 1):cx + w + 1] = True
    # drum stems cross the whole staff too, but carry on below it to their beams
    below = ink[bot + 2:int(bot + 0.8 * s), lo:x1 + 4].mean(0)
    xs = []
    for a, b in _runs((frac >= 0.9) & ~tall & (below < 0.5)):
        xs.append((a + b - 1) / 2 + lo)
    # merge double / thick bar lines
    merged = []
    for x in xs:
        if merged and x - merged[-1][-1] <= 0.6 * s:
            merged[-1].append(x)
        else:
            merged.append([x])
    return [(g[0], g[-1]) for g in merged]


# ---------------------------------------------------------------- glyphs on strings

def _components(mask):
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [(int(st[i, 0]), int(st[i, 1]), int(st[i, 2]), int(st[i, 3]), int(st[i, 4]), i)
            for i in range(1, n)], lab


def find_frets(gray, bg, line_gray, staff, x_lo, x_hi, classifier, warnings):
    s = staff.spacing
    ys = staff.lines
    y0 = int(ys[0] - 0.9 * s)
    y1 = int(ys[-1] + 0.9 * s)
    sub = gray[y0:y1, x_lo:x_hi]
    # anything darker than the string lines; glyphs must also contain near-black
    # pixels, which ties, slides and lines never do
    ink = sub < line_gray - 6
    core = sub < bg - 150
    comps, lab = _components(ink)

    pieces = []
    for (x, y, w, h, a, i) in comps:
        m = (lab[y:y + h, x:x + w] == i) & core[y:y + h, x:x + w]
        if m.sum() < 2:
            continue
        cy_, cx_ = np.where(m)
        # tighten the box to the dark core (+1px antialias) so touching slides don't widen it
        bx0, bx1 = max(x, x + cx_.min() - 1), min(x + w, x + cx_.max() + 2)
        by0, by1 = max(y, y + cy_.min() - 1), min(y + h, y + cy_.max() + 2)
        bw, bh = bx1 - bx0, by1 - by0
        if bh < 0.4 * s or bh > 1.45 * s or bw > 1.1 * s:
            continue       # time signature, noise
        pieces.append([bx0, by0, bw, bh])
    if not pieces:
        return []

    hs = sorted(p[3] for p in pieces)
    digit_h = float(np.percentile(hs, 75))

    # one list of pieces per string, then group neighbours into numbers
    by_string = {}
    for p in pieces:
        cy = y0 + p[1] + p[3] / 2
        k = int(round((cy - ys[0]) / s))
        if 0 <= k < len(ys) and abs(cy - ys[k]) <= 0.45 * s:
            by_string.setdefault(k, []).append(p)

    frets = []
    for string, plist in by_string.items():
        plist.sort(key=lambda p: p[0])
        groups = []
        for p in plist:
            if groups and p[0] - max(q[0] + q[2] for q in groups[-1]) <= 0.3 * s:
                groups[-1].append(p)
            else:
                groups.append([p])
        for grp in groups:
            parens = [p for p in grp if p[2] <= 0.3 * s and p[3] >= 1.12 * digit_h]
            digits = [p for p in grp if p not in parens]
            if not digits:
                continue
            gx0 = min(p[0] for p in digits)
            gy0 = min(p[1] for p in digits)
            gx1 = max(p[0] + p[2] for p in digits)
            gy1 = max(p[1] + p[3] for p in digits)
            gh = gy1 - gy0
            text, conf = "", 1.0
            for p in digits:
                ch, cf = classifier.classify(sub[p[1]:p[1] + p[3], p[0]:p[0] + p[2]], bg)
                text += ch
                conf = min(conf, cf)
            tied = bool(parens) or "(" in text or ")" in text
            text = text.replace("(", "").replace(")", "")
            dead = text.upper() == "X"
            if not text.isdigit() and not dead:
                continue
            fx = x_lo + (gx0 + gx1) / 2
            cy = y0 + (gy0 + gy1) / 2
            frets.append(Fret(x=fx, y=cy, string=string, fret=0 if dead else int(text), text=text,
                              box=(x_lo + gx0, y0 + gy0, gx1 - gx0, gh),
                              tied=tied, grace=gh < 0.82 * digit_h, conf=conf, dead=dead))
            if conf < 0.6:
                warnings.append(f"low confidence fret '{text}' at x={fx:.0f} y={cy:.0f}")
    frets.sort(key=lambda f: (f.x, f.string))
    return frets


# ---------------------------------------------------------------- strum arrows

def find_strums(ink, staff, x_lo, x_hi):
    """Vertical arrows inside the staff, left of a chord. The arrowhead end tells
    the stroke direction; a bar line has no head."""
    s = staff.spacing
    ys = staff.lines
    y_top = int(ys[0] - 1.0 * s)
    y_bot = int(ys[-1] + 1.0 * s)
    band = ink[y_top:y_bot, x_lo:x_hi].astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(6, int(1.5 * s))))
    vl = cv2.morphologyEx(band, cv2.MORPH_OPEN, k)
    on_line = np.zeros(band.shape[0], bool)
    for y in ys:
        yy = int(round(y)) - y_top
        on_line[max(0, yy):yy + 1] = True        # string lines are 1 px

    out = []
    for a, b in _runs(vl.any(0)):
        if b - a > 3:
            continue
        rr = _runs(vl[:, a:b].any(1))
        r0, r1 = max(rr, key=lambda r: r[1] - r[0])
        xc = (a + b - 1) / 2

        def head_width(rows):
            # an arrowhead flares out on BOTH sides of the shaft (a bar number
            # beside a bar line only sits on one side)
            best = 0
            for r in rows:
                if 0 <= r < band.shape[0] and not on_line[r]:
                    left = int(band[r, max(0, a - 4):a].sum())
                    right = int(band[r, b:b + 4].sum())
                    if left >= 2 and right >= 2:
                        best = max(best, left + right)
            return best

        reach = int(0.5 * s)
        top_w = head_width(range(r0 - 2, r0 + reach))
        bot_w = head_width(range(r1 - reach, r1 + 2))
        if max(top_w, bot_w) == 0:
            continue                       # no arrowhead: bar line or stray stroke
        direction = "down" if top_w > bot_w else "up"
        y0, y1 = y_top + r0, y_top + r1 - 1
        first = int(np.clip(round((y0 - ys[0]) / s), 0, len(ys) - 1))
        last = int(np.clip(round((y1 - ys[0]) / s), 0, len(ys) - 1))
        outside = y0 < ys[0] - 1 if direction == "down" else y1 > ys[-1] + 1
        out.append(Strum(x=x_lo + xc, y0=y0, y1=y1, direction=direction, strings=(first, last),
                         outside=outside))
    return out


# ---------------------------------------------------------------- rhythm

def find_stems(ink, staff, x_lo, x_hi, dot_mask, halves=True):
    s = staff.spacing
    ry0 = int(staff.bottom + 0.6 * s)
    ry1 = int(staff.bottom + 3.4 * s)
    band = ink[ry0:ry1, x_lo:x_hi].astype(np.uint8)
    # take beams out first: stacked 16th beams are tall enough to pass as verticals
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(0.8 * s)), 1))
    band = band & (1 - cv2.morphologyEx(band, cv2.MORPH_OPEN, hk))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(4, int(0.45 * s))))
    vl = cv2.morphologyEx(band, cv2.MORPH_OPEN, k)

    cols = np.where(vl.any(0))[0]
    stems = []
    for a, b in _runs(np.isin(np.arange(band.shape[1]), cols)):
        if b - a > 3:
            continue
        xc = (a + b - 1) / 2
        col = vl[:, a:b].any(1)
        rr = _runs(col)
        if not rr:
            continue
        # first continuous run only: tuplet bracket ticks can sit just below the foot
        top, bot = ry0 + rr[0][0], ry0 + rr[0][1] - 1
        # the search band clips the stem; walk up to its real top
        xi = x_lo + a
        lim = int(staff.bottom + 1)
        while top - 1 > lim and ink[top - 1, xi:x_lo + b].any():
            top -= 1
        # ...and down to its real foot (beams sit lower when notes hang below the staff)
        lim = min(ink.shape[0] - 1, int(staff.bottom + 6 * s))
        while bot + 1 < lim:
            if ink[bot + 1, xi:x_lo + b].any():
                bot += 1
            else:
                # hop a short break where tremolo slashes cut the stem, if solid stem resumes
                col = ink[bot + 1:min(lim, bot + 8), xi:x_lo + b].any(1)
                nxt = next((k for k in range(1, min(4, len(col))) if col[k]), None)
                if nxt is None or not col[nxt:nxt + 3].all() or len(col[nxt:nxt + 3]) < 3:
                    break
                # only a tremolo break has slash ink right beside the stem
                if not ink[bot + 1:bot + 1 + nxt, max(0, xi - 4):x_lo + b + 4].any():
                    break
                bot += nxt + 1
        stems.append(Stem(x=x_lo + xc, top=top, bottom=bot))
    if not stems:
        return stems
    # real stems reach down to the beam line; parenthesis strokes and other
    # short verticals hanging just under the staff don't
    foot = sorted(st.bottom for st in stems)[len(stems) * 3 // 4]
    stems = [st for st in stems if st.bottom >= foot - 1.0 * s]

    tops = sorted(st.top for st in stems)
    usual_top = tops[len(tops) // 4]
    for st in stems:
        x = int(round(st.x))
        # beams: count dark bands just beside the stem foot
        y_hi = int(st.bottom - 1.0 * s)
        y_lo = st.bottom + 2
        # a beam is horizontal: the same rows are inked 2 px and 4 px from the stem
        # (on either side); diagonal tremolo slashes shift between the two
        side = np.zeros(y_lo - y_hi, bool)
        for sgn in (-1, 1):
            near, far = x + 2 * sgn, x + max(5, int(0.45 * s)) * sgn
            if 0 <= min(near, far) and max(near, far) < ink.shape[1]:
                side |= ink[y_hi:y_lo, near] & ink[y_hi:y_lo, far]
        # beams stack up from the stem foot
        runs = [r for r in _runs(side) if r[1] - r[0] >= 2][::-1]
        beams, edge = 0, len(side)
        for a, b in runs:
            if edge - b > (3 if beams == 0 else 0.6 * s):
                break
            beams += 1
            edge = a
        st.beams = beams
        st.short = halves and st.top - usual_top > 0.4 * s and st.beams == 0
        # augmentation dot: small blob right of the stem near its foot
        dx0, dx1 = x + 2, x + int(0.75 * s)
        dy0, dy1 = st.bottom - int(0.9 * s), st.bottom + 2
        win = dot_mask[dy0:dy1, dx0:dx1].astype(np.uint8)
        comps, _ = _components(win)
        for (cx, cy, w, h, a, _) in comps:
            if w <= 4 and h <= 4 and 2 <= a <= 14 and cy + h < win.shape[0] - 1 - 0:
                # a beam stub is wider than tall and touches the stem; a dot is detached
                if cx >= 1:
                    st.dotted = True
    return stems


def find_tuplets(gray, bg, staff, stems, classifier):
    """'⌐ 3 ⌐' brackets under the beams -> [(x0, x1, n)]."""
    if not stems:
        return []
    s = staff.spacing
    foot = int(np.median([st.bottom for st in stems]))
    y0, y1 = foot + 2, int(foot + 1.6 * s)
    x_lo = max(0, int(min(st.x for st in stems) - s))
    sub = gray[y0:y1, x_lo:int(max(st.x for st in stems) + s)]
    m = sub < bg - 60
    comps, lab = _components(m)
    out = []
    for (x, y, w, h, a, i) in comps:
        if not (0.4 * s <= h <= 0.9 * s and w <= 0.7 * s):
            continue
        ch, cf = classifier.classify(sub[y:y + h, x:x + w], bg)
        if not ch.isdigit() or int(ch) < 3 or cf < 0.3:
            continue
        # bracket: one continuous horizontal stroke each side of the digit (text is
        # broken into letters), each ending in a tick that turns up toward the beams
        reach = int(5 * s)

        def stroke(side):
            best = None
            for k in range(y, y + h):
                row = m[k]
                if side < 0:
                    seg = row[max(0, x - reach):x][::-1]     # walking left from the digit
                else:
                    seg = row[x + w:x + w + reach]
                on = np.where(seg)[0]
                if not len(on) or on[0] > 0.6 * s:
                    continue
                run_end = on[0]
                while run_end + 1 < len(seg) and seg[run_end + 1]:
                    run_end += 1
                length = run_end - on[0] + 1
                if best is None or length > best[1]:
                    end_x = x - 1 - run_end if side < 0 else x + w + run_end
                    best = (k, length, end_x)
            return best

        lb, rb = stroke(-1), stroke(1)
        if not lb or not rb or lb[1] < 0.8 * s or rb[1] < 0.8 * s:
            continue
        tick = lambda cx, ky: m[max(0, ky - h):ky, max(0, cx - 1):cx + 2].any(1).sum() >= max(2, h // 3)
        if not (tick(lb[2], lb[0]) and tick(rb[2], rb[0])):
            continue
        l, r = lb[2], rb[2]
        out.append((x_lo + l, x_lo + r, int(ch)))
    return out


TUPLET_FACTOR = {3: 2 / 3, 5: 4 / 5, 6: 4 / 6, 7: 4 / 7, 9: 8 / 9}


# ---------------------------------------------------------------- bar numbers / tempo

def read_bar_number(gray, bg, staff, xbar, classifier):
    s = staff.spacing
    y0, y1 = int(staff.top - 2.4 * s), int(staff.top - 0.3 * s)
    x0, x1 = int(xbar - 0.4 * s), int(xbar + 4 * s)
    if y0 < 0:
        return None
    sub = gray[y0:y1, x0:x1]
    m = sub < bg - 35
    comps, _ = _components(m)
    comps = [c for c in comps if 0.45 * s <= c[3] <= 1.2 * s and c[2] <= 0.9 * s]
    if not comps:
        return None
    comps.sort(key=lambda c: c[0])
    # keep the left-most run of adjacent digits on one baseline
    run = [comps[0]]
    for c in comps[1:]:
        if c[0] - (run[-1][0] + run[-1][2]) <= 0.55 * s and abs(c[1] - run[0][1]) <= 2:
            run.append(c)
    text = ""
    for c in run:
        ch, cf = classifier.classify(sub[c[1]:c[1] + c[3], c[0]:c[0] + c[2]], bg)
        if cf < 0.45:
            return None
        text += ch
    return int(text) if text.isdigit() else None


def read_tempo(gray, bg, staff, classifier):
    """Digits after '=' above the first staff (e.g. '♩ = 92')."""
    s = staff.spacing
    y0, y1 = max(0, int(staff.top - 5 * s)), int(staff.top - 1.6 * s)
    sub = gray[y0:y1, staff.x0:staff.x0 + int(30 * s)]
    m = sub < bg - 35
    comps, _ = _components(m)
    # '=' is two flat strokes; find it, then read the digits right of it
    flats = [c for c in comps if c[3] <= 2 and 0.3 * s <= c[2] <= 0.9 * s]
    if len(flats) < 2:
        return None
    flats.sort(key=lambda c: (c[0], c[1]))
    eq_x = flats[0][0] + flats[0][2]
    eq_y = flats[0][1]
    digs = [c for c in comps if c[0] > eq_x and c[0] - eq_x < 4 * s
            and 0.45 * s <= c[3] <= 1.2 * s and abs(c[1] + c[3] / 2 - eq_y) < s]
    digs.sort(key=lambda c: c[0])
    text = ""
    for c in digs:
        if text and c[0] - prev_x1 > 0.4 * s:
            break
        ch, cf = classifier.classify(sub[c[1]:c[1] + c[3], c[0]:c[0] + c[2]], bg)
        text += ch
        prev_x1 = c[0] + c[2]
    return int(text) if text.isdigit() and 20 <= int(text) <= 400 else None


def read_time_signature(gray, bg, staff, classifier):
    """Big stacked digits at the start of a staff, if any."""
    s = staff.spacing
    sub = gray[int(staff.top - 0.5 * s):int(staff.bottom + 0.8 * s),
               staff.x0:staff.x0 + int(8 * s)]
    m = sub < bg - 150
    comps, _ = _components(m)
    big = [c for c in comps if c[3] >= 1.5 * s]
    if len(big) < 2:
        return None
    big.sort(key=lambda c: c[1])
    vals = []
    for c in (big[0], big[-1]):
        ch, cf = classifier.classify(sub[c[1]:c[1] + c[3], c[0]:c[0] + c[2]], bg)
        if not ch.isdigit():
            return None
        vals.append(int(ch))
    return tuple(vals), big


# ---------------------------------------------------------------- main entry

def analyze(img, classifier=None):
    classifier = classifier or glyphs.default_classifier()
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sat = img.max(2).astype(int) - img.min(2).astype(int)
    else:
        gray, sat = img, np.zeros_like(img, int)
    h, w = gray.shape
    bg = _background(gray)
    page = _page_mask(gray)
    ink = (gray < bg - 40) & page & (sat < 60)   # drop coloured UI overlays
    dot_mask = (gray < bg - 90) & page

    page_obj = Page(width=w, height=h, staffs=[])
    raw = find_staffs(gray, ink)
    line_gray = bg
    if raw:
        ys0, _, lx0, lx1 = raw[0]
        row = gray[int(round(ys0[0])), lx0:lx1]
        line_gray = int(np.median(row[row < bg - 20])) if (row < bg - 20).any() else bg - 80
    # drum notation: 5-line staffs whose glyphs are noteheads, so the digit
    # classifier is unsure about most of what it sees there
    fives = [r for r in raw if len(r[0]) == 5]
    if raw and len(fives) > len(raw) / 2:
        ys5, s5, a5, b5 = fives[0]
        probe = Staff(index=0, lines=ys5, spacing=s5, x0=a5, x1=b5, barlines=[])
        fr = find_frets(gray, bg, line_gray, probe, a5, b5, classifier, [])
        if not fr or sum(f.conf < 0.6 for f in fr) / len(fr) > 0.3:
            page_obj.kind = "drums"

    prev_num = 0
    for si, (ys, s, x0, x1) in enumerate(raw):
        bl = find_barlines(gray, bg, ink, ys, s, x0, x1)
        strums = [] if len(ys) == 5 else find_strums(ink, Staff(index=si, lines=ys, spacing=s, x0=x0,
                                                                   x1=x1, barlines=[]), x0, x1 + 1)
        # a strum across every string spans the staff like a bar line, but its head
        # pokes out of the staff; ties crossing a real bar line never do
        near = lambda t, g: g[0] - 2 <= t.x <= g[1] + 2
        bl = [g for g in bl if not any(near(t, g) and t.outside for t in strums)]
        strums = [t for t in strums if not any(near(t, g) for g in bl)]
        # a staff may start without a vertical line (first system)
        if bl and bl[0][0] - x0 > 1.5 * s:
            bl.insert(0, (float(x0), float(x0)))
        if len(bl) < 2:
            page_obj.warnings.append(f"staff {si + 1}: no bar lines found")
            continue
        st = Staff(index=si, lines=ys, spacing=s, x0=int(bl[0][0]), x1=int(bl[-1][1]),
                   barlines=bl)
        if si == 0:
            ts = read_time_signature(gray, bg, st, classifier)
            if ts:
                page_obj.time_sig = ts[0]
            page_obj.tempo = read_tempo(gray, bg, st, classifier)

        if page_obj.kind == "drums" and st.n_strings == 5:
            frets = []
            hits = drums.find_hits(gray, bg, st, st.x0, st.x1 + 1)
            accents = drums.find_accents(gray, bg, st, st.x0, st.x1 + 1)
        else:
            frets = find_frets(gray, bg, line_gray, st, st.x0, st.x1 + 1, classifier, page_obj.warnings)
            hits, accents = [], []
        # drum stems start at their highest head, so stem start can't mark half notes
        stems = find_stems(ink, st, st.x0, st.x1 + 1, dot_mask, halves=not hits)
        for tx0, tx1, tn in find_tuplets(gray, bg, st, stems, classifier):
            for t in stems:
                if tx0 - 2 <= t.x <= tx1 + 2:
                    t.tuplet = TUPLET_FACTOR.get(tn, 1.0)
        for k in range(len(bl) - 1):
            bx0, bx1 = bl[k][1], bl[k + 1][0]
            if bx1 - bx0 < 1.5 * s:
                continue
            num = read_bar_number(gray, bg, st, bx0, classifier)
            expected = prev_num + 1
            if num is not None and prev_num and num != expected:
                # a dropped or merged digit ("1" for "101") -> trust the sequence
                if str(num) in str(expected) or str(expected) in str(num) or num < prev_num:
                    num = None
            bar = Bar(number=num if num is not None else expected, x0=int(bx0), x1=int(bx1),
                      number_read=num is not None)
            if num is not None and num != expected and prev_num:
                page_obj.warnings.append(
                    f"bar number read as {num}, expected {expected} (staff {si + 1})")
            prev_num = bar.number
            bar.frets = [f for f in frets if bx0 < f.x < bx1]
            bar.stems = [t for t in stems if bx0 < t.x < bx1]
            bar.hits = [hh for hh in hits if bx0 < hh.cx < bx1]
            bar.accents = [ax for ax in accents if bx0 < ax < bx1]
            bar.strums = [t for t in strums if bx0 < t.x < bx1]
            for t in bar.stems:
                near = min(bar.frets, key=lambda f: abs(f.x - t.x), default=None)
                if near is not None and near.grace and abs(near.x - t.x) <= 0.6 * s:
                    t.grace = True
                    t.short = False
            st.bars.append(bar)

        top_y = int(max(0, ys[0] - 3.2 * s))
        bot_y = int(min(h, ys[-1] + 5.2 * s))
        st.crop = (max(0, st.x0 - int(2 * s)), top_y,
                   min(w, st.x1 + int(2 * s)) - max(0, st.x0 - int(2 * s)), bot_y - top_y)
        page_obj.staffs.append(st)

    # make adjacent crops not overlap
    for a, b in zip(page_obj.staffs, page_obj.staffs[1:]):
        ax, ay, aw, ah = a.crop
        bx, by, bw, bh = b.crop
        if ay + ah > by:
            mid = (int(a.bottom) + int(b.top)) // 2 + int(1.5 * b.spacing)
            mid = min(mid, by + bh)
            a.crop = (ax, ay, aw, max(1, min(ay + ah, mid) - ay))
            b.crop = (bx, max(by, mid - 1), bw, by + bh - max(by, mid - 1))
    return page_obj


# ---------------------------------------------------------------- debug drawing

def draw_overlay(img, page):
    out = img.copy() if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for st in page.staffs:
        for y in st.lines:
            cv2.line(out, (st.x0, int(y)), (st.x1, int(y)), (255, 160, 0), 1)
        for a, b in st.barlines:
            cv2.line(out, (int(a), int(st.top) - 6), (int(a), int(st.bottom) + 6), (0, 0, 255), 1)
        for bar in st.bars:
            col = (0, 140, 0) if bar.number_read else (0, 0, 255)
            cv2.putText(out, str(bar.number), (bar.x0 + 2, int(st.top) - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
            for f in bar.frets:
                x, y, w, h = f.box
                c = (0, 0, 255) if f.conf < 0.6 else (200, 0, 200) if f.tied else (0, 170, 255) if f.grace else (0, 160, 0)
                cv2.rectangle(out, (x - 1, y - 1), (x + w, y + h), c, 1)
                cv2.putText(out, f.text, (x, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.3, c, 1)
            for hh in bar.hits:
                x, y, w, h = hh.box
                c = (200, 0, 200) if hh.ghost else (0, 160, 0)
                cv2.rectangle(out, (x - 1, y - 1), (x + w, y + h), c, 1)
                cv2.putText(out, f"{hh.shape[:2]}{hh.pos}", (x - 2, y - 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.28, c, 1)
            for t in bar.strums:
                c = (0, 90, 230) if t.direction == "down" else (230, 120, 0)
                cv2.line(out, (int(t.x) - 3, t.y0), (int(t.x) - 3, t.y1), c, 2)
                tip = t.y0 if t.direction == "down" else t.y1
                d = 5 if t.direction == "down" else -5
                cv2.line(out, (int(t.x) - 3, tip), (int(t.x) - 7, tip + d), c, 2)
                cv2.line(out, (int(t.x) - 3, tip), (int(t.x) + 1, tip + d), c, 2)
            for ax in bar.accents:
                cv2.circle(out, (int(ax), int(st.top - 2.4 * st.spacing)), 6, (0, 120, 255), 1)
            for t in bar.stems:
                c = (255, 0, 0) if t.tuplet == 1.0 else (0, 100, 255)
                cv2.line(out, (int(t.x) + 2, t.top), (int(t.x) + 2, t.bottom), c, 1)
                lab = ("h" if t.short else "") + str(t.beams) + ("." if t.dotted else "") + ("g" if t.grace else "")
                cv2.putText(out, lab, (int(t.x) - 3, t.bottom + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.3, c, 1)
        x, y, w, h = st.crop
        cv2.rectangle(out, (x, y), (x + w - 1, y + h - 1), (160, 160, 160), 1)
    return out
