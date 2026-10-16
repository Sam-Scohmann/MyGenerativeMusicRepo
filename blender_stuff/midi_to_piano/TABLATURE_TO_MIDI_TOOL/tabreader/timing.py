"""Detected stems + frets/drum heads -> timed notes.

Time is measured in quarter-note beats. Each bar is checked against the time
signature. When the rhythm read from the stems doesn't add up, the bar is
repaired in this order:
  1. an unbeamed note is really a half/whole note (plain stems look the same),
  2. hidden rests: time is inserted where the note spacing shows a gap,
  3. last resort: durations are scaled to fit (flagged as a warning).
"""
from dataclasses import dataclass, field

from . import drums as drumkit

TUNINGS = {
    # top line (highest string) first
    4: [43, 38, 33, 28],                  # bass  G2 D2 A1 E1
    5: [43, 38, 33, 28, 23],              # 5-string bass  G D A E B
    6: [64, 59, 55, 50, 45, 40],          # guitar E4 B3 G3 D3 A2 E2
    7: [64, 59, 55, 50, 45, 40, 35],      # 7-string guitar
}
TUNING_NAMES = {4: "Bass EADG", 5: "Bass BEADG", 6: "Guitar EADGBE", 7: "Guitar BEADGBE"}

GRACE_BEATS = 0.125
EPS = 1e-3


@dataclass
class Note:
    start: float        # beats from song start
    dur: float          # beats
    pitch: int
    velocity: int
    staff: int
    bar: int
    string: int = -1
    fret: int = -1
    grace: bool = False
    drum: str = ""      # drum key ("filled@7") for drum notes
    strum: str = ""     # "down" / "up" when part of a strummed chord
    dead: bool = False  # muted "X" note


@dataclass
class BarTiming:
    number: int
    staff: int
    start: float
    length: float
    status: str = "ok"   # ok | repaired | scaled | empty
    note: str = ""

    @property
    def ok(self):
        return self.status != "scaled"


@dataclass
class Song:
    notes: list
    bars: list
    tempo: float
    time_sig: tuple
    warnings: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)   # staff -> [(beat, x_px)] for playheads
    kind: str = "tab"

    def beat_to_x(self, staff, beat):
        pts = self.anchors.get(staff)
        if not pts:
            return None
        if beat <= pts[0][0]:
            return pts[0][1]
        for (b0, x0), (b1, x1) in zip(pts, pts[1:]):
            if b0 <= beat <= b1:
                return x0 if b1 == b0 else x0 + (x1 - x0) * (beat - b0) / (b1 - b0)
        return pts[-1][1]

    def staff_range(self, staff):
        """(start_beat, end_beat) of one staff row."""
        bs = [b for b in self.bars if b.staff == staff]
        if not bs:
            return 0.0, 0.0
        return bs[0].start, bs[-1].start + bs[-1].length

    @property
    def length(self):
        return self.bars[-1].start + self.bars[-1].length if self.bars else 0.0


def stem_beats(st):
    base = 2.0 if st.short else 1.0
    d = base / (2 ** st.beams)
    if st.dotted:
        d *= 1.5
    return d * st.tuplet


# ---------------------------------------------------------------- bar repair

def _q(v, step=0.25):
    return round(v / step) * step


