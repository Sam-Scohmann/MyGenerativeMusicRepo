"""Choosing where on the neck each note is played.

A player's fretting hand sits in a *position*: the index finger over fret p,
one finger per fret over p..p+3, with a one-fret stretch either side. Notes
are found from there; the hand only shifts when a note is out of reach, and
then by the shortest move (the nearest copy of the note - often an octave
shape away on another string rather than up the same string).

A *voicing* places every note of a group (one note, or a chord) on its own
string. Costs (lower is better):
    shift        moving the position, more for longer and faster moves
    stretch      a note one fret outside the 4-fret window
    open string  cheap low on the neck, avoided high up
    high frets   a mild preference for lower positions
    crossing     melody notes jumping across strings
    gaps         a chord with silent strings between its notes (hard to strum)
    cut          a still-ringing note on a string the new voicing needs
"""
import itertools

SHIFT_COST = 1.5
SHIFT_DIST_COST = 0.35        # per fret
SHIFT_SPEED_COST = 0.05       # per fret per second of time available (fast jumps cost more)
STRETCH_COST = 0.8
OPEN_LOW_BONUS = -0.3         # open string while the hand is at fret <= 5
OPEN_HIGH_COST = 0.8          # open string while the hand is higher up
HIGH_FRET_COST = 0.04         # per fret of position
CROSS_COST = 0.3              # per string between consecutive melody notes
GAP_COST = 0.7                # per silent string inside a chord
CUT_COST = 2.5                # re-using a string that's still ringing another note
SAME_STRING_BONUS = -0.2      # melody staying on the same string for a step
MAX_VOICINGS = 14
CROWD_COST = 3.0              # per fretted note that would have to share a finger (no barre)
OCTAVE_COST = 1.5             # per note played an octave away from what was sent
TOP_OCTAVE_COST = 2.0         # extra when that note is the top (melody) note
MAX_OCTAVE_SHIFTS = 3         # notes moved at most, before dropping notes instead
WINDOW = 4                    # frets covered by index..pinkie


class Voicing:
    __slots__ = ("notes", "fmin", "fmax", "strings", "base", "source")

    def __init__(self, notes):
        self.notes = tuple(sorted(notes, key=lambda x: x[1]))   # (midi, string, fret) by string
        fretted = [f for _, _, f in self.notes if f > 0]
        self.fmin = min(fretted) if fretted else None
        self.fmax = max(fretted) if fretted else None
        self.strings = [s for _, s, _ in self.notes]
        self.base = 0.0
        self.source = {}  # played midi -> the midi that was sent, for notes moved an octave

    def sent(self, midi):
        return self.source.get(midi, midi)

    def positions(self, max_pos):
        """Hand positions (index fret) this voicing can be played from,
        core window first."""
        if self.fmin is None:
            return None  # all open: any position
        lo = max(1, self.fmax - (WINDOW - 1) - 1)    # allow a pinkie stretch
        hi = min(max_pos, self.fmin + 1)             # allow an index stretch
        return [p for p in range(lo, hi + 1)]

    def stretches(self, p):
        return sum(1 for _, _, f in self.notes if f > 0 and (f < p or f > p + WINDOW - 1))


def voicings(neck, midis, hints=None, limit=MAX_VOICINGS):
    """Ways to put `midis` on distinct strings, the hand spanning at most a
    stretched window. hints: {midi: string} to force strings (one MIDI
    channel per string)."""
    hints = hints or {}
    cands = []
    for m in sorted(set(midis)):
        pos = neck.positions(m)
        if m in hints:
            pos = [p for p in pos if p[0] == hints[m]] or pos
        if not pos:
            continue  # out of the guitar's range
        cands.append([(m, s, f) for s, f in pos])
    if not cands:
        return []
    out = []
    for combo in itertools.product(*cands):
        strings = [s for _, s, _ in combo]
        if len(set(strings)) != len(strings):
            continue
        fretted = [f for _, _, f in combo if f > 0]
        if fretted and max(fretted) - min(fretted) > WINDOW:
            continue
        v = Voicing(combo)
        # static cost: gaps inside the chord, open strings and height
        if len(strings) > 1:
            span = max(strings) - min(strings) + 1
            v.base += GAP_COST * (span - len(strings))
        v.base += HIGH_FRET_COST * (sum(fretted) / len(fretted) if fretted else 0)
        out.append(v)
    out.sort(key=lambda v: v.base)
    return out[:limit] if limit else out


