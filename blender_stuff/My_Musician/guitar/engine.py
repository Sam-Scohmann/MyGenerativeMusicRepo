"""Moves the guitarist: the fretting hand, the picking hand and the strings.

Hands (HandsArmature, suffixes _l / _r):
    strumming hand  the hand_control nearer the bridge; it carries the pick
                    (an object parented to one of its bones, e.g. PluckTrum on
                    thumb_control). Only hand_control moves; the pick follows.
    fretting hand   the other one; hand_control follows the neck to the
                    current position, finger IK controls press fret points.
Both hands keep their pose relative to the neck, so a guitar animated at its
root carries them along (position and orientation).

Strings (from the Guitar setup):
    FRET_f_STRING_s_CTRL     pushed towards the fretboard while pressed at f
    VIBRATE_i_STRING_s_CTRL  ring across the strings after a pluck, decaying

All motion is computed in the neck frame (see neck.py): u along the strings,
v across them, n out of the fretboard towards the hands.
"""
import math

import bpy
import numpy as np
from mathutils import Matrix, Vector

FINGERS = ("index", "mid", "ring_f", "pinkie")   # finger 1..4 of the fretting hand
THUMB = "thumb"
HAND = "hand_control_{}"
ELBOW = "elbow_controller_{}"
CONTROL = "{}_control_{}"
VIS_HZ = 9.0          # visible string vibration
PRE_ROLL = 0.035      # live: seconds from a MIDI note to the pick reaching the first string
OVERSHOOT = 0.6       # string spacings the pick travels past the last string of a stroke


def _smooth(cur, target, half_life, dt):
    if half_life <= 0.0:
        return target.copy() if isinstance(target, Vector) else target
    return cur + (target - cur) * (1.0 - 0.5 ** (dt / half_life))


def pose_matrix(pb):
    """Armature-space pose matrix of a bone from rest + basis (constraints ignored)."""
    m = pb.bone.matrix_local @ pb.matrix_basis
    if pb.parent is None:
        return m
    parent = pb.parent
    return pose_matrix(parent) @ parent.bone.matrix_local.inverted() @ m


