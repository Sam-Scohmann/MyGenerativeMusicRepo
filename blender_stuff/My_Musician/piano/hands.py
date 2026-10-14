"""Realtime hand animation for the HandsArmature rig.

The rig has, per side (suffix _l / _r):
    hand_control_{s}            root bone, moves the whole hand (wrist + finger targets)
    wrist_{s}                   arm IK target, child of hand_control
    {finger}_control_{s}        fingertip IK targets, children of hand_control

The engine only ever writes the *location* of those control bones, so the
artist's IK / limit-distance setup does the actual posing.

Everything is computed in armature space. Positions along the keyboard are
measured as a single "lateral" coordinate (dot with the keyboard axis), in
the same units as the scene; `self.w` is one white-key width and most
settings are expressed in multiples of it.
"""
import itertools
import math
import random

import bpy
from mathutils import Vector

FINGER_BONES = ("thumb_control_{}", "index_control_{}", "mid_control_{}",
                "ring_f_control_{}", "pinkie_control_{}")
HAND_BONE = "hand_control_{}"
WRIST_BONE = "wrist_{}"
THUMB, PINKIE = 0, 4

# Finger choice cost weights (unitless, distances are in white keys)
MOVE_COST = 2.0          # any hand movement at all
MOVE_DIST_COST = 0.05    # per white key of hand travel
SPREAD_COST = 1.0        # per finger, at its full sideways spread (0 = relaxed spot)
RELEASE_COST = 6.0       # per held finger lifted early so the hand can move on
LEAD_COST = 1.5          # per finger step between the landing finger and the lead finger, when moving
NEAR_COST = 0.6          # per white key between the key and the finger's current spot
REPEAT_COST = 1.5        # same finger playing the next, different key...
REPEAT_TIME = 0.8        # ...within this many seconds (fades out over it)
INDEX, MIDDLE = 1, 2
THUMB_BLACK_COST = 0.6
PINKIE_BLACK_COST = 0.3
CROSS_HAND_COST = 3.0    # playing a note an octave or more on the "wrong" side of the split
SEP_COST = 4.0           # per white key two busy hands are closer than Min Hand Distance
SEP_IDLE_COST = 0.3      # same, when the other hand is idle (it just gets pushed away)
LIMIT_MARGIN = 0.95      # keep pressed fingers this far inside their wrist distance limit
WRIST_HALF_LIFE = 0.12   # seconds: how damped the wrist follows the fingers
SWAY_HZ = 3.0            # natural frequency of an idle finger's sideways dangle
SWAY_DAMPING = 0.22      # 1 = no overshoot; lower = more dangle
DEFAULT_SWAY_DEG = 5.0   # if the rig has no IK Z limits on the finger bones
NOISE_HZ = (0.53, 1.17, 2.31)  # slow, incommensurate wobbles for the wrist noise
UNREACH_COST = 12.0      # per white key a finger would have to reach below what its IK can
REACH_TOLERANCE = 0.08   # white keys: the IK tip counts as "reaching" its target within this
LOOSE_COST = 20.0        # playing a note only by sliding the hand back past the wrist limit
REACH_IN_COST = 1.5      # per white key the hand has to slide towards the back of the piano
REACH_MARGIN = 0.95      # fraction of a finger's straightened chain it may use
MAX_YAW = math.radians(89.0)


def find_sides(arm):
    return [s for s in ("l", "r") if HAND_BONE.format(s) in arm.pose.bones]


def controlled_bone_names(arm):
    """Every bone the engine writes to: hand_control (where the hand points),
    wrist (its height) and the finger IK controls."""
    for side in find_sides(arm):
        yield HAND_BONE.format(side)
        if WRIST_BONE.format(side) in arm.pose.bones:
            yield WRIST_BONE.format(side)
        for pattern in FINGER_BONES:
            if pattern.format(side) in arm.pose.bones:
                yield pattern.format(side)


def _smooth(cur, target, half_life, dt):
    if half_life <= 0.0:
        return target.copy() if isinstance(target, Vector) else target
    return cur + (target - cur) * (1.0 - 0.5 ** (dt / half_life))


def _ik_chain_root(arm, control_name):
    """Top bone of the IK chain that reaches for `control_name`, or None."""
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if c.type == 'IK' and c.subtarget == control_name and not c.mute:
                root = pb
                for _ in range(max(c.chain_count, 1) - 1):
                    if root.parent is None:
                        break
                    root = root.parent
                return root
    return None


def _ik_tip_bone(arm, control_name):
    """The bone whose IK reaches for `control_name` (its tail is the fingertip)."""
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if c.type == 'IK' and c.subtarget == control_name and not c.mute:
                return pb
    return None


def _palm_bone(arm, control_name):
    """The bone the finger's IK chain hangs from (the palm / forearm), or None."""
    root = _ik_chain_root(arm, control_name)
    return root.parent if root is not None else None


def _yaw(v, forward, axis):
    """Sideways angle of a vector in the keyboard plane (0 = straight ahead,
    positive = towards the high end of the keyboard)."""
    return math.atan2(v.dot(axis), v.dot(forward))


def _interval_and(a, b):
    if a is None or b is None:
        return None
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else None


def _ik_chain(arm, control_name):
    """[tip bone, its parent, ...] up to the chain length of the IK reaching
    for `control_name`."""
    tip = _ik_tip_bone(arm, control_name)
    if tip is None:
        return []
    count = next(c.chain_count for c in tip.constraints if c.type == 'IK' and c.subtarget == control_name)
    chain, bone = [], tip
    for _ in range(max(count, 1)):
        if bone is None:
            break
        chain.append(bone)
        bone = bone.parent
    return chain


class Sway:
    """Ragdoll-like sideways dangle of an idle finger: a damped spring on the
    finger's angle around its local Z, pushed by the hand's sideways
    acceleration and stopped at the rig's IK Z limit."""

    def __init__(self, hz, max_angle):
        self.omega = 2 * math.pi * hz
        self.max_angle = max_angle
        self.angle = 0.0
        self.vel = 0.0

    def step(self, dt, accel, length, gain):
        """accel: hand acceleration along the keyboard; returns the fingertip's
        sideways offset (same units as length)."""
        steps = max(1, int(math.ceil(dt / 0.004)))  # stable at any frame rate
        h = dt / steps
        for _ in range(steps):
            force = (-self.omega ** 2 * self.angle - 2 * SWAY_DAMPING * self.omega * self.vel
                     - gain * accel / max(length, 1e-6))
            self.vel += force * h
            self.angle += self.vel * h
            if abs(self.angle) > self.max_angle:
                self.angle = math.copysign(self.max_angle, self.angle)
                self.vel = 0.0
        return length * math.sin(self.angle)


