"""Re-animate the hands from a finished take, with the whole performance known.

The live engine has to react to each note as it arrives. Here every note is
known up front, so:

1. Hand split   - which hand plays each note, chosen over the whole piece
                  (Viterbi over chord splits) so hands don't jump back and forth.
2. Fingering    - per hand, Viterbi over finger choices with lookahead: costs for
                  stretching, fast hand jumps, re-using a finger quickly on a new
                  key, awkward crossings and thumbs/pinkies on black keys.
3. Motion       - hands travel *before* each note and arrive just in time;
                  fingers hover over their next key, descend onto it at the
                  note-on and lift on release. The result replaces the take's
                  hand keyframes (keys and sound are untouched).
"""
import bisect
import itertools
import math
import time

import bpy
import numpy as np
from mathutils import Vector

from . import live, piano_sound
from .hands import (PINKIE, PINKIE_BLACK_COST, THUMB, THUMB_BLACK_COST, UNREACH_COST, Sway, WristSpring,
                    LOOSE_COST, REACH_IN_COST, SEP_COST, WRIST_HALF_LIFE, _cross_cost, _sway_limit, _yaw, hand_hop,
                    jump_lift, move_arc)

# fingering costs (distances in white keys)
SPREAD_COST = 1.0          # per finger at its full sideways spread (0 = relaxed spot)
RELEASE_COST = 6.0         # per held note whose finger lifts early so the hand can move
MIN_TRAVEL = 0.08          # seconds a hand needs at least to move to its next position
CUT_COST = 5.0             # a move that makes the previous finger lift before its note ends,
                           # per whole note lost (fast passages: keep the hand, spread instead)
DROP_COST = 12.0           # per chord note left unfingered (the chord is wider than the hand)
MOVE_COST = 2.0            # any hand movement (prefer reaching with another finger)
MOVE_DIST_COST = 0.25      # per white key
MOVE_SPEED_COST = 0.08     # per white key per second of available time (fast jumps cost more)
REUSE_COST = 2.0           # same finger, different key, less than REUSE_TIME apart
REUSE_TIME = 0.8
LEAD_COST = 1.0            # per finger step from the middle finger when the hand moves
NEAR_COST = 0.6            # per white key between the key and the finger's spot (hand before the note)
DIR_COST = 1.5             # next finger not on the side the melody moves
CROSS_COST = 2.5           # melodic step against finger order without moving the hand
THUMB_CROSS_COST = 0.8     # ... unless the thumb passes under / a finger over the thumb
SPAN_COST = 4.0            # hand split: per white key a hand's chord is wider than it can reach
BUSY_WINDOW = 1.0          # seconds a hand counts as "in place" after its last note
SPLIT_MOVE_COST = 0.15     # hand-split: per white key a hand's centre moves