def octave_voicings(neck, notes, hints, limit):
    """Voicings with as few notes as possible moved to their nearest octave
    (above or below), keeping the top note where it is if possible."""
    lo, hi = neck.note_range()
    for k in range(1, min(MAX_OCTAVE_SHIFTS, len(notes)) + 1):
        found = []
        for idxs in itertools.combinations(range(len(notes)), k):
            alts = [[m + d for d in (-12, 12) if lo <= m + d <= hi] for m in (notes[i] for i in idxs)]
            for choice in itertools.product(*alts):
                moved = list(notes)
                source = {}
                for i, m in zip(idxs, choice):
                    moved[i] = m
                    source[m] = notes[i]
                if len(set(moved)) < len(moved):
                    continue  # that octave is already in the chord
                extra = OCTAVE_COST * k + (TOP_OCTAVE_COST if (len(notes) - 1) in idxs else 0.0)
                for v in voicings(neck, moved, hints, limit):
                    v.base += extra
                    v.source = source
                    found.append(v)
        if found:
            found.sort(key=lambda v: v.base)
            return found[:limit] if limit else found
    return []


def playable(neck, midis, hints=None, limit=MAX_VOICINGS, octaves=True):
    """Voicings for the whole group. When no hand shape holds all of it (too
    many notes, or frets too far apart): with `octaves`, move the fewest
    notes to their nearest octave; otherwise (or if that fails) play the
    largest part that fits, keeping the top note (the melody) and the bottom
    note (the bass) first."""
    found = voicings(neck, midis, hints, limit)
    notes = sorted(set(midis))
    if found or len(notes) <= 1:
        return found
    if octaves:
        found = octave_voicings(neck, notes, hints, limit)
        if found:
            return found
    top, bottom = notes[-1], notes[0]
    for size in range(len(notes) - 1, 0, -1):
        best = []
        for sub in itertools.combinations(notes, size):
            rank = (top in sub) * 2 + (bottom in sub)
            for v in voicings(neck, sub, hints, limit):
                best.append((-rank, v.base, v))
        if best:
            best.sort(key=lambda x: (x[0], x[1]))
            keep = [v for r, b, v in best if r == best[0][0]]
            return keep[:limit] if limit else keep
    return []


def crowding(v, p):
    """Fretted notes that would have to share a finger at position p (a barre
    on the index finger's fret doesn't count: one finger holds those)."""
    fingers, barre = assign_fingers(v, p)
    used = [fg for (s, f), fg in fingers.items() if f > 0]
    shared = len(used) - len(set(used))
    if barre:
        shared -= sum(1 for fg in used if fg == 1) - 1
    return max(shared, 0)


def position_cost(v, p, prev_p, dt, max_pos):
    """Cost of playing voicing v from position p after being at prev_p."""
    cost = STRETCH_COST * v.stretches(p) + HIGH_FRET_COST * p + CROWD_COST * crowding(v, p)
    for _, _, f in v.notes:
        if f == 0:
            cost += OPEN_LOW_BONUS if p <= 5 else OPEN_HIGH_COST
    if prev_p is not None and p != prev_p:
        d = abs(p - prev_p)
        cost += SHIFT_COST + SHIFT_DIST_COST * d + SHIFT_SPEED_COST * d / max(dt, 0.05)
    return cost


def best_position(v, prev_p, dt, max_pos):
    options = v.positions(max_pos)
    if options is None:  # all open strings: stay
        return (prev_p or 1), position_cost(v, prev_p or 1, prev_p, dt, max_pos)
    return min(((p, position_cost(v, p, prev_p, dt, max_pos)) for p in options), key=lambda x: x[1])


def melody_cost(v, prev_strings):
    if len(v.notes) != 1 or not prev_strings:
        return 0.0
    s = v.notes[0][1]
    d = min(abs(s - ps) for ps in prev_strings)
    return SAME_STRING_BONUS if d == 0 else CROSS_COST * d


def ring_cost(v, ringing):
    """ringing: {string: midi} still sounding."""
    return sum(CUT_COST for m, s, f in v.notes if s in ringing and ringing[s] != m)