class WristSpring:
    """The wrist follows its target height with a bit of inertia (lag and
    overshoot), never further than `max_offset` from the target."""

    def __init__(self):
        self.value = 0.0
        self.vel = 0.0

    def step(self, dt, target, inertia, max_offset):
        if inertia <= 0.0:
            self.value, self.vel = target, 0.0
            return target
        omega = 2 * math.pi * (6.0 - 4.5 * inertia)  # 6 Hz snappy .. 1.5 Hz heavy
        damping = 1.0 - 0.6 * inertia                # critically damped .. bouncy
        steps = max(1, int(math.ceil(dt / 0.004)))
        h = dt / steps
        for _ in range(steps):
            self.vel += (omega ** 2 * (target - self.value) - 2 * damping * omega * self.vel) * h
            self.value += self.vel * h
        self.value = _clamp(self.value, target - max_offset, target + max_offset)
        return self.value


def jump_lift(total, remaining, settings, w):
    """Wrist lift while the hand travels: an arc over the move, for moves
    longer than Jump Threshold (white keys)."""
    excess = total - settings.jump_threshold * w
    if excess <= 0 or total <= 0:
        return 0.0
    progress = _clamp(1.0 - remaining / total, 0.0, 1.0)
    return settings.jump_lift * excess * math.sin(math.pi * progress)


def hand_hop(total, remaining, settings, w):
    """hand_control's upward arc while moving sideways more than Hand Jump
    Threshold (white keys): Hand Jump Height at the middle of the move."""
    excess = total - settings.hand_jump_threshold * w
    if excess <= 0 or total <= 0:
        return 0.0
    progress = _clamp(1.0 - remaining / total, 0.0, 1.0)
    return settings.hand_jump_height * w * min(1.0, excess / w) * math.sin(math.pi * progress)


def finger_choice_cost(h, pairs, u_from, w, moving, lead, last, now):
        """Which finger plays: the one nearest the key (from where the hand is),
        not the same finger as the previous different key, and - when the hand
        has to move - landing on the hand's lead finger."""
        cost = 0.0
        rank = {f: i for i, f in enumerate(h.ordered)}
        for f, k in pairs:
            cost += NEAR_COST * abs(k.lateral - (u_from + f.dx)) / w
            if last is not None:
                lf, lnote, lt = last
                if f is lf and k.note != lnote and now - lt < REPEAT_TIME:
                    cost += REPEAT_COST * (1.0 - (now - lt) / REPEAT_TIME)
        if moving and len(pairs) == 1 and lead is not None:
            cost += LEAD_COST * abs(rank[pairs[0][0]] - rank[lead])
        return cost


def move_arc(total, remaining, settings, w):
    """hand_control's world-Z arc over any sideways move: Move Lift per white
    key travelled, highest mid-move."""
    if total <= 0:
        return 0.0
    progress = _clamp(1.0 - remaining / total, 0.0, 1.0)
    return settings.move_lift * total * math.sin(math.pi * progress)


def _sway_limit(arm, control_name):
    """Smallest IK Z limit (radians) on the bones of a finger's IK chain."""
    tip = None
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if c.type == 'IK' and c.subtarget == control_name and not c.mute:
                tip, count = pb, c.chain_count
    if tip is None:
        return math.radians(DEFAULT_SWAY_DEG)
    limits = []
    bone = tip
    for _ in range(max(count, 1)):
        if bone is None:
            break
        if bone.use_ik_limit_z:
            limits.append(min(abs(bone.ik_min_z), abs(bone.ik_max_z)))
        bone = bone.parent
    return min(limits) if limits else math.radians(DEFAULT_SWAY_DEG)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _cross_cost(note, is_bass, settings):
    """Cost of the bass (or treble) hand playing `note`; notes near the
    split point are cheap to give to either hand."""
    beyond = note - settings.split_note + 1 if is_bass else settings.split_note - note
    return CROSS_HAND_COST * min(1.0, beyond / 12.0) if beyond > 0 else 0.0


class Finger:
    def __init__(self, index, pbone, rest_offset):
        self.index = index
        self.pb = pbone
        self.rest_offset = rest_offset  # from hand head, armature space
        self.dx = 0.0                   # lateral position when the hand is at home
        self.order = 0.0                # 0 = lowest finger on the keyboard, 1 = highest
        self.note = None
        self.key = None
        self.pos = None
        self.released_at = -math.inf
        self.start_loc = pbone.location.copy()
        self.sway_offset = 0.0
        # the rig's Limit Distance to the wrist = how far this finger can reach
        self.limit = next((c.distance for c in pbone.constraints
                           if c.type == 'LIMIT_DISTANCE' and c.limit_mode == 'LIMITDIST_INSIDE'
                           and c.subtarget.startswith("wrist") and not c.mute and c.distance > 0), None)