def repair(cols, bar_len, bar, s):
    """Make the column durations fill the bar. Mutates cols (dur, gap) -> (status, note)."""
    total = sum(c["dur"] for c in cols)
    if abs(total - bar_len) <= EPS:
        return "ok", ""
    if total < bar_len:
        deficit = bar_len - total
        # 1. plain stems can't show half/whole notes on some layouts: lengthen one
        for c in reversed(cols):
            if c["stem"] is None or c["stem"].beams or c["stem"].short:
                continue
            for mult in (2, 4):
                if abs(c["dur"] * (mult - 1) - deficit) <= EPS:
                    c["dur"] *= mult
                    return "repaired", f"read a {c['dur']:g}-beat note from a plain stem"
        # 2. hidden rests: where a note sits further from its neighbour than its
        #    length explains. Pixels-per-beat comes from the bar's own spacing.
        ratios = sorted((cols[i + 1]["x"] - cols[i]["x"]) / cols[i]["dur"]
                        for i in range(len(cols) - 1) if cols[i]["dur"] > 0)
        ppb = ratios[len(ratios) // 3] if ratios else (bar.x1 - bar.x0) / bar_len
        lead = 1.6 * s                      # bar line to first note
        cands = [((cols[0]["x"] - bar.x0 - lead) / ppb, 0)]
        for i in range(1, len(cols)):
            cands.append(((cols[i]["x"] - cols[i - 1]["x"]) / ppb - cols[i - 1]["dur"], i))
        left, inserted = deficit, []
        for extra, i in sorted(cands, reverse=True):
            rest = min(_q(extra), _q(left))
            if rest >= 0.25 - EPS:
                cols[i]["gap"] = rest
                left -= rest
                inserted.append(rest)
        if inserted:
            return "repaired", f"assumed rest(s) of {', '.join(f'{r:g}' for r in inserted)} beat(s)" +                 (f"; {left:g} beat(s) rest at the end" if left > EPS else "")
        return "repaired", f"{deficit:g} beat(s) rest at the end"
    k = bar_len / total
    for c in cols:
        c["dur"] *= k
    return "scaled", f"rhythm adds to {total:g} beats, expected {bar_len:g}; scaled to fit"


# ---------------------------------------------------------------- columns

def _columns(bar, items, x_of, s, bar_len, warns, label):
    """Attach items (frets or drum hits) to the nearest stem."""
    stems = sorted((st for st in bar.stems if not st.grace), key=lambda st: st.x)
    cols = [{"x": st.x, "dur": stem_beats(st), "items": [], "stem": st, "gap": 0.0} for st in stems]
    loose = []
    for it in items:
        best = min(cols, key=lambda c: abs(c["x"] - x_of(it)), default=None)
        if best is not None and abs(best["x"] - x_of(it)) <= 0.9 * s:
            best["items"].append(it)
        else:
            loose.append(it)
    if not cols and loose:
        # stemless notes: whole notes (or several spread over the bar)
        groups = []
        for it in sorted(loose, key=x_of):
            if groups and x_of(it) - groups[-1]["x"] <= 0.9 * s:
                groups[-1]["items"].append(it)
            else:
                groups.append({"x": x_of(it), "dur": 0.0, "items": [it], "stem": None, "gap": 0.0})
        for g in groups:
            g["dur"] = bar_len / len(groups)
        return groups
    for it in loose:
        # a stemless note among stemmed ones: its own column, length decided by repair
        warns.append(f"bar {bar.number}: {label(it)} has no rhythm stem")
        cols.append({"x": x_of(it), "dur": 1.0, "items": [it], "stem": None, "gap": 0.0})
    cols.sort(key=lambda c: c["x"])
    return cols


STRUM_MS = 12          # default spread between strings of a strummed chord
UP_VELOCITY_DROP = 10  # upstrokes catch fewer strings cleanly and sound lighter
DEAD_VELOCITY = 40
DEAD_BEATS = 0.08


def build_song(page, tuning=None, transpose=0, velocity=96, tempo=None, drum_map=None,
               strum_ms=STRUM_MS):
    num, den = page.time_sig
    bar_len = num * 4.0 / den
    tempo = tempo or page.tempo or 120
    drum_map = drum_map if drum_map is not None else drumkit.load_map()
    notes, bars, warns = [], [], list(page.warnings)
    t = 0.0
    sounding = {}   # string -> last Note (for ties)
    anchors = {}
    strum_step = strum_ms / 1000.0 * tempo / 60.0   # ms per string -> beats

    for staff in page.staffs:
        is_drum = page.kind == "drums" and staff.n_strings == 5
        tune = tuning or TUNINGS.get(staff.n_strings) or TUNINGS[6][:staff.n_strings]
        s = staff.spacing
        for bar in staff.bars:
            if is_drum:
                cols = _columns(bar, bar.hits, lambda h: h.x, s, bar_len, warns,
                                lambda h: f"{h.shape} head")
                for c in cols:
                    st = c["stem"]
                    if any(h.dotted for h in c["items"]) and (st is None or not st.dotted):
                        c["dur"] *= 1.5
            else:
                cols = _columns(bar, [f for f in bar.frets if not f.grace], lambda f: f.x, s,
                                bar_len, warns, lambda f: f"fret {f.text}")

            if not cols:
                status, note = "empty", "empty bar (rest)"
            else:
                status, note = repair(cols, bar_len, bar, s)
            if status != "ok" and status != "empty":
                warns.append(f"bar {bar.number}: {note}")

            pts = anchors.setdefault(staff.index, [])
            if not cols:
                pts.append((t, bar.x0))
            pos = t
            for c in cols:
                pos += c["gap"]
                pts.append((pos, c["x"]))
                if is_drum:
                    accent = any(abs(ax - c["x"]) <= 1.0 * s for ax in bar.accents)
                    for h in c["items"]:
                        vel = drumkit.VEL_GHOST if h.ghost else drumkit.VEL_ACCENT if accent else drumkit.VEL_NORMAL
                        notes.append(Note(start=pos, dur=min(c["dur"], 1.0) * 0.9,
                                          pitch=drumkit.note_for(drum_map, h.key), velocity=vel,
                                          staff=staff.index, bar=bar.number, drum=h.key))
                elif not c["items"]:
                    # tie continuation: extend whatever was sounding
                    for n in sounding.values():
                        if abs(n.start + n.dur - pos) < 1e-6:
                            n.dur += c["dur"]
                else:
                    # a strum arrow just left of the chord sets the stroke order
                    arrow = min((a for a in bar.strums if -2.2 * s <= a.x - c["x"] <= 0.2 * s),
                                key=lambda a: c["x"] - a.x, default=None)
                    order = sorted(c["items"], key=lambda f: f.string,
                                   reverse=arrow is not None and arrow.direction == "down")
                    new_sounding = {}
                    k = 0
                    for f in order:
                        pitch = tune[min(f.string, len(tune) - 1)] + f.fret + transpose
                        prev = sounding.get(f.string)
                        if f.tied and prev and prev.pitch == pitch and abs(prev.start + prev.dur - pos) < 1e-6:
                            prev.dur += c["dur"]
                            new_sounding[f.string] = prev
                            continue
                        off = k * strum_step if arrow else 0.0
                        k += 1
                        vel = velocity
                        if arrow is not None and arrow.direction == "up":
                            vel -= UP_VELOCITY_DROP
                        dur = c["dur"] - off
                        if f.dead:
                            vel, dur = DEAD_VELOCITY, min(dur, DEAD_BEATS)
                        n = Note(start=pos + off, dur=max(dur, 0.02), pitch=pitch, velocity=vel,
                                 staff=staff.index, bar=bar.number, string=f.string, fret=f.fret,
                                 strum=arrow.direction if arrow else "", dead=f.dead)
                        notes.append(n)
                        if not f.dead:
                            new_sounding[f.string] = n
                    sounding = new_sounding
                pos += c["dur"]
            if pos < t + bar_len - EPS:
                sounding = {}      # a rest ends any tie chain

            # grace notes: a short note just before the beat they lead into
            for gf in (f for f in bar.frets if f.grace):
                col_starts, p = [], t
                for c in cols:
                    p += c["gap"]
                    col_starts.append((c["x"], p))
                    p += c["dur"]
                nxt = [cs for cs in col_starts if cs[0] > gf.x]
                at = nxt[0][1] if nxt else t + bar_len
                pitch = tune[min(gf.string, len(tune) - 1)] + gf.fret + transpose
                notes.append(Note(start=max(0.0, at - GRACE_BEATS), dur=GRACE_BEATS * 0.9, pitch=pitch,
                                  velocity=int(velocity * 0.8), staff=staff.index, bar=bar.number,
                                  string=gf.string, fret=gf.fret, grace=True))

            pts.append((t + bar_len, bar.x1))
            bars.append(BarTiming(number=bar.number, staff=staff.index, start=t, length=bar_len,
                                  status=status, note=note))
            t += bar_len

    notes.sort(key=lambda n: (n.start, n.pitch))
    return Song(notes=notes, bars=bars, tempo=tempo, time_sig=(num, den), warnings=warns,
                anchors=anchors, kind=page.kind)