def _smoothstep(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class Stroke:
    """One pass of the pick across strings (a pluck, a strum, or a miss)."""

    def __init__(self, t_hit, v_from, v_to, duration, dig, muted, hits):
        self.t_hit = t_hit            # pick at v_from (first contact) at this time
        self.v_from, self.v_to = v_from, v_to
        self.duration = duration
        self.dig = dig                # False: a miss (hand swings above the strings)
        self.muted = muted
        self.hits = hits              # [(time, string, midi, vel, fret)]
        self.down = v_to > v_from

    @property
    def end(self):
        return self.t_hit + self.duration


class Hand:
    def __init__(self, engine, side):
        arm = engine.arm
        self.side = side
        self.pb = arm.pose.bones[HAND.format(side)]
        self.start_basis = self.pb.matrix_basis.copy()
        rest = self.pb.bone.matrix_local
        pose = rest @ self.pb.matrix_basis
        # the hand's pose relative to the neck, kept for the whole session
        self.in_neck = engine.neck.frame_inv @ engine.arm_world @ pose
        self.head_local = self.in_neck.translation.copy()
        self.offset = Vector()       # neck-frame translation from rest
        self.controls = {}
        for name in FINGERS + (THUMB,):
            pb = arm.pose.bones.get(CONTROL.format(name, side))
            if pb is not None:
                self.controls[name] = pb
        self.start_locs = {n: pb.location.copy() for n, pb in self.controls.items()}

    def write(self, engine):
        """hand_control = its rest pose relative to the (current) neck, moved by offset."""
        m = engine.arm_world_inv @ engine.neck.frame @ Matrix.Translation(self.offset) @ self.in_neck
        self.pb.matrix_basis = self.pb.bone.matrix_local.inverted() @ m

    def control_pose_parent(self, pb):
        rest = self.pb.bone.matrix_local
        return rest @ self.pb.matrix_basis @ rest.inverted() @ pb.bone.matrix_local

    def restore(self):
        self.pb.matrix_basis = self.start_basis
        for n, pb in self.controls.items():
            pb.location = self.start_locs[n]


class GuitarEngine:
    def __init__(self, neck, arm, settings):
        self.neck = neck
        self.arm = arm
        self.settings = settings
        self.arm_world = arm.matrix_world.copy()
        self.arm_world_inv = self.arm_world.inverted()
        neck.update()
        sides = [s for s in ("l", "r") if HAND.format(s) in arm.pose.bones]
        if len(sides) < 2:
            raise RuntimeError(f"'{arm.name}' needs hand_control_l and hand_control_r bones")
        hands = {s: Hand(self, s) for s in sides}
        # the hand nearer the bridge strums
        strum_side = max(sides, key=lambda s: hands[s].head_local.x)
        self.strum = hands[strum_side]
        self.fret = hands["r" if strum_side == "l" else "l"]
        self.sp = neck.spacing
        self._setup_fret_hand()
        self._setup_pick()
        self._setup_strings()
        self.now = 0.0
        self.strokes = []
        self.on_strike = None        # callback(time, string, midi, vel, fret, dir, muted)
        self.dirty_strings = set()

    # -----------------------------
    # SETUP
    # -----------------------------
    def _local_of_control(self, hand, name):
        pb = hand.controls[name]
        m = hand.control_pose_parent(pb) @ pb.matrix_basis
        return self.neck.frame_inv @ (self.arm_world @ m.translation)

    def _setup_fret_hand(self):
        h = self.fret
        self.finger_rest = {n: self._local_of_control(h, n) for n in h.controls}
        self.finger_pos = dict(self.finger_rest)
        # which side of the strings the fingers are on
        side = sum(p.z for p in self.finger_rest.values())
        self.n_side = 1.0 if side >= 0 else -1.0
        # rest position: the fret space the index finger sits over
        idx = self.finger_rest.get("index", h.head_local)
        s0 = self.neck.numbers[len(self.neck.numbers) // 2]
        p0 = 1
        for f in range(1, self.neck.max_fret + 1):
            if self.neck.local(self.neck.wire(s0, f)).x >= idx.x:
                p0 = f
                break
        self.p0 = p0
        self.p = float(p0)
        self.p_target = p0
        self.pressing = {}       # finger name -> (string, fret, midi, tag)
        # barres: the index finger lies straight across the fret, its IK target
        # past the edge string on the side away from the palm (the wrist side
        # is where the hand comes from)
        wrist = self.arm.pose.bones.get(f"wrist_{h.side}")
        palm_v = (self.neck.frame_inv @ (self.arm_world @ pose_matrix(wrist).translation)).y \
            if wrist is not None else h.head_local.y
        vs = {s: self.neck.string_v(s) for s in self.neck.numbers}
        mid_v = sum(vs.values()) / len(vs)
        if palm_v >= mid_v:   # palm on the high-v side: the far edge is the lowest-v string
            self.barre_edge = min(vs, key=vs.get)
            self.barre_out = -1.0
        else:
            self.barre_edge = max(vs, key=vs.get)
            self.barre_out = 1.0
        # the fretting arm's elbow follows the hand along the neck
        self.elbow = self.arm.pose.bones.get(ELBOW.format(h.side))
        if self.elbow is not None:
            self.elbow_start = self.elbow.location.copy()
            self.elbow_rest = self.neck.frame_inv @ (self.arm_world @ pose_matrix(self.elbow).translation)
        heights = {s: self.neck.wire(s, 0).z for s in self.neck.numbers}
        top, bottom = max(heights.values()), min(heights.values())
        self.lowness = {s: (top - z) / (top - bottom) if top > bottom else 0.5 for s, z in heights.items()}
        self.low = 0.0              # smoothed 0 (highest string) .. 1 (lowest string)
        self.along = Vector()        # hand offset along the neck only (the elbow follows this)
        self.barre = None
        self.fret_dirty = set()

    def _setup_pick(self):
        """Pick tip: the vertex of the pick deepest towards the strings."""
        h = self.strum
        bones = set(h.controls[n].name for n in h.controls) | {h.pb.name}
        picks = [o for o in bpy.data.objects
                 if o.parent == self.arm and o.parent_type == 'BONE' and o.parent_bone in bones and o.type == 'MESH']
        tip = None
        if picks:
            pick = max(picks, key=lambda o: o.dimensions.length)
            ev = pick.evaluated_get(self.neck.depsgraph)
            me = ev.to_mesh()
            pts = [self.neck.local(ev.matrix_world @ v.co) for v in me.vertices]
            ev.to_mesh_clear()
            if pts:
                tip = min(pts, key=lambda p: p.z * self.n_side)
            self.pick_name = pick.name
        if tip is None:  # no pick: the thumb tip strums
            tip = self._local_of_control(h, THUMB) if THUMB in h.controls else h.head_local.copy()
            self.pick_name = None
        self.tip_rest = tip
        self.tip = tip.copy()
        vs = [self.neck.string_v(s) for s in self.neck.numbers]
        self.v_min, self.v_max = min(vs), max(vs)
        self.pick_dir = 1          # next single-note pick: 1 down, -1 up
        self.last_pick_t = -1e9
        self.last_strum = (-1e9, -1)

    def _setup_strings(self):
        n = self.neck
        self.string_state = {s: {"fret": 0, "t": -1e9, "amp": 0.0, "damp_t": None, "midi": None}
                             for s in n.numbers}
        self.rest_loc = {}
        for s in n.numbers:
            for o in list(n.frets[s].values()) + n.vibrate.get(s, []):
                self.rest_loc[o.name] = o.location.copy()

    # -----------------------------
    # PERFORMANCE INPUT
    # -----------------------------
    def fret_chord(self, voicing, p, fingers, barre, tag=None):
        """Fretting hand: go to position p and hold this voicing."""
        self.p_target = p
        self.barre = barre
        self.pressing = {}
        names = {1: "index", 2: "mid", 3: "ring_f", 4: "pinkie"}
        midi_at = {(s, f): m for m, s, f in voicing.notes}
        for (s, f), finger in fingers.items():
            if finger:
                self.pressing[names[finger]] = (s, f, midi_at.get((s, f)), tag)
            self.string_state[s]["fret"] = f
        for name in list(self.pressing):
            if name not in self.fret.controls:
                del self.pressing[name]

    def release(self, string, now, midi=None, tag=None):
        """The note on this string ends: its finger lifts, the string is damped.
        With `midi` (and `tag`, the chord it belonged to), only that note's
        finger lifts - a new note already fretted on the string keeps its finger."""
        st = self.string_state.get(string)
        if st is None:
            return
        for name, (s, f, m, g) in list(self.pressing.items()):
            if s == string and (midi is None or m is None or m == midi) and (tag is None or g == tag):
                del self.pressing[name]
        if midi is not None and any(s == string for s, f, m, g in self.pressing.values()):
            return  # the string already belongs to the next note
        if self.barre and self.barre[1] <= string <= self.barre[2]:
            if not any(f == self.barre[0] for _, f, _, _ in self.pressing.values()):
                self.barre = None
        st["fret"] = 0
        st["midi"] = None
        st["damp_t"] = now

    def pluck(self, string, midi, vel, fret, now, direction=None, at=None):
        """Single-note pick stroke across one string (alternate picking)."""
        if now - self.last_pick_t > 0.5:
            self.pick_dir = 1
        d = direction if direction is not None else self.pick_dir
        v = self.neck.string_v(string)
        half = 0.5 * self.sp
        dur = self.settings.pick_stroke
        t_hit = (at if at is not None else now + PRE_ROLL) - dur * 0.5
        self.add_stroke(Stroke(t_hit, v - half * d, v + half * d, dur, True, False,
                               [(t_hit + dur * 0.5, string, midi, vel, fret)]))
        self.pick_dir = -d
        self.last_pick_t = now

    def strum_chord(self, notes, now, direction=None, muted=False, dig=True, at=None, span=None):
        """notes: [(string, midi, vel, fret)]. Strums across their strings (or `span`
        strings, e.g. for a miss); down unless an up stroke follows quickly."""
        if not notes and span is None:
            return
        if direction is None:
            last_t, last_dir = self.last_strum
            direction = -last_dir if now - last_t < 0.35 else 1
        strings = sorted({n[0] for n in notes}) if notes else list(span)
        vs = [self.neck.string_v(s) for s in strings]
        lo, hi = min(vs) - OVERSHOOT * self.sp * 0.5, max(vs) + OVERSHOOT * self.sp * 0.5
        v_from, v_to = (lo, hi) if direction > 0 else (hi, lo)
        per = self.settings.strum_spread
        dur = max(per * (len(strings) - 1), 0.0) + per
        t_hit = at if at is not None else now + PRE_ROLL
        hits = []
        for s, midi, vel, fret in notes:
            frac = (self.neck.string_v(s) - v_from) / (v_to - v_from) if v_to != v_from else 0.0
            hits.append((t_hit + dur * frac, s, midi, vel, fret))
        self.add_stroke(Stroke(t_hit, v_from, v_to, dur, dig, muted, hits if dig else []))
        self.last_strum = (now, direction)

    def add_stroke(self, stroke):
        self.strokes.append(stroke)
        self.strokes.sort(key=lambda s: s.t_hit)

    # -----------------------------
    # PER-TICK UPDATE
    # -----------------------------
    def update(self, now, dt):
        """Advance to time `now`: emit strikes that happened, move hands and strings."""
        self.now = now
        self.neck.update()
        st = self.settings
        # strikes
        for stroke in self.strokes:
            keep = []
            for hit in stroke.hits:
                if hit[0] <= now:
                    self._strike(hit, stroke)
                else:
                    keep.append(hit)
            stroke.hits = keep
        self.strokes = [s for s in self.strokes if s.end + 0.25 > now or s.hits]

        self._update_pick(now, dt)
        self._update_fret_hand(dt)
        self._update_strings(now)
        self.strum.write(self)
        self.fret.write(self)
        self._write_fret_fingers()
        self._update_elbow(dt)

    def _strike(self, hit, stroke):
        t, s, midi, vel, fret = hit
        state = self.string_state[s]
        state.update(t=t, amp=vel, damp_t=None, midi=midi)
        if fret is not None:
            state["fret"] = fret
        self.dirty_strings.add(s)
        if self.on_strike:
            self.on_strike(t, s, midi, vel, fret, "D" if stroke.down else "U", stroke.muted)

    # ---- picking hand
    def _update_pick(self, now, dt):
        st = self.settings
        dig = -st.pick_dig * self.sp * self.n_side
        lift = st.pick_lift * self.sp * self.n_side
        idle = Vector((self.tip_rest.x, self.tip_rest.y, lift))
        target, half_life = idle, st.hand_half_life * 2
        for stroke in self.strokes:
            depth = dig if stroke.dig else lift * 0.6
            pre = st.pick_approach
            if stroke.t_hit - pre <= now < stroke.t_hit:          # approach
                k = _smoothstep((now - (stroke.t_hit - pre)) / pre)
                target = Vector((self.tip_rest.x, stroke.v_from, lift + (depth - lift) * k))
                half_life = 0.004
                break
            if stroke.t_hit <= now <= stroke.end:                  # through the strings
                k = (now - stroke.t_hit) / stroke.duration if stroke.duration > 0 else 1.0
                target = Vector((self.tip_rest.x, stroke.v_from + (stroke.v_to - stroke.v_from) * k, depth))
                half_life = 0.0
                break
            if stroke.end < now <= stroke.end + st.pick_approach * 2:  # follow through and lift
                k = _smoothstep((now - stroke.end) / (st.pick_approach * 2))
                over = (stroke.v_to - stroke.v_from)
                over = math.copysign(OVERSHOOT * self.sp, over) if over else 0.0
                target = Vector((self.tip_rest.x, stroke.v_to + over * k, depth + (lift - depth) * k))
                half_life = 0.006
        self.tip = _smooth(self.tip, target, half_life, dt)
        self.strum.offset = self.tip - self.tip_rest

    # ---- fretting hand
    def _window_centre(self, p):
        s = self.neck.numbers[len(self.neck.numbers) // 2]
        a = self.neck.local(self.neck.fret_point(s, max(1, round(p)))).x
        b = self.neck.local(self.neck.fret_point(s, min(self.neck.max_fret, round(p) + 3))).x
        return (a + b) / 2

    def _update_fret_hand(self, dt):
        st = self.settings
        self.p = _smooth(self.p, float(self.p_target), st.hand_half_life, dt)
        lo, hi = math.floor(self.p), math.ceil(self.p)
        k = self.p - lo
        u = self._window_centre(lo) * (1 - k) + self._window_centre(hi) * k
        self.along = Vector((u - self._window_centre(self.p0), 0.0, 0.0))
        # the hand drops (world down) and comes out from the guitar's front in
        # proportion to how low - physically - the fretted strings are
        pressed = [s for s, f, _, _ in self.pressing.values()]
        if pressed:  # hold the last height while nothing is fretted
            low = sum(self.lowness[s] for s in pressed) / len(pressed)
            self.low = _smooth(self.low, low, st.low_string_lag, dt)
        down = self.neck.frame_inv.to_3x3() @ Vector((0.0, 0.0, -1.0))
        self.fret.offset = (self.along + down * (st.hand_drop * self.sp * self.low)
                            + Vector((0.0, 0.0, self.n_side)) * (st.hand_forward * self.sp * self.low))
        pad = (st.finger_pad - st.press_depth) * self.sp * self.n_side
        for name in self.fret.controls:
            if name in self.pressing:
                s, f, _, _ = self.pressing[name]
                if self.barre and name == "index":
                    # straight across the fret: aim past the far edge string
                    target = self.neck.local(self.neck.fret_point(self.barre_edge, self.barre[0]))
                    target.y += self.barre_out * st.barre_overhang * self.sp
                else:
                    target = self.neck.local(self.neck.fret_point(s, f))
                target = target + Vector((0, 0, pad))
                sideways = (target - self.finger_pos[name]).xy.length
                target.z += st.finger_arc * self.sp * self.n_side * min(1.0, sideways / self.sp)
                half_life = st.press_half_life
            else:
                target = self.finger_rest[name] + self.fret.offset
                half_life = st.release_half_life
            self.finger_pos[name] = _smooth(self.finger_pos[name], target, half_life, dt)

    def _update_elbow(self, dt):
        if self.elbow is None:
            return
        st = self.settings
        world = self.neck.to_world(self.elbow_rest + self.along * st.elbow_follow)
        parent = self.elbow.parent
        m = (pose_matrix(parent) @ parent.bone.matrix_local.inverted() @ self.elbow.bone.matrix_local
             if parent is not None else self.elbow.bone.matrix_local)
        self.elbow.location = m.inverted() @ (self.arm_world_inv @ world)

    def start_location(self, pb):
        """Basis location/matrix a controlled bone had when the session started."""
        if pb is self.strum.pb:
            return self.strum.start_basis
        if pb is self.fret.pb:
            return self.fret.start_basis
        if pb is self.elbow:
            return self.elbow_start
        for name, c in self.fret.controls.items():
            if c == pb:
                return self.fret.start_locs[name]
        return pb.location.copy()

    def _write_fret_fingers(self):
        h = self.fret
        for name, pb in h.controls.items():
            world = self.neck.to_world(self.finger_pos[name])
            m = h.control_pose_parent(pb)
            pb.location = m.inverted() @ (self.arm_world_inv @ world)

    # ---- strings
    def _offset_object(self, obj, world_delta):
        """Move a setup empty by world_delta from its rest location."""
        parent_m = obj.parent.matrix_world @ obj.matrix_parent_inverse if obj.parent else Matrix.Identity(4)
        local = parent_m.inverted().to_3x3() @ world_delta
        obj.location = self.rest_loc[obj.name] + local

    def _update_strings(self, now):
        st = self.settings
        n_world = self.neck.n * self.n_side
        v_world = self.neck.v
        for s, state in self.string_state.items():
            # pressed fret controllers
            for f, obj in self.neck.frets[s].items():
                pressed = state["fret"] == f and state["midi"] is not None
                key = (s, f)
                if pressed:
                    self._offset_object(obj, -n_world * st.string_press * self.sp)
                    self.fret_dirty.add(key)
                elif key in self.fret_dirty:
                    obj.location = self.rest_loc[obj.name]
                    self.fret_dirty.discard(key)
                    self.dirty_strings.add(s)
            # vibration
            vib = self.neck.vibrate.get(s, [])
            if not vib or state["amp"] <= 0:
                continue
            age = now - state["t"]
            decay = st.string_decay
            amp = state["amp"] * math.exp(-max(age, 0.0) / decay)
            if state["damp_t"] is not None:
                amp *= math.exp(-max(now - state["damp_t"], 0.0) / 0.06)
            if amp < 0.01 or age < 0:
                if amp < 0.01:
                    state["amp"] = 0.0
                    for obj in vib:
                        obj.location = self.rest_loc[obj.name]
                continue
            wave = math.sin(2 * math.pi * VIS_HZ * age * (1.0 + 0.08 * s))
            for i, obj in enumerate(vib):
                shape = math.sin(math.pi * (i + 1) / (len(vib) + 1))
                self._offset_object(obj, v_world * (st.string_vibration * self.sp * amp * wave * shape))
            self.dirty_strings.add(s)

    # -----------------------------
    def controlled_bones(self):
        yield self.strum.pb
        yield self.fret.pb
        for pb in self.fret.controls.values():
            yield pb
        if self.elbow is not None:
            yield self.elbow

    def string_objects(self, strings=None):
        for s in (strings if strings is not None else self.neck.numbers):
            yield from self.neck.frets[s].values()
            yield from self.neck.vibrate.get(s, [])

    def restore(self):
        self.strum.restore()
        self.fret.restore()
        if self.elbow is not None:
            self.elbow.location = self.elbow_start
        for name, loc in self.rest_loc.items():
            obj = bpy.data.objects.get(name)
            if obj:
                obj.location = loc