class Hand:
    def __init__(self, engine, side):
        arm = engine.arm
        self.side = side
        self.pb = arm.pose.bones[HAND_BONE.format(side)]
        self.start_loc = self.pb.location.copy()
        p_rest = self.pb.bone.matrix_local
        p_pose = p_rest @ self.pb.matrix_basis
        self.home = p_pose.translation.copy()

        self.fingers = []
        for i, pattern in enumerate(FINGER_BONES):
            fpb = arm.pose.bones.get(pattern.format(side))
            if fpb is None:
                continue
            m = p_pose @ p_rest.inverted() @ fpb.bone.matrix_local @ fpb.matrix_basis
            f = Finger(i, fpb, m.translation - self.home)
            f.pos = m.translation.copy()
            # idle fingers keep their rest shape relative to the (evaluated) palm
            f.palm = _palm_bone(arm, fpb.name)
            if f.palm is not None:
                f.rest_in_palm = f.palm.matrix.inverted() @ f.pos
            # knuckle = where the chain leaves the palm (top chain bone's tail);
            # the finger spreads sideways around it
            root = _ik_chain_root(arm, fpb.name)
            f.knuckle = (root.tail - self.home) if root is not None else None
            self.fingers.append(f)

        self.wpb = arm.pose.bones.get(WRIST_BONE.format(side))
        if self.wpb is not None:
            self.wrist_start_loc = self.wpb.location.copy()
            m = p_pose @ p_rest.inverted() @ self.wpb.bone.matrix_local @ self.wpb.matrix_basis
            self.wrist_offset = m.translation - self.home
        else:
            self.wrist_offset = None

        self.u = 0.0           # lateral offset from home
        self.u_target = 0.0
        self.depth = 0.0       # horizontal slide towards the black keys
        self.wrist_dz = 0.0    # wrist height above its rest (the only vertical motion)
        self.engine = engine
        self.reach_fix = Vector()  # extra forward shift so pressed fingers stay within their limits
        self.last_active = -math.inf
        rng = random.Random(side)
        self.noise_phase = [rng.uniform(0, 2 * math.pi) for _ in NOISE_HZ]
        self.wrist = WristSpring()
        self.prev_u = None
        self.prev_vel = 0.0
        self.jump_total = 0.0
        self.reach_depth = 0.0  # planned slide towards the back of the piano
        self.lead = None         # finger the hand lands on when it moves (set by the engine)
        self.last_played = None  # (finger, note, time) of the hand's last single note
        for f in self.fingers:
            # slightly different dangle per finger so they don't move in lockstep
            f.sway = Sway(SWAY_HZ * (0.85 + 0.08 * f.index), _sway_limit(arm, f.pb.name))

    # -----------------------------
    def setup_frame(self, axis, forward, up, w):
        base = self.home.dot(axis)
        arm = self.engine.arm
        for f in self.fingers:
            f.dx = base + f.rest_offset.dot(axis)
            if f.knuckle is None:  # no IK chain found: pretend a finger 2 keys long, pointing ahead
                f.knuckle = f.rest_offset - forward * 2.0 * w
            v = f.rest_offset - f.knuckle
            f.yaw0 = _yaw(v, forward, axis)
            horizontal = v - up * v.dot(up)
            f.spread_len = max(horizontal.length, 0.5 * w)  # sway lever
            chain = _ik_chain(arm, f.pb.name)
            # how far the fingertip can get from the knuckle: the chain below
            # the knuckle, nearly straightened (never stretched)
            f.reach_len = REACH_MARGIN * sum(b.length for b in chain[:-1]) if len(chain) > 1 else 1.2 * v.length
            f.reach_len = max(f.reach_len, v.length)
            # the "middle segment" (the bone above the IK bone) - its sideways
            # angle is what neighbouring fingers mustn't close up on
            mid = chain[1] if len(chain) > 1 else (chain[0] if chain else None)
            f.seg_yaw0 = _yaw(mid.tail - mid.head, forward, axis) if mid is not None else f.yaw0
        ordered = sorted(self.fingers, key=lambda f: f.dx)
        self.ordered = ordered
        # rest angle between neighbours' middle segments
        self.gap0 = {(a, b): b.seg_yaw0 - a.seg_yaw0 for a, b in zip(ordered, ordered[1:])}
        lo = min(f.dx for f in self.fingers)
        hi = max(f.dx for f in self.fingers)
        for f in self.fingers:
            f.order = (f.dx - lo) / (hi - lo) if hi > lo else 0.5
        self.lateral = base
        self.span = (lo - base, hi - base)

    def held(self):
        return [f for f in self.fingers if f.note is not None]

    def low_edge(self, u=None):
        return (self.u_target if u is None else u) + self.lateral + self.span[0]

    def high_edge(self, u=None):
        return (self.u_target if u is None else u) + self.lateral + self.span[1]

    def candidates(self, keys, w, settings):
        """Yields (cost, fingers, hand_u, released) for every way the free
        fingers can play `keys` (sorted low to high) together.

        Fingers never stretch: each one may only turn sideways at its knuckle
        by the Seek Angle (and not into its neighbours), reach no further than
        its IK chain, and stay inside the rig's wrist Limit Distance. The hand
        moves as little as possible to make that true for the new notes and
        the notes still held; held notes that can't come along have their
        fingers lift early (`released`) - the keys keep sounding.
        If nothing fits the wrist limit, the hand may slide back instead."""
        found = list(self._candidates(keys, w, settings, strict=True))
        if not found:
            found = [(c + LOOSE_COST, fs, u, r) for c, fs, u, r in self._candidates(keys, w, settings, strict=False)]
        yield from found

    def _candidates(self, keys, w, settings, strict):
        engine = self.engine
        held = self.held()
        free = sorted((f for f in self.fingers if f.note is None), key=lambda f: f.dx)
        if len(keys) > len(free):
            return
        seek = math.radians(settings.finger_seek)
        held_pairs = [(g, g.key) for g in held]

        for combo in itertools.combinations(free, len(keys)):
            pairs = list(zip(combo, keys))
            base_depth = engine.depth_for([k for _, k in pairs] + [k for _, k in held_pairs], settings)
            placed = engine.placement(pairs, base_depth, settings, strict)
            if placed is None:
                continue  # these fingers can't play this chord without stretching
            win, depth = placed

            # keep every held finger whose key stays within reach; lift the rest
            keep = [(g, k) for g, k in held_pairs
                    if all((nk.lateral - k.lateral) * (f.dx - g.dx) > 0 for f, nk in pairs)]
            released = [g for g, k in held_pairs if (g, k) not in keep]
            centre = sum(k.lateral for k in keys) / len(keys)
            while keep:
                kplaced = engine.placement(pairs + keep, base_depth, settings, strict)
                if kplaced is not None:
                    win, depth = kplaced
                    break
                far = max(keep, key=lambda p: abs(p[1].lateral - centre))
                keep.remove(far)
                released.append(far[0])
            lo, hi = win

            penalty = RELEASE_COST * len(released) + REACH_IN_COST * (depth - base_depth) / w
            for f, k in pairs:
                penalty += UNREACH_COST * engine.shortfall(f, k, settings) / w
                if k.black and f.index == THUMB:
                    penalty += THUMB_BLACK_COST
                elif k.black and f.index == PINKIE:
                    penalty += PINKIE_BLACK_COST

            for u in (_clamp(self.u_target, lo, hi), (lo + hi) / 2):
                turns = {f: engine.turn(f, k, u, depth, settings) for f, k in pairs + keep}
                if not engine.gaps_ok(self, turns, settings):
                    continue  # a finger would turn into its pressed neighbour
                cost = penalty + SPREAD_COST * sum(abs(turns[f]) / seek for f, _ in pairs)
                cost += finger_choice_cost(self, pairs, self.u_target, w,
                                           moving=abs(u - self.u_target) > 1e-6, lead=self.lead,
                                           last=self.last_played, now=self.engine.now)
                if abs(u - self.u_target) > 1e-6:
                    cost += MOVE_COST + MOVE_DIST_COST * abs(u - self.u_target) / w
                yield cost, combo, (u, depth), released