def _smoothstep(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Note:
    __slots__ = ("on", "off", "midi", "vel", "key", "k", "hand", "finger")

    def __init__(self, on, off, midi, vel, key):
        self.on, self.off, self.midi, self.vel, self.key = on, off, midi, vel, key
        self.k = key.lateral
        self.hand = None
        self.finger = None


# -----------------------------
# 1. HAND SPLIT
# -----------------------------
def assign_hands(groups, engine, settings):
    hands = engine.hands
    if len(hands) == 1:
        for _, notes in groups:
            for n in notes:
                n.hand = hands[0]
        return
    bass, treble = hands
    w = engine.w
    seek = math.radians(settings.finger_seek)
    capacity = {h: (h.span[1] - h.span[0]) + 2 * min(f.reach_len * math.sin(seek) for f in h.fingers)
                for h in hands}
    home = {h: h.lateral + sum(h.span) / 2 for h in hands}
    min_d = settings.min_hand_distance * w

    def subset_cost(h, notes, last_center, last_time, t):
        if not notes:
            return 0.0, last_center, last_time
        ks = [n.k for n in notes]
        cost = sum(_cross_cost(n.midi, h is bass, settings) for n in notes)
        span = max(ks) - min(ks)
        if span > capacity[h]:
            cost += SPAN_COST * (span - capacity[h]) / w
        if len(notes) > 5:
            cost += 10.0 * (len(notes) - 5)
        center = sum(ks) / len(ks)
        d = abs(center - last_center) / w
        cost += SPLIT_MOVE_COST * d * (1.0 + 0.3 / max(t - last_time, 0.05))
        return cost, center, t

    # state: (cost, bass centre, treble centre, bass time, treble time, back index)
    layers = []
    prev = [(0.0, home[bass], home[treble], -1e9, -1e9, None)]
    for t, notes in groups:
        cur = []
        for split in range(len(notes) + 1):
            low, high = notes[:split], notes[split:]
            best = None
            for pi, (c, cb, ct, tb, tt, _) in enumerate(prev):
                cost_b, cb2, tb2 = subset_cost(bass, low, cb, tb, t)
                cost_t, ct2, tt2 = subset_cost(treble, high, ct, tt, t)
                total = c + cost_b + cost_t
                if t - tb2 < BUSY_WINDOW and t - tt2 < BUSY_WINDOW:
                    total += SEP_COST * max(0.0, min_d - (ct2 - cb2)) / w
                if best is None or total < best[0]:
                    best = (total, cb2, ct2, tb2, tt2, pi)
            cur.append(best + (split,))
        layers.append(cur)
        prev = [s[:6] for s in cur]

    i = min(range(len(layers[-1])), key=lambda j: layers[-1][j][0])
    for (t, notes), layer in zip(reversed(groups), reversed(layers)):
        state = layer[i]
        split = state[6]
        for n in notes[:split]:
            n.hand = bass
        for n in notes[split:]:
            n.hand = treble
        i = state[5]


# -----------------------------
# 2. FINGERING
# -----------------------------
class _State:
    __slots__ = ("cost", "fingers", "notes", "u", "depth", "t", "back")

    def __init__(self, cost, fingers, notes, u, t, back, depth=0.0):
        self.cost, self.fingers, self.notes, self.u, self.t, self.back = cost, fingers, notes, u, t, back
        self.depth = depth


def _pick_five(notes):
    """A hand can only finger five notes: keep the outer ones, spread the rest."""
    if len(notes) <= 5:
        return notes
    idx = sorted({round(i * (len(notes) - 1) / 4) for i in range(5)})
    return [notes[i] for i in idx]


def finger_hand(h, hand_groups, engine, settings, hand_lead=0.06, other=None):
    """hand_groups: [(t, notes sorted by pitch)] for this hand. Sets note.finger
    and returns [(t, u)] - the hand's lateral offset for each group.

    Fingers never stretch: a finger may only spread sideways from its relaxed
    spot by its tolerance (Finger Spread), so the hand is placed where every
    new note - and every note still held - is within that. When held notes
    can't come along the fingers lift early; a chord wider than the hand
    leaves its outer notes unfingered (their keys still play)."""
    w = engine.w
    fingers = sorted(h.fingers, key=lambda f: f.dx)
    seek = math.radians(settings.finger_seek)
    lead_finger = next((f for f in fingers if f.index == 2), None)  # re-animate leads with the middle finger

    def depth_of(notes):
        return engine.depth_for([n.key for n in notes], settings)

    def placement(pairs, depth, strict):
        return engine.placement([(f, n.key) for f, n in pairs], depth, settings, strict)

    def reachable(f, n, u, depth):
        win = engine.u_window(f, n.key, depth, settings)
        return win is not None and win[0] - 0.02 * w <= u <= win[1] + 0.02 * w

    def transition(p, combo, notes, t, keep_held, centred, strict):
        pairs = list(zip(combo, notes))
        held = [(f, n) for f, n in zip(p.fingers, p.notes) if n.off > t + 1e-4]
        if any(f in combo for f, _ in held):
            if keep_held:
                return None
            held = [(f, n) for f, n in held if f not in combo]
        released = 0
        base_depth = depth_of(list(notes) + [n for _, n in held] if keep_held else list(notes))
        if keep_held:
            if any((n.k - hn.k) * (f.dx - hf.dx) <= 0 for f, n in pairs for hf, hn in held):
                return None
            placed = placement(pairs + held, base_depth, strict)
        else:
            released = len(held)
            held = []
            placed = placement(pairs, base_depth, strict)
        if placed is None:
            return None
        (lo, hi), depth = placed
        # either move as little as possible, or settle with every finger as
        # relaxed as possible (better placed for what comes next)
        u = (lo + hi) / 2 if centred else _clamp(p.u, lo, hi)
        turns = {f: engine.turn(f, n.key, u, depth, settings) for f, n in pairs + held}
        if not engine.gaps_ok(h, turns, settings):
            return None

        dt = t - p.t
        cost = SPREAD_COST * sum(abs(turns[f]) / seek for f, _ in pairs)
        if other is not None:
            centre = other(t)
            if centre is not None:  # the other hand is busy here: keep Min Hand Distance
                gap = (centre - (h.lateral + u)) if h is engine.hands[0] else ((h.lateral + u) - centre)
                cost += SEP_COST * max(0.0, settings.min_hand_distance * w - gap) / w
        cost += RELEASE_COST * released + (0.0 if strict else LOOSE_COST * len(pairs))
        cost += REACH_IN_COST * (depth - base_depth) / w
        du = abs(u - p.u) / w
        if du > 1e-6:
            # the hand can't leave before the previous notes it would lose are
            # released, and must arrive `lead` before t: anything that doesn't
            # fit comes out of those notes (their fingers lift early)
            lead = min(hand_lead, 0.4 * dt)
            travel = max(MIN_TRAVEL, 0.08 + 0.03 * du)
            for pf, pn in zip(p.fingers, p.notes):
                if pf not in combo and not reachable(pf, pn, u, depth):
                    lost = (pn.off + travel) - (t - lead)
                    if lost > 0:
                        cost += CUT_COST * min(1.0, lost / max(pn.off - pn.on, 1e-3))
        if du > 1e-6:
            cost += MOVE_COST + MOVE_DIST_COST * du + MOVE_SPEED_COST * du / max(dt, 0.04)
        if dt < REUSE_TIME:
            prev_key = dict(zip(p.fingers, (n.midi for n in p.notes)))
            for f, n in pairs:
                if f in prev_key and prev_key[f] != n.midi:
                    cost += REUSE_COST * (REUSE_TIME - dt) / REUSE_TIME
        if len(notes) == 1 and len(p.notes) == 1 and du < 0.5 and dt < 0.6:
            a, b, fa, fb = notes[0], p.notes[0], combo[0], p.fingers[0]
            if (a.k - b.k) * (fa.dx - fb.dx) < 0:
                cost += THUMB_CROSS_COST if THUMB in (fa.index, fb.index) else CROSS_COST
            elif a.midi != b.midi and fa is not fb and dt < 0.6:
                # directional: the next finger over on the side the melody moves,
                # skipping fingers only for bigger intervals
                rank = {f: i for i, f in enumerate(fingers)}
                steps = abs(rank[fa] - rank[fb])
                wanted = max(1, round(abs(a.k - b.k) / (1.4 * w)))
                cost += DIR_COST * 0.5 * abs(steps - wanted)
        # nearest finger from where the hand was; land on the middle finger when moving
        for f, n in pairs:
            cost += NEAR_COST * abs(n.k - (p.u + f.dx)) / w
        if du > 1e-6 and len(pairs) == 1 and lead_finger is not None:
            rank = {f: i for i, f in enumerate(fingers)}
            cost += LEAD_COST * abs(rank[combo[0]] - rank[lead_finger])
        return p.cost + cost, u, depth

    def options(notes):
        """(notes fingered, fingers, penalty, strict) for every way to finger
        `notes` within reach - or, if no combination fits the hand, the
        largest subsets; and only if nothing fits the wrist limit, loosely."""
        for strict in (True, False):
            for size in range(len(notes), 0, -1):
                found = []
                for sub in itertools.combinations(notes, size):
                    depth = depth_of(list(sub))
                    for combo in itertools.combinations(fingers, size):
                        if placement(list(zip(combo, sub)), depth, strict) is None:
                            continue
                        pen = DROP_COST * (len(notes) - size)
                        for f, n in zip(combo, sub):
                            pen += UNREACH_COST * engine.shortfall(f, n.key, settings) / w
                            if n.key.black and f.index == THUMB:
                                pen += THUMB_BLACK_COST
                            elif n.key.black and f.index == PINKIE:
                                pen += PINKIE_BLACK_COST
                        found.append((sub, combo, pen, strict))
                if found:
                    return found
        return []

    layers = []
    prev = [_State(0.0, (), (), 0.0, -1e9, None)]
    for t, notes in hand_groups:
        notes = _pick_five(notes)
        cur = []
        choices = options(notes)
        for keep_held in (True, False):  # lift held fingers only if nothing else works
            for sub, combo, pen, strict in choices:
                for centred in (False, True):
                    best = None
                    for pi, p in enumerate(prev):
                        r = transition(p, combo, sub, t, keep_held, centred, strict)
                        if r and (best is None or r[0] < best[0]):
                            best = (r[0], r[1], pi, r[2])
                    if best:
                        cur.append(_State(best[0] + pen, combo, tuple(sub), best[1], t, best[2], best[3]))
            if cur:
                break
        if not cur:  # can't happen with a sane rig; keep the hand where it is
            p = min(prev, key=lambda s: s.cost)
            cur.append(_State(p.cost, (), (), p.u, t, prev.index(p), p.depth))
        layers.append(cur)
        prev = cur

    plan = []
    state = min(layers[-1], key=lambda s: s.cost)
    for j in range(len(layers) - 1, -1, -1):
        for f, n in zip(state.fingers, state.notes):
            n.finger = f
        plan.append((state.t, state.u, state.depth))
        if j > 0:
            state = layers[j - 1][state.back]
    plan.reverse()
    return plan


def finger_presses(h, hand_groups, segs, settings, stats=None, engine=None):
    """{finger: [(on, off, key)]} with each press ending early when the finger
    is needed for another note, or when the hand leaves for a position where
    this key would be out of the finger's reach (no stretching to hold it).
    Counts into stats: 'unfingered' notes and presses 'lifted' early."""
    presses = {f: [] for f in h.fingers}
    for _, ns in hand_groups:
        for n in ns:
            if n.finger is not None:
                presses[n.finger].append([n.on, n.off, n.key, n.k, n.off])
            elif stats is not None:
                stats["unfingered"] = stats.get("unfingered", 0) + 1
    for f, items in presses.items():
        items.sort(key=lambda p: p[0])
        for i, p in enumerate(items):
            if i + 1 < len(items):
                p[1] = min(p[1], items[i + 1][0] - 0.03)
            for t0, _t1, _a, b in segs:
                if p[0] < t0 < p[1] and not _reaches(engine, f, p[2], b, settings):
                    p[1] = t0
                    break
            if stats is not None and p[1] < p[0] + 0.5 * (p[4] - p[0]):  # lost over half its length
                stats["lifted"] = stats.get("lifted", 0) + 1
            p[1] = max(p[1], p[0] + 0.02)
    return {f: [(p[0], p[1], p[2]) for p in items] for f, items in presses.items()}


def _reaches(engine, f, key, hand, settings):
    """Can finger f press key with the hand at hand = (u, depth)?"""
    win = engine.u_window(f, key, hand[1], settings)
    slack = 0.02 * engine.w
    return win is not None and win[0] - slack <= hand[0] <= win[1] + slack


# -----------------------------
# 3. MOTION
# -----------------------------
def _gaussian(values, sigma_frames):
    """Zero-phase smoothing of an (N, D) array - it anticipates as much as it lags."""
    if sigma_frames <= 0 or len(values) < 3:
        return values
    radius = int(3 * sigma_frames) + 1
    x = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (x / sigma_frames) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(values, ((radius, radius), (0, 0)), mode="edge")
    return np.stack([np.convolve(padded[:, d], kernel, mode="valid") for d in range(values.shape[1])], axis=1)


def _segments(plan, depth_of, last_off, opts, w, fingered=(), engine=None, settings=None):
    """Piecewise motion of (u, depth): hold, then ease to the next group's
    position, arriving `hand_lead` before its notes. The hand doesn't leave
    while a held note would end up out of its finger's reach - it waits for
    the release (keeping at least MIN_TRAVEL to get there).
    fingered: (on, off, finger, key) of every fingered note."""
    segs = []  # (t0, t1, from (u, d), to (u, d))
    cur = (0.0, 0.0)
    prev_t = None
    for (t, u, depth), _unused in zip(plan, depth_of):
        target = (u, depth)
        if target != cur:
            gap = (t - prev_t) if prev_t is not None else 1.0
            arrive = t - min(opts.hand_lead, 0.4 * gap)
            travel = _clamp(0.08 + 0.03 * abs(target[0] - cur[0]) / w, 0.05, max(0.05, 0.7 * gap))
            earliest = prev_t + min(0.05, 0.3 * gap) if prev_t is not None else -1e9
            depart = max(earliest, arrive - travel)
            held_until = max((off for on, off, f, key in fingered
                              if on < t and off > depart and not _reaches(engine, f, key, target, settings)),
                             default=None)
            if held_until is not None:
                depart = max(depart, min(held_until, arrive - MIN_TRAVEL))
            if depart >= arrive:
                depart = arrive - 0.02
            segs.append((depart, arrive, cur, target))
            cur = target
        prev_t = t
    home_at = last_off + opts.home_after
    segs.append((home_at, home_at + 0.6, cur, (0.0, 0.0)))
    return segs


def _eval_segments(segs, t):
    value = segs[0][2] if segs else (0.0, 0.0)
    for t0, t1, a, b in segs:
        if t < t0:
            break
        if t >= t1:
            value = b
        else:
            s = _smoothstep((t - t0) / (t1 - t0))
            value = (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s)
            break
    return value


class _FingerTrack:
    """Press timeline of one finger and its position at any time."""

    def __init__(self, presses):
        self.presses = sorted(presses, key=lambda p: p[0])  # (on, off, key)
        self.i = 0

    def idle(self, t, opts):
        """Not pressing, reaching for a key or lifting off one."""
        return not any(on - opts.approach <= t < off + opts.lift for on, off, _ in self.presses)

    def position(self, t, rest, touch, up, opts, w):
        presses = self.presses
        while self.i < len(presses) and presses[self.i][1] <= t:
            self.i += 1
        i = self.i
        press_down = up * (opts.press_depth * w)
        if i < len(presses) and presses[i][0] <= t:
            return touch(presses[i][2]) - press_down

        prev = presses[i - 1] if i > 0 else None
        nxt = presses[i] if i < len(presses) else None
        pos = rest
        if nxt:
            free_from = prev[1] if prev else -1e9
            approach = min(opts.approach, nxt[0] - free_from)
            descend = min(opts.descend, 0.5 * approach)
            target = touch(nxt[2])
            hover = target + up * (opts.hover * w)
            remaining = nxt[0] - t
            if approach > 0:
                pos = rest.lerp(hover, _smoothstep(1.0 - remaining / approach))
            if remaining < descend:
                pos = hover.lerp(target - press_down, _smoothstep(1.0 - remaining / descend))
        if prev:
            lift = opts.lift if not nxt else min(opts.lift, 0.5 * (nxt[0] - prev[1]))
            since = t - prev[1]
            if since < lift:
                pos = (touch(prev[2]) - press_down).lerp(pos, _smoothstep(since / lift))
        return pos


def _write_fcurve(fc, frames, values, f0, f1):
    keep = [(p.co[0], p.co[1]) for p in fc.keyframe_points if p.co[0] < f0 - 0.5 or p.co[0] > f1 + 0.5]
    points = sorted(keep + list(zip(frames, values)))
    fc.keyframe_points.clear()
    fc.keyframe_points.add(len(points))
    fc.keyframe_points.foreach_set("co", [c for p in points for c in p])
    fc.keyframe_points.foreach_set("interpolation", [1] * len(points))  # LINEAR
    fc.update()


# -----------------------------
# DRIVER
# -----------------------------
def rethink(context, opts, progress=None):
    """Re-animates the hands of the recorded take. Returns a report string."""
    scene = context.scene
    settings = scene.piano_midi
    if live.is_running():
        raise RuntimeError("Stop the live session first")
    fps = scene.render.fps / scene.render.fps_base
    started = time.perf_counter()

    # mutes the take, applies the rest pose, builds keymap + engine at rest
    session = live.Session(scene, settings, rest_from_keys=True)
    try:
        engine = session.engine
        if not engine:
            raise RuntimeError("No hands armature (hand_control_l / _r bones) found")
        raw = piano_sound.extract_events(session.main, settings.octave_offset, fps)
        notes = [Note(on / fps, off / fps, m, v, session.keymap.get(m))
                 for on, off, m, v in raw if session.keymap.get(m)]
        if not notes:
            raise RuntimeError("No recorded notes in the take")

        # chords: notes starting on the same frame
        groups = []
        for n in sorted(notes, key=lambda n: (n.on, n.midi)):
            if groups and abs(groups[-1][0] - n.on) < 0.5 / fps:
                groups[-1][1].append(n)
            else:
                groups.append((n.on, [n]))

        assign_hands(groups, engine, settings)

        w = engine.w
        motion = {}
        stats = {}
        hand_groups_of = {}
        for h in engine.hands:
            hg = [(t, [n for n in ns if n.hand is h]) for t, ns in groups]
            hand_groups_of[h] = [(t, ns) for t, ns in hg if ns]

        def busy_centre(h, plan):
            """Where hand h's hand_control sits at time t while it's busy
            (playing within BUSY_WINDOW), else None."""
            spans = [(t, max(n.off for n in ns)) for t, ns in hand_groups_of[h]]

            def at(t):
                i = bisect.bisect_right([p[0] for p in plan], t) - 1
                if i < 0 or t > spans[i][1] + BUSY_WINDOW:
                    return None
                return h.lateral + plan[i][1]
            return at

        # plan each hand, then re-plan each against the other's plan
        plans = {}
        for round_ in range(2):
            for h in engine.hands:
                if not hand_groups_of[h]:
                    continue
                others = [o for o in engine.hands if o is not h and o in plans]
                other = busy_centre(others[0], plans[others[0]]) if others else None
                plans[h] = finger_hand(h, hand_groups_of[h], engine, settings, opts.hand_lead, other)

        for h in engine.hands:
            hand_groups = hand_groups_of[h]
            if not hand_groups:
                motion[h] = None
                continue
            plan = plans[h]
            depth = [d for _, _, d in plan]  # planned slide towards the back (black keys + reach-in)
            last_off = max(n.off for _, ns in hand_groups for n in ns)
            fingered = [(n.on, n.off, n.finger, n.key)
                        for _, ns in hand_groups for n in ns if n.finger is not None]
            segs = _segments(plan, depth, last_off, opts, w, fingered, engine, settings)
            tracks = {f: _FingerTrack(ps) for f, ps in finger_presses(h, hand_groups, segs, settings, stats, engine).items()}
            motion[h] = (segs, tracks, [(n.on, n.off) for _, ns in hand_groups for n in ns])

        first = min(n.on for n in notes)
        last = max(n.off for n in notes)
        f0 = max(int(scene.frame_start), int(math.floor((first - 1.0) * fps)))
        f1 = int(math.ceil((last + opts.home_after + 0.8) * fps))
        frames = list(range(f0, f1 + 1))
        times = [f / fps for f in frames]

        # hand_control path: planned sideways travel + horizontal slide for black
        # keys, then a forward-only reach fix so pressed fingers stay within the
        # rig's wrist limits. It never changes height.
        def press_at(fg):
            """Key surface + fingertip pad (the finger track adds Press Depth itself)."""
            return lambda key: engine.to_arm @ key.touch_world() + engine.up * engine.pad(fg, settings)

        hand_path, wrist_dz, busy = {}, {}, {}
        for h, m in motion.items():
            if m is None:
                hand_path[h] = np.tile(np.array(h.home), (len(frames), 1))
                wrist_dz[h] = np.zeros(len(frames))
                busy[h] = np.zeros(len(frames), dtype=bool)
                continue
            segs, tracks, spans = m
            base = np.zeros((len(frames), 3))
            for i, t in enumerate(times):
                u, d = _eval_segments(segs, t)
                base[i] = h.home + engine.axis * u + engine.forward * d
            pressed_at = []
            for t in times:
                pressed_at.append({f: engine.press_target(f, p[2], settings) for f, tr in tracks.items()
                                   for p in tr.presses if p[0] <= t < p[1]})
            # moves: the Hand Jump hop (only while no key is held) and the
            # Move Lift arc (every move); applied after the reach fix below
            hop = np.zeros(len(frames))
            move = np.zeros(len(frames))
            for i, t in enumerate(times):
                for t0, t1, a, b in segs:
                    if t0 <= t < t1 and t1 > t0:
                        total = abs(b[0] - a[0])
                        remaining = total * (1.0 - _smoothstep((t - t0) / (t1 - t0)))
                        hop[i] = hand_hop(total, remaining, settings, w)
                        move[i] = move_arc(total, remaining, settings, w)
                        break
            free = np.array([[0.0 if pressed else 1.0] for pressed in pressed_at])
            free = np.minimum(free, _gaussian(free, 0.03 * fps))[:, 0]
            path = base
            for sigma in (0.08, 0.03, 0.0):
                fix = np.zeros((len(frames), 3))
                for i, pressed in enumerate(pressed_at):
                    if pressed:
                        fix[i] = engine._reach_fix(h, Vector(path[i]), pressed)
                path = path + _gaussian(fix, sigma * fps)

            # world-Z lift of hand_control: every move, the hop, reach-in and
            # playing; smoothed, then cut back wherever a pressing finger
            # couldn't reach its key from the raised hand
            activity = np.array([[len(p) / max(len(h.fingers), 1)] for p in pressed_at])
            activity = _gaussian(activity, WRIST_HALF_LIFE * fps)[:, 0]
            depth_now = (path - np.array(h.home)) @ np.array(engine.forward)
            raw = np.array([engine.lift_target(h, move[i], hop[i] * free[i], depth_now[i], activity[i],
                                               times[i], settings) for i in range(len(frames))])
            allowed = np.array([engine.lift_allowed(h, Vector(path[i]), pressed_at[i], settings)
                                for i in range(len(frames))])
            allowed = np.minimum(allowed, settings.max_hand_lift * w)
            lift = np.minimum(_gaussian(np.minimum(raw, allowed)[:, None], 0.04 * fps)[:, 0], allowed)
            path = path + np.outer(lift, np.array(engine.up))
            hand_path[h] = path
            busy[h] = np.array([bool(p) for p in pressed_at])

            # wrist height: damped average of how far the fingers are up/down from
            # their relaxed spots (planned motion, before the palm is known)
            offsets = np.zeros((len(frames), 1))
            for i, t in enumerate(times):
                hand_pos = Vector(path[i])
                total = 0.0
                for fg in h.fingers:
                    relaxed = hand_pos + fg.rest_offset
                    pos = tracks[fg].position(t, relaxed, press_at(fg), engine.up, opts, w)
                    total += (pos - relaxed).dot(engine.up)
                offsets[i, 0] = total / len(h.fingers)
            for tr in tracks.values():
                tr.i = 0  # rewind for the baking pass
            offsets = _gaussian(offsets, 0.12 * fps)[:, 0]
            # extra dip where a pressing finger can't reach its key; eased in
            # around the strike but never less than needed while it's pressed
            need = np.zeros((len(frames), 1))
            for i, t in enumerate(times):
                need[i, 0] = max((engine.shortfall(f, p[2], settings) for f, tr in tracks.items()
                                  for p in tr.presses if p[0] - opts.descend <= t < p[1]), default=0.0)
            need = np.maximum(need, _gaussian(need, 0.06 * fps))[:, 0]
            # wrist lift arcing over long hand moves
            jumps = np.zeros(len(frames))
            for i, t in enumerate(times):
                for t0, t1, a, b in segs:
                    if t0 <= t < t1 and t1 > t0:
                        total = abs(b[0] - a[0])
                        done = _smoothstep((t - t0) / (t1 - t0))
                        jumps[i] = jump_lift(total, total * (1.0 - done), settings, w)
                        break
            # target height, then the wrist's own inertia, then its limits
            spring = WristSpring()
            dzs = []
            for i in range(len(frames)):
                target = engine.wrist_height(h, Vector(path[i]), offsets[i], times[i], settings, need[i], jumps[i])
                dz = spring.step(1.0 / fps, target, settings.wrist_inertia, settings.wrist_inertia_max * w)
                dzs.append(engine.limit_wrist(h, dz, settings, need[i]))
            wrist_dz[h] = np.array(dzs)

        # idle fingers dangle sideways with the hand's acceleration
        sway = {}
        for h in engine.hands:
            lateral = hand_path[h] @ np.array(engine.axis)
            vel = np.gradient(lateral, 1.0 / fps)
            accel = np.gradient(vel, 1.0 / fps)
            for fg in h.fingers:
                sim = Sway(fg.sway.omega / (2 * math.pi), _sway_limit(engine.arm, fg.pb.name))
                sway[fg] = [sim.step(1.0 / fps, a, fg.spread_len, settings.finger_sway) for a in accel]

        # an idle hand moves aside so the hands keep Min Hand Distance
        if len(engine.hands) == 2 and all(m is not None for m in motion.values()):
            bass, treble = engine.hands
            ax = np.array(engine.axis)
            gap = hand_path[treble] @ ax - hand_path[bass] @ ax
            short = np.maximum(0.0, settings.min_hand_distance * w - gap)
            idle_b, idle_t = ~busy[bass], ~busy[treble]
            # who moves: the idle one; both idle -> half each; both busy -> nobody
            share_t = np.where(idle_t & idle_b, 0.5, np.where(idle_t, 1.0, 0.0))
            share_b = np.where(idle_t & idle_b, 0.5, np.where(idle_b & ~idle_t, 1.0, 0.0))
            for h, share, sign in ((treble, share_t, 1.0), (bass, share_b, -1.0)):
                shift = _gaussian((short * share)[:, None], 0.08 * fps)[:, 0]
                shift = np.where(busy[h], 0.0, shift)  # a pressing hand never gets pushed
                shift = _gaussian(shift[:, None], 0.03 * fps)[:, 0] * (~busy[h])
                hand_path[h] = hand_path[h] + np.outer(sign * shift, ax)
            gap = hand_path[treble] @ ax - hand_path[bass] @ ax
            stats["too_close"] = int(np.sum(gap < settings.min_hand_distance * w - 0.05 * w))

        # take: keys play (note properties unmuted), hand bones stay muted
        bone_paths = {f'pose.bones["{pb.name}"].location' for pb in engine.controlled_bones()}
        for key, (fc, was_muted) in list(session.muted.items()):
            if fc.data_path not in bone_paths:
                fc.mute = was_muted
                del session.muted[key]

        baked = {pb.name: [] for pb in engine.controlled_bones()}
        orig_frame = scene.frame_current
        view_layer = context.view_layer
        for i, f in enumerate(frames):
            scene.frame_set(f)
            for h in engine.hands:
                engine.write_hand(h, Vector(hand_path[h][i]), wrist_dz[h][i])
            view_layer.update()  # palms follow the arms' IK
            for h, m in motion.items():
                hand_pos = Vector(hand_path[h][i])
                t = times[i]
                idle = {}
                for fg in h.fingers:
                    rest = fg.palm.matrix @ fg.rest_in_palm if fg.palm is not None else hand_pos + fg.rest_offset
                    rest = rest + engine.axis * sway[fg][i]
                    if m is None:
                        fg.pos = rest
                        idle[fg] = True
                    else:
                        pos = m[1][fg].position(t, rest, press_at(fg), engine.up, opts, w)
                        fg.pos, _ = engine.clamp_seek(fg, hand_pos, pos, settings)
                        idle[fg] = m[1][fg].idle(t, opts)
                # idle fingers turn aside to make room for pressing neighbours
                turns = {fg: _yaw(fg.pos - (hand_pos + fg.knuckle), engine.forward, engine.axis) - fg.yaw0
                         for fg in h.fingers if not idle[fg]}
                if turns:
                    room = engine.idle_turns(h, turns, settings)
                    for fg in h.fingers:
                        if idle[fg]:
                            fg.pos = engine.turn_about_knuckle(fg, hand_pos, fg.pos, room[fg])
                engine._write(h, hand_pos, wrist_dz[h][i])
            for pb in engine.controlled_bones():
                baked[pb.name].append(pb.location.copy())
            if progress and i % 25 == 0:
                progress(i / len(frames))

        # replace the take's hand keys in [f0, f1]
        for pb in engine.controlled_bones():
            pb.keyframe_insert("location", frame=f0, group=pb.name)  # make sure the F-curves exist
        curves = {(fc.data_path, fc.array_index): fc for fc in live._fcurves(engine.arm)}
        for pb in engine.controlled_bones():
            for axis in range(3):
                fc = curves[(f'pose.bones["{pb.name}"].location', axis)]
                _write_fcurve(fc, frames, [v[axis] for v in baked[pb.name]], f0, f1)
    finally:
        session._unmute_takes()

    scene.frame_set(orig_frame)
    per_hand = ", ".join(f"{sum(1 for n in notes if n.hand is h)} {'bass' if h is engine.hands[0] else 'treble'}"
                         for h in engine.hands)
    extra = ""
    if stats.get("unfingered"):
        extra += f"; {stats['unfingered']} chord note(s) wider than a hand left unfingered"
    if stats.get("too_close"):
        extra += f"; hands closer than Min Hand Distance on {stats['too_close']} frames (both playing)"
    if stats.get("lifted"):
        extra += f"; {stats['lifted']} held note(s) released early by the finger"
    return (f"Re-animated {len(notes)} notes ({per_hand}) over frames {f0}-{f1} "
            f"in {time.perf_counter() - started:.1f}s{extra}")