def assign_fingers(v, p):
    """{(string, fret): finger} with 1 index .. 4 pinkie (0 = open string),
    and the barre: (fret, lowest string, highest string) or None.
    Fingers follow frets (one per fret from p); several strings on the
    index's fret make a barre; other repeated frets take the next finger."""
    out, barre = {}, None
    fretted = sorted(((f, s) for _, s, f in v.notes if f > 0), key=lambda x: (x[0], x[1]))
    for _, s, f in v.notes:
        if f == 0:
            out[(s, f)] = 0
    at_index = [s for f, s in fretted if f == max(p, v.fmin or p) and f <= p]
    if len(at_index) >= 2:
        barre = (fretted[0][0], min(at_index), max(at_index))
    used = set()
    for f, s in fretted:
        finger = min(4, max(1, f - p + 1))
        if barre and f == barre[0]:
            finger = 1
        else:
            while finger in used and finger < 4:
                finger += 1
            if finger in used:  # all taken: share the nearest (rare - 5+ fretted notes)
                finger = 4
        used.add(finger)
        out[(s, f)] = finger
    return out, barre


# -----------------------------
# LIVE: one group at a time
# -----------------------------
def choose_live(neck, midis, state, now, hints=None, max_pos=None, octaves=True):
    """state: dict with 'p' (position), 't' (time of last group),
    'strings' (strings of the last group), 'ringing' {string: midi}.
    Returns (voicing, position, fingers, barre) or None."""
    max_pos = max_pos or neck.max_fret - 3
    best = None
    dt = now - state.get("t", -1e9)
    for v in playable(neck, midis, hints, octaves=octaves):
        p, pc = best_position(v, state.get("p"), dt, max_pos)
        cost = v.base + pc + melody_cost(v, state.get("strings")) + ring_cost(v, state.get("ringing", {}))
        if best is None or cost < best[0]:
            best = (cost, v, p)
    if best is None:
        return None
    _, v, p = best
    fingers, barre = assign_fingers(v, p)
    return v, p, fingers, barre


# -----------------------------
# WHOLE TAKE: Viterbi over voicings and positions
# -----------------------------
def choose_global(neck, groups, hints=None, max_pos=None, start_p=None, octaves=True):
    """groups: [(t, [midi], {midi: off time})]. Returns [(voicing, p, fingers, barre)]
    per group (None where nothing fits)."""
    max_pos = max_pos or neck.max_fret - 3
    layers = []   # per group: list of (cost, voicing, p, back index)
    prev = [(0.0, None, start_p, None)]
    for gi, (t, midis, offs) in enumerate(groups):
        vs = playable(neck, midis, hints, octaves=octaves)
        cur = []
        prev_t = groups[gi - 1][0] if gi else t - 1.0
        dt = t - prev_t
        for v in vs:
            options = v.positions(max_pos)
            for p in (options if options is not None else [None]):
                best = None
                for bi, (pc, pv, pp, _) in enumerate(prev):
                    q = p if p is not None else (pp or 1)
                    cost = pc + v.base + position_cost(v, q, pp, dt, max_pos)
                    if pv is not None:
                        cost += melody_cost(v, pv.strings)
                        # notes of the previous group still ringing
                        ringing = {s: m for m, s, f in pv.notes if groups[gi - 1][2].get(m, 0.0) > t + 1e-4}
                        cost += ring_cost(v, ringing)
                    if best is None or cost < best[0]:
                        best = (cost, v, q, bi)
                if best:
                    cur.append(best)
        if not cur:  # nothing playable (out of range): carry the previous state through
            cur = [(c, pv, pp, bi) for bi, (c, pv, pp, _) in enumerate(prev)]
        # keep the cheapest states only
        cur.sort(key=lambda x: x[0])
        cur = cur[:60]
        layers.append(cur)
        prev = cur
    out = [None] * len(groups)
    if not layers:
        return out
    i = 0  # cheapest final state (layers are sorted)
    for gi in range(len(layers) - 1, -1, -1):
        cost, v, p, back = layers[gi][i]
        if v is not None and set(v.sent(m) for m, _, _ in v.notes) <= set(groups[gi][1]):
            fingers, barre = assign_fingers(v, p)
            out[gi] = (v, p, fingers, barre)
        i = back if back is not None else 0
    return out