class HandsEngine:
    def __init__(self, arm, keymap, settings):
        self.arm = arm
        self.keymap = keymap
        self.settings = settings
        self.to_arm = arm.matrix_world.inverted()
        self.axis, self.forward, self.up, self.w = keymap.keyboard_frame(self.to_arm)

        self.hands = [Hand(self, s) for s in find_sides(arm)]
        self.hands = [h for h in self.hands if h.fingers]
        for h in self.hands:
            h.setup_frame(self.axis, self.forward, self.up, self.w)
        # bass hand = the one sitting lower on the keyboard, regardless of its suffix
        self.hands.sort(key=lambda h: h.lateral)
        self.pending = []
        self.measure_pads()
        self.measure_reach()
        self.now = 0.0
        # live: the right (treble) hand leads with the index, the left (bass) with the pinkie
        for h in self.hands:
            idx = INDEX if (h is self.hands[-1] and len(self.hands) > 1) or len(self.hands) == 1 else PINKIE
            h.lead = next((f for f in h.fingers if f.index == idx), None)

    def __bool__(self):
        return bool(self.hands)

    def measure_pads(self):
        """How far each fingertip's skin reaches below its IK tip (the end of
        the last finger bone), measured on the deformed hand mesh in the pose
        the hands are in now. A pressing finger's target is raised by this so
        the pad rests on the key instead of sinking into it."""
        arm = self.arm
        meshes = [o for o in bpy.data.objects if o.type == 'MESH' and any(
            m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]
        points = []
        dg = bpy.context.evaluated_depsgraph_get()
        to_arm = self.to_arm
        for o in meshes:
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            mw = to_arm @ ev.matrix_world
            points += [mw @ v.co for v in me.vertices]
            ev.to_mesh_clear()
        radius = 0.4 * self.w
        for h in self.hands:
            for f in h.fingers:
                f.pad = 0.0
                tip = _ik_tip_bone(arm, f.pb.name)
                if tip is None or not points:
                    continue
                tail = tip.tail  # armature space, current (evaluated) pose
                below = [(tail - p).dot(self.up) for p in points if (p - tail).length < radius]
                if below:
                    f.pad = _clamp(max(below), 0.0, 0.5 * self.w)

    def measure_reach(self):
        """How far straight down (armature units) below its relaxed spot each
        finger's IK can really reach, tested on the rig in the current pose.
        Each finger control is lowered step by step (others stay put) until
        the fingertip no longer follows; the pose is restored afterwards."""
        view_layer = bpy.context.view_layer
        w = self.w
        for h in self.hands:
            p_rest = h.pb.bone.matrix_local
            to_hand = p_rest @ h.pb.matrix_basis @ p_rest.inverted()
            for f in h.fingers:
                f.reach = 2.0 * w  # fallback: no IK found
                tip = _ik_tip_bone(self.arm, f.pb.name)
                if tip is None:
                    continue
                saved = f.pb.location.copy()
                start = f.pos.copy()
                m_inv = (to_hand @ f.pb.bone.matrix_local).inverted()

                def reaches(drop):
                    target = start - self.up * drop
                    f.pb.location = m_inv @ target
                    view_layer.update()
                    return (tip.tail - target).length < REACH_TOLERANCE * w

                lo, hi = 0.0, 2.0 * w
                if reaches(hi):
                    lo = hi
                else:
                    for _ in range(10):  # binary search, ~0.002 key widths
                        mid = (lo + hi) / 2
                        if reaches(mid):
                            lo = mid
                        else:
                            hi = mid
                f.reach = lo
                f.pb.location = saved
            view_layer.update()

    def shortfall(self, f, key, settings):
        """How far (armature units) pressing `key` is below what finger f can
        reach from its relaxed spot (0 if it can reach). The hand never
        changes height, so this doesn't depend on where the hand is."""
        drop = (f.rest_offset + self.home_of(f)).dot(self.up) - self.press_target(f, key, settings).dot(self.up)
        return max(0.0, drop - f.reach)

    def home_of(self, f):
        for h in self.hands:
            if f in h.fingers:
                return h.home
        return Vector()

    def pad(self, f, settings):
        """Lift (armature units) of a pressing finger's target above the key surface."""
        if settings.fingertip_pad_auto:
            return f.pad
        return settings.fingertip_pad * self.w

    def press_target(self, f, key, settings):
        """Where a finger's IK target goes to press `key`: the key's touch point,
        raised by the fingertip pad, pushed in by Press Depth."""
        return (self.to_arm @ key.touch_world()
                + self.up * (self.pad(f, settings) - settings.press_depth * self.w))

    def wrist_noise(self, h, t, settings):
        amp = settings.wrist_noise * self.w
        return amp * sum(math.sin(2 * math.pi * hz * t + ph)
                         for hz, ph in zip(NOISE_HZ, h.noise_phase)) / len(NOISE_HZ)

    def wrist_height(self, h, hand_pos, finger_offsets, t, settings, reach_dip=0.0, jump=0.0):
        """Wrist height above its rest from the fingers' up/down offsets
        (already damped by the caller), its noise and its limits. reach_dip:
        how much further a pressing finger needs to get down than it can - the
        wrist dips that much more, up to Extra Dip for Reach."""
        dz = settings.wrist_strength * finger_offsets + self.wrist_noise(h, t, settings) + jump
        dz = _clamp(dz, -settings.max_wrist_dip * self.w, settings.max_wrist_lift * self.w)
        if reach_dip > 0:
            dz = min(dz, -settings.max_wrist_dip * self.w - min(reach_dip, settings.extra_reach_dip * self.w))
        if h.wrist_offset is not None:
            # never below hand_control
            dz = max(dz, -h.wrist_offset.dot(self.up))
        return dz

    # -----------------------------
    # NOTE EVENTS
    # -----------------------------
    def preferred_hand(self, note, settings):
        if len(self.hands) == 1:
            return self.hands[0]
        return self.hands[0] if note < settings.split_note else self.hands[-1]

    def note_on(self, note, now, settings):
        self.settings = settings
        self.now = now
        """Queued: notes arriving in the same tick are fingered together as a chord."""
        key = self.keymap.get(note)
        if key is None:
            return
        for h in self.hands:
            for f in h.fingers:
                if f.note == note:  # re-trigger of a held note
                    h.last_active = now
                    return
        if key not in self.pending:
            self.pending.append(key)

    def note_off(self, note, now):
        self.pending = [k for k in self.pending if k.note != note]
        for h in self.hands:
            for f in h.fingers:
                if f.note == note:
                    self._release(f, now)
                    h.last_active = now

    def _best_for(self, h, keys, settings):
        """Cheapest (cost, fingers, u, released) for hand h to play keys together, or None."""
        if not keys:
            return 0.0, (), (h.u_target, h.reach_depth), []
        best = None
        for cost, fingers, u, released in h.candidates(keys, self.w, settings):
            cost += self._collision_cost(h, u[0])
            if best is None or cost < best[0]:
                best = (cost, fingers, u, released)
        return best

    def _flush(self, now, settings):
        pending, self.pending = self.pending, []
        pending.sort(key=lambda k: k.lateral)

        if len(self.hands) == 2:
            # Try every low/high split of the chord between the two hands;
            # notes on the "wrong" side of the split note cost extra.
            bass, treble = self.hands
            best = None
            for i in range(len(pending) + 1):
                low, high = pending[:i], pending[i:]
                b = self._best_for(bass, low, settings)
                t = self._best_for(treble, high, settings)
                if b is None or t is None:
                    continue
                cost = (b[0] + t[0] + sum(_cross_cost(k.note, True, settings) for k in low)
                        + sum(_cross_cost(k.note, False, settings) for k in high))
                if best is None or cost < best[0]:
                    best = (cost, low, b, high, t)
            if best:
                _, low, b, high, t = best
                if low:
                    self._assign(bass, b[1], low, b[2], now, b[3])
                if high:
                    self._assign(treble, t[1], high, t[2], now, t[3])
                return
        elif len(self.hands) == 1:
            found = self._best_for(self.hands[0], pending, settings)
            if found:
                self._assign(self.hands[0], found[1], pending, found[2], now, found[3])
                return

        for key in pending:
            self._assign_single(key, now, settings)

    def _assign_single(self, key, now, settings):
        preferred = self.preferred_hand(key.note, settings)
        best = None
        for h in self.hands:
            side_cost = _cross_cost(key.note, h is self.hands[0], settings) if len(self.hands) > 1 else 0.0
            for cost, fingers, u, released in h.candidates([key], self.w, settings):
                cost += side_cost + self._collision_cost(h, u[0])
                if best is None or cost < best[0]:
                    best = (cost, h, fingers, u, released)

        if best is None:
            # every free finger is taken: give the note to the finger holding
            # the nearest key (that key keeps sounding, only the finger moves)
            h = preferred
            f = min(h.held(), key=lambda g: abs(g.key.lateral - key.lateral), default=None)
            if f is None:
                return
            self._release(f, now)
            best = min(((c, h, fs, u, r) for c, fs, u, r in h.candidates([key], self.w, settings)),
                       key=lambda c: c[0], default=None)
            if best is None:
                return

        _, h, fingers, u, released = best
        self._assign(h, fingers, [key], u, now, released)

    def _assign(self, h, fingers, keys, u, now, released=()):
        u, h.reach_depth = u
        if len(keys) == 1 and fingers:
            h.last_played = (fingers[0], keys[0].note, now)
        for f in released:
            self._release(f, now)
        for f, key in zip(fingers, keys):
            f.note, f.key = key.note, key
        if abs(u - h.u_target) > 1e-6:
            h.jump_total = abs(u - h.u)  # a new move starts from where the hand is now
        h.u_target = u
        h.last_active = now
        self._push_other_hand(h)

    def all_off(self, now):
        self.pending = []
        for h in self.hands:
            for f in h.fingers:
                if f.note is not None:
                    self._release(f, now)

    @staticmethod
    def _release(f, now):
        f.note = None
        f.key = None
        f.released_at = now

    # -----------------------------
    # TWO-HAND SPACING
    # -----------------------------
    def _neighbours(self, h):
        i = self.hands.index(h)
        below = self.hands[i - 1] if i > 0 else None
        above = self.hands[i + 1] if i + 1 < len(self.hands) else None
        return below, above

    def _collision_cost(self, h, u):
        """Cost of hand h sitting at u closer than Min Hand Distance to its
        neighbour (hand_control centres, along the keyboard). A busy neighbour
        costs a lot; an idle one little - it will be pushed away."""
        below, above = self._neighbours(h)
        min_d = self.settings.min_hand_distance * self.w
        cost = 0.0
        if above:
            short = min_d - ((above.lateral + above.u_target) - (h.lateral + u))
            if short > 0:
                cost += (SEP_COST if above.held() else SEP_IDLE_COST) * short / self.w
        if below:
            short = min_d - ((h.lateral + u) - (below.lateral + below.u_target))
            if short > 0:
                cost += (SEP_COST if below.held() else SEP_IDLE_COST) * short / self.w
        return cost

    def _separate(self):
        """Keep Min Hand Distance between the two hands' targets: a busy hand
        pushes an idle one away; two idle hands each give way half."""
        if len(self.hands) != 2:
            return
        bass, treble = self.hands
        short = self.settings.min_hand_distance * self.w - (
            (treble.lateral + treble.u_target) - (bass.lateral + bass.u_target))
        if short <= 0:
            return
        bh, th = bool(bass.held()), bool(treble.held())
        if bh and th:
            return  # both playing: their notes decide
        share_b = 0.5 if not bh and not th else (1.0 if not bh else 0.0)
        for h, amount in ((bass, -short * share_b), (treble, short * (1.0 - share_b))):
            if amount:
                h.u_target += amount
                h.jump_total = abs(h.u_target - h.u)

    def _push_other_hand(self, h):
        """An idle neighbour moves out of the way to keep Min Hand Distance."""
        below, above = self._neighbours(h)
        min_d = self.settings.min_hand_distance * self.w
        if above and not above.held():
            short = min_d - ((above.lateral + above.u_target) - (h.lateral + h.u_target))
            if short > 0:
                above.u_target += short
                above.jump_total = abs(above.u_target - above.u)
        if below and not below.held():
            short = min_d - ((h.lateral + h.u_target) - (below.lateral + below.u_target))
            if short > 0:
                below.u_target -= short
                below.jump_total = abs(below.u_target - below.u)

    # -----------------------------
    # PER-TICK UPDATE
    # -----------------------------
    def update(self, dt, now, settings):
        self.settings = settings
        if self.pending:
            self._flush(now, settings)
        for h in self.hands:
            if not h.held() and settings.home_delay > 0 and now - h.last_active > settings.home_delay:
                h.u_target = 0.0  # back home...
        self._separate()          # ...but never into the other hand
        w = self.w
        for h in self.hands:
            held = h.held()
            go_home = not held and settings.home_delay > 0 and now - h.last_active > settings.home_delay

            if held:
                depth_target = h.reach_depth
            else:
                depth_target = 0.0 if go_home else h.depth

            # hand_control: sideways (+ forward for black keys), never up or down
            h.u = _smooth(h.u, h.u_target, settings.hand_half_life, dt)
            h.depth = _smooth(h.depth, depth_target, settings.hand_half_life * 2, dt)
            hand_pos = h.home + self.axis * h.u + self.forward * h.depth
            h.hop_gate = _smooth(getattr(h, "hop_gate", 1.0), 0.0 if held else 1.0, settings.press_half_life, dt)
            remaining = abs(h.u_target - h.u)
            hop = h.hop_gate * hand_hop(h.jump_total, remaining, settings, w)
            move = move_arc(h.jump_total, remaining, settings, w)
            vel = 0.0 if h.prev_u is None or dt <= 0 else (h.u - h.prev_u) / dt
            accel = 0.0 if dt <= 0 else (vel - h.prev_vel) / dt
            h.prev_u, h.prev_vel = h.u, vel

            key_targets = {f: self.press_target(f, f.key, settings) for f in held}
            fix = self._reach_fix(h, hand_pos, key_targets)
            fix = self.forward * min(fix.length, max(0.0, settings.max_reach_in * w - h.depth))
            h.reach_fix = _smooth(h.reach_fix, fix, settings.hand_half_life, dt)
            hand_pos = hand_pos + h.reach_fix

            # world-Z lift of hand_control: every move, the hop, reach-in and
            # playing - never so high that a pressing finger can't reach
            h.activity = _smooth(getattr(h, "activity", 0.0), len(held) / max(len(h.fingers), 1),
                                 WRIST_HALF_LIFE, dt)
            raw = self.lift_target(h, move, hop, h.depth + h.reach_fix.length, h.activity, now, settings)
            allowed = self.lift_allowed(h, hand_pos, key_targets, settings)
            h.lift = _smooth(getattr(h, "lift", 0.0), min(raw, allowed), settings.hand_half_life, dt)
            h.lift = min(h.lift, allowed)
            hand_pos = hand_pos + self.up * h.lift

            pressed_turns = {f: _yaw(key_targets[f] - (hand_pos + f.knuckle), self.forward, self.axis) - f.yaw0
                             for f in held}
            room = self.idle_turns(h, pressed_turns, settings)
            for f in h.fingers:
                if f.key is not None:
                    # while the hand is still on its way the finger reaches only as
                    # far as it can spread, hovering, and lands when the hand arrives
                    target, short = self.clamp_seek(f, hand_pos, key_targets[f], settings)
                    sideways = target - f.pos
                    sideways -= self.up * sideways.dot(self.up)
                    lift = min(1.0, (sideways.length + short) / w)
                    target += self.up * (settings.arc_height * w * lift)
                    half_life = settings.press_half_life
                else:
                    if f.palm is not None:
                        target = f.palm.matrix @ f.rest_in_palm
                    else:
                        target = hand_pos + f.rest_offset
                    target = self.turn_about_knuckle(f, hand_pos, target, room.get(f, 0.0))
                    target = target + self.axis * f.sway_offset
                    half_life = settings.release_half_life
                f.pos = _smooth(f.pos, target, half_life, dt)
                f.sway_offset = f.sway.step(dt, accel, f.spread_len, settings.finger_sway)
                if f.key is not None:  # a finger sliding in from far away mustn't over-reach either
                    f.pos, _ = self.clamp_seek(f, hand_pos, f.pos, settings)

            # wrist: damped average of how far the fingers are up/down from relaxed
            offsets = [(f.pos - hand_pos - f.rest_offset).dot(self.up) if f.key is not None else 0.0
                       for f in h.fingers]
            h.finger_avg = _smooth(getattr(h, "finger_avg", 0.0), sum(offsets) / len(offsets),
                                   WRIST_HALF_LIFE, dt)
            need = max((self.shortfall(f, f.key, settings) for f in h.fingers if f.key is not None), default=0.0)
            h.reach_dip = _smooth(getattr(h, "reach_dip", 0.0), need, settings.press_half_life, dt)
            jump = jump_lift(h.jump_total, abs(h.u_target - h.u), settings, w)
            if h.jump_total and abs(h.u_target - h.u) < 1e-3 * w:
                h.jump_total = 0.0
            target_dz = self.wrist_height(h, hand_pos, h.finger_avg, now, settings, h.reach_dip, jump)
            dz = h.wrist.step(dt, target_dz, settings.wrist_inertia, settings.wrist_inertia_max * w)
            h.wrist_dz = self.limit_wrist(h, dz, settings, h.reach_dip)
            self._write(h, hand_pos, h.wrist_dz)

    def limit_wrist(self, h, dz, settings, reach_dip=0.0):
        """Wrist limits after inertia: Max Wrist Dip (+ the reach dip), Max Wrist
        Lift, and never below hand_control."""
        lowest = -settings.max_wrist_dip * self.w - min(reach_dip, settings.extra_reach_dip * self.w)
        dz = _clamp(dz, lowest, settings.max_wrist_lift * self.w)
        if h.wrist_offset is not None:
            dz = max(dz, -h.wrist_offset.dot(self.up))
        return dz

    # -----------------------------
    # SEEK GEOMETRY
    # -----------------------------
    def depth_for(self, keys, settings):
        """Horizontal slide towards the back of the piano for these keys."""
        if not keys:
            return 0.0
        return sum(k.black for k in keys) / len(keys) * settings.black_forward * self.w

    def hand_of(self, f):
        return next(h for h in self.hands if f in h.fingers)

    def u_window(self, f, key, depth, settings, strict=True):
        """Range of hand offsets u (along the keyboard) from which finger f
        reaches `key` without stretching: turning at its knuckle no more than
        the Seek Angle, within its IK chain's reach, and (strict) within the
        rig's wrist Limit Distance. None if there's no such u."""
        h = self.hand_of(f)
        target = self.press_target(f, key, settings)
        seek = math.radians(settings.finger_seek)
        # knuckle when u = 0
        knuckle0 = h.home + self.forward * depth + f.knuckle
        v0 = target - knuckle0
        ahead, up = v0.dot(self.forward), self.lowered(v0.dot(self.up), settings)
        if ahead <= 0:
            return None  # the key is behind the knuckle
        side0 = v0.dot(self.axis)  # sideways offset at u = 0; it is side0 - u at u
        lo_yaw = max(f.yaw0 - seek, -MAX_YAW)
        hi_yaw = min(f.yaw0 + seek, MAX_YAW)
        if lo_yaw > hi_yaw:
            return None
        side = (ahead * math.tan(lo_yaw), ahead * math.tan(hi_yaw))
        room = f.reach_len ** 2 - ahead ** 2 - up ** 2
        if room < 0:
            return None
        side = _interval_and(side, (-math.sqrt(room), math.sqrt(room)))
        if side is None:
            return None
        win = (side0 - side[1], side0 - side[0])
        if strict and f.limit is not None and h.wrist_offset is not None:
            w0 = target - (h.home + self.forward * depth + h.wrist_offset)
            wroom = (f.limit * LIMIT_MARGIN) ** 2 - w0.dot(self.forward) ** 2 - self.lowered(w0.dot(self.up), settings) ** 2
            if wroom < 0:
                return None
            ws = w0.dot(self.axis)
            win = _interval_and(win, (ws - math.sqrt(wroom), ws + math.sqrt(wroom)))
        return win

    def lowered(self, up, settings):
        """Vertical offset to a key (negative = below) once the wrist has
        dipped as far as it may (Max Wrist Dip + Extra Dip for Reach)."""
        return min(0.0, up + (settings.max_wrist_dip + settings.extra_reach_dip) * self.w) if up < 0 else up

    def reach_in(self, f, key, depth, settings, strict=True):
        """How much further (armature units) the hand must slide towards the
        back of the piano, from `depth`, for finger f to get within reach of
        key at all (ignoring sideways). None if no slide can do it."""
        h = self.hand_of(f)
        target = self.press_target(f, key, settings)
        need = 0.0
        spheres = [(h.home + self.forward * depth + f.knuckle, f.reach_len)]
        if strict and f.limit is not None and h.wrist_offset is not None:
            spheres.append((h.home + self.forward * depth + h.wrist_offset, f.limit * LIMIT_MARGIN))
        for centre, radius in spheres:
            v = target - centre
            up = self.lowered(v.dot(self.up), settings)
            room = radius ** 2 - up ** 2
            if room < 0:
                return None
            need = max(need, v.dot(self.forward) - math.sqrt(room) * 0.98)
        return need

    def placement(self, pairs, depth, settings, strict=True):
        """(u window, depth) for playing every (finger, key) in pairs: the hand
        slides towards the back of the piano only as far as needed (at most
        Max Reach-in). None if the chord can't be reached."""
        extra = 0.0
        for f, k in pairs:
            need = self.reach_in(f, k, depth, settings, strict)
            if need is None:
                return None
            extra = max(extra, need)
        if depth + extra > settings.max_reach_in * self.w + 1e-9:
            return None
        win = self.window(pairs, depth + extra, settings, strict)
        return (win, depth + extra) if win is not None else None

    def lift_allowed(self, h, hand_pos, key_targets, settings):
        """Highest world-Z lift of hand_control (from hand_pos) at which every
        pressing finger still reaches its key: within its IK chain from the
        knuckle and inside the rig's wrist Limit Distance (the wrist may still
        dip as usual). inf when no finger is pressing."""
        best = math.inf
        dip = (settings.max_wrist_dip + settings.extra_reach_dip) * self.w
        for f, target in key_targets.items():
            spheres = [(hand_pos + f.knuckle, f.reach_len, 0.0)]  # knuckles don't dip with the wrist
            if f.limit is not None and h.wrist_offset is not None:
                spheres.append((hand_pos + h.wrist_offset, f.limit * LIMIT_MARGIN, dip))
            for centre, radius, slack in spheres:
                v = target - centre
                vert = v.dot(self.up)
                horiz2 = v.length_squared - vert * vert
                room = radius * radius - horiz2
                if room < 0:
                    best = min(best, 0.0)
                    continue
                # lifting by L moves the centre up: need (vert - L)^2 <= room
                best = min(best, max(0.0, vert + math.sqrt(room) + slack))
        return best

    def lift_target(self, h, move, hop, depth, activity, t, settings):
        """World-Z lift of hand_control before limits: Move Lift arc, Hand Jump,
        Lift per Reach-in, Play Lift (damped finger activity) and Hand Noise."""
        w = self.w
        noise = settings.hand_noise * w * sum(math.sin(2 * math.pi * hz * 0.83 * t + ph + 1.3)
                                              for hz, ph in zip(NOISE_HZ, h.noise_phase)) / len(NOISE_HZ)
        raw = (move + hop + settings.reach_in_lift * max(depth, 0.0)
               + settings.play_lift * w * activity + noise)
        return _clamp(raw, 0.0, settings.max_hand_lift * w)

    def window(self, pairs, depth, settings, strict=True):
        """Hand offsets from which every (finger, key) in pairs is reachable."""
        win = (-math.inf, math.inf)
        for f, k in pairs:
            win = _interval_and(win, self.u_window(f, k, depth, settings, strict))
            if win is None:
                return None
        return win

    def turn(self, f, key, u, depth, settings):
        """How far (radians) finger f turns sideways from rest to press key."""
        h = self.hand_of(f)
        knuckle = h.home + self.axis * u + self.forward * depth + f.knuckle
        return _yaw(self.press_target(f, key, settings) - knuckle, self.forward, self.axis) - f.yaw0

    def gaps_ok(self, h, turns, settings):
        """Pressed fingers mustn't close up on each other: between two pressed
        fingers, the rest angles of their middle segments (and of any idle
        fingers in between, which make room) minus a minimum gap per space."""
        min_gap = math.radians(settings.finger_min_gap)
        pressed = [f for f in h.ordered if f in turns]
        for a, b in zip(pressed, pressed[1:]):
            i, j = h.ordered.index(a), h.ordered.index(b)
            room = sum(max(h.gap0[(h.ordered[n], h.ordered[n + 1])], min_gap) - min_gap for n in range(i, j))
            if turns[a] - turns[b] > room + 1e-6:
                return False
        return True

    def idle_turns(self, h, turns, settings):
        """Turns for idle fingers so they make room for pressed neighbours
        (within the Seek Angle); pressed fingers keep theirs."""
        min_gap = math.radians(settings.finger_min_gap)
        seek = math.radians(settings.finger_seek)
        out = {f: turns.get(f, 0.0) for f in h.ordered}
        order = h.ordered
        for _ in range(2):
            for a, b in zip(order, order[1:]):  # push higher fingers up
                room = max(h.gap0[(a, b)], min_gap) - min_gap
                if b not in turns and out[a] - out[b] > room:
                    out[b] = _clamp(out[a] - room, -seek, seek)
            for a, b in reversed(list(zip(order, order[1:]))):  # push lower fingers down
                room = max(h.gap0[(a, b)], min_gap) - min_gap
                if a not in turns and out[a] - out[b] > room:
                    out[a] = _clamp(out[b] + room, -seek, seek)
        return out

    def turn_about_knuckle(self, f, hand_pos, point, angle):
        """`point` rotated sideways by `angle` around finger f's knuckle."""
        if abs(angle) < 1e-9:
            return point.copy()
        knuckle = hand_pos + f.knuckle
        v = point - knuckle
        fw, ax, up = v.dot(self.forward), v.dot(self.axis), v.dot(self.up)
        c, s_ = math.cos(angle), math.sin(angle)
        return knuckle + self.forward * (fw * c - ax * s_) + self.axis * (fw * s_ + ax * c) + self.up * up

    def clamp_seek(self, f, hand_pos, target, settings):
        """`target` turned back to within the finger's Seek Angle around its
        knuckle and pulled in to its reach, for a hand at `hand_pos` (a finger
        never stretches, even while the hand is still on its way).
        Returns (target, how far it fell short)."""
        knuckle = hand_pos + f.knuckle
        v = target - knuckle
        yaw = _yaw(v, self.forward, self.axis)
        seek = math.radians(settings.finger_seek)
        clamped = _clamp(yaw, f.yaw0 - seek, f.yaw0 + seek)
        out = self.turn_about_knuckle(f, hand_pos, target, clamped - yaw)
        v = out - knuckle
        if v.length > f.reach_len:
            out = knuckle + v * (f.reach_len / v.length)
        return out, (out - target).length

    def _reach_fix(self, h, hand_pos, key_targets):
        """Forward (horizontal) hand shift that brings each pressed key within
        its finger's wrist distance limit. hand_control never changes height
        and never shifts sideways (that's the fingering's decision)."""
        if h.wrist_offset is None:
            return Vector()
        shift = 0.0
        wrist = hand_pos + h.wrist_offset
        for f, target in key_targets.items():
            if f.limit is None:
                continue
            d = target - wrist
            ahead = d.dot(self.forward)
            side = d.dot(self.axis)
            vertical = d.dot(self.up)
            limit = f.limit * LIMIT_MARGIN
            room = limit * limit - side * side - vertical * vertical
            shift = max(shift, ahead - math.sqrt(max(room, 0.0)))
        return self.forward * shift

    def write_hand(self, h, hand_pos, wrist_dz):
        """hand_control and wrist only (fingers are written by _write)."""
        p_rest = h.pb.bone.matrix_local
        h.pb.location = p_rest.inverted() @ hand_pos
        if h.wpb is not None:
            to_hand = p_rest @ h.pb.matrix_basis @ p_rest.inverted()
            m = to_hand @ h.wpb.bone.matrix_local
            h.wpb.location = m.inverted() @ (hand_pos + h.wrist_offset + self.up * wrist_dz)

    def _write(self, h, hand_pos, wrist_dz=0.0):
        self.write_hand(h, hand_pos, wrist_dz)
        p_rest = h.pb.bone.matrix_local
        to_hand = p_rest @ h.pb.matrix_basis @ p_rest.inverted()
        for f in h.fingers:
            m = to_hand @ f.pb.bone.matrix_local
            f.pb.location = m.inverted() @ f.pos

    # -----------------------------
    def controlled_bones(self):
        for h in self.hands:
            yield h.pb
            if h.wpb is not None:
                yield h.wpb
            for f in h.fingers:
                yield f.pb

    def start_location(self, pb):
        """Location the bone had when the session started (the rest pose)."""
        for h in self.hands:
            if h.pb == pb:
                return h.start_loc.copy()
            if h.wpb is not None and h.wpb == pb:
                return h.wrist_start_loc.copy()
            for f in h.fingers:
                if f.pb == pb:
                    return f.start_loc.copy()
        return pb.location.copy()

    def restore(self):
        for h in self.hands:
            h.pb.location = h.start_loc
            if h.wpb is not None:
                h.wpb.location = h.wrist_start_loc
            for f in h.fingers:
                f.pb.location = f.start_loc
