"""Re-animate the guitarist from the recorded take, with the whole take known.

1. Fingering: every chord/note group is placed on the neck by the global
   solver (positions, shortest shifts and chord voicings over the whole take).
2. Replay: the recorded plucks and strums are replayed through the same engine
   on a frame clock, with lookahead - the fretting hand shifts just before the
   notes, the pick approaches before each recorded hit time.
3. Bake: hands and strings are keyed frame by frame over the take, replacing
   their keys there. The take's plucks get the new strings/frets (the sound is
   rebuilt if Build Sound After Recording is on).
"""
import math

import bpy
from mathutils import Vector

from . import fingering, take
from .engine import OVERSHOOT, Stroke
from .session import Session
from ..piano.live import _fcurves
from ..piano.rethink import _write_fcurve

FRET_LEAD = 0.08     # seconds the fretting hand is in place before a note
STROKE_GAP = 0.03    # plucks further apart than this (plus the strum spread) are separate strokes


def _final_key_frame(arm, names):
    frames = []
    for fc in _fcurves(arm):
        if any(fc.data_path.startswith(f'pose.bones["{n}"]') for n in names) and len(fc.keyframe_points):
            frames.append(max(p.co[0] for p in fc.keyframe_points))
    return max(frames) if frames else None


def _apply_rest_from_keys(arm, names):
    """Put the controlled bones where they are at their final keyframe."""
    final = _final_key_frame(arm, names)
    if final is None:
        return
    for fc in _fcurves(arm):
        for n in names:
            prefix = f'pose.bones["{n}"].'
            if fc.data_path.startswith(prefix) and len(fc.keyframe_points):
                prop = fc.data_path[len(prefix):]
                pb = arm.pose.bones[n]
                if prop in ("location", "rotation_quaternion", "rotation_euler", "scale"):
                    getattr(pb, prop)[fc.array_index] = fc.evaluate(final)


def _groups(events, window):
    ons = [e for e in events if e["type"] == "on"]
    offs = [e for e in events if e["type"] == "off"]
    groups = []
    for e in sorted(ons, key=lambda e: e["t"]):
        if groups and e["t"] - groups[-1][0] <= window:
            groups[-1][1].append(e["midi"])
        else:
            groups.append((e["t"], [e["midi"]]))
    out = []
    for t, midis in groups:
        off = {}
        for m in midis:
            off[m] = next((o["t"] for o in offs if o["midi"] == m and o["t"] > t), t + 1.0)
        out.append((t, midis, off))
    return out


def rethink(context):
    scene = context.scene
    settings = scene.guitar_live
    from . import session as session_mod
    if session_mod.is_running():
        raise RuntimeError("Stop the live guitar first")
    data = take.load(scene)
    if not data:
        raise RuntimeError("No recorded guitar take: record one first")
    arm = settings.armature or session_mod.find_armature(scene)
    if arm is None:
        raise RuntimeError("No hands armature found")
    fps, start_frame = data["fps"], data["start_frame"]
    events = sorted(data["events"], key=lambda e: e["t"])

    names = [b.name for b in arm.pose.bones if b.name.startswith("hand_control_") or "_control_" in b.name]
    _apply_rest_from_keys(arm, names)
    context.view_layer.update()
    sess = Session(scene, settings)          # builds neck + engine, mutes the take
    engine, neck = sess.engine, sess.neck
    try:
        # 1. global fingering
        if settings.fold_octaves:
            for e in events:
                if e["type"] in ("on", "off"):
                    e["midi"] = neck.fold(e["midi"])
        groups = _groups(events, max(settings.chord_window, 0.005))
        plan = fingering.choose_global(neck, groups, start_p=engine.p0, octaves=settings.octave_far_notes)
        placed = []  # (t, midi) -> (string, fret)
        actions = []  # (time, kind, payload)
        for gi, ((t, midis, offs), choice) in enumerate(zip(groups, plan)):
            if choice is None:
                continue
            v, p, fingers, barre = choice
            actions.append((t - FRET_LEAD, "fret", (v, p, fingers, barre, gi)))
            for m, s, f in v.notes:
                placed.append((t, v.sent(m), s, f, gi, m))

        def where(midi, t, with_group=False):
            """(string, fret[, group]) of the latest group with this note (as sent,
            or as played an octave away) started by t."""
            best = None
            for gt, sent, s, f, gi, played_m in placed:
                if midi in (sent, played_m) and gt <= t + 1e-3:
                    best = (s, f, gi)
            if best is None:
                return None
            return best if with_group else best[:2]

        def played_as(midi, t):
            best = midi
            for gt, sent, s, f, gi, played_m in placed:
                if midi in (sent, played_m) and gt <= t + 1e-3:
                    best = played_m
            return best

        # 2. strikes -> strokes, re-placed on the new strings
        strikes = [e for e in events if e["type"] == "strike"]
        for e in strikes:
            sf = where(e["midi"], e["t"])
            if sf:
                e["midi"] = played_as(e["midi"], e["t"])
                e["string"], e["fret"] = sf
        strokes, cur = [], []
        gap = STROKE_GAP + settings.strum_spread * len(neck.numbers)
        for e in strikes:
            if cur and (e["t"] - cur[-1]["t"] > gap or e["dir"] != cur[-1]["dir"]
                        or any(c["string"] == e["string"] for c in cur)):
                strokes.append(cur)
                cur = []
            cur.append(e)
        if cur:
            strokes.append(cur)
        for st in strokes:
            down = st[0]["dir"] == "D"
            strings = sorted({e["string"] for e in st})
            vs = [neck.string_v(s) for s in strings]
            margin = (OVERSHOOT * 0.5 if len(st) > 1 else 0.5) * neck.spacing
            lo, hi = min(vs) - margin, max(vs) + margin
            v_from, v_to = (lo, hi) if down else (hi, lo)
            first = min(st, key=lambda e: e["t"])
            if len(st) > 1:
                dur = max(settings.strum_spread * len(strings), max(e["t"] for e in st) - first["t"] + settings.strum_spread)
            else:
                dur = settings.pick_stroke
            frac0 = (neck.string_v(first["string"]) - v_from) / (v_to - v_from)
            t_hit = first["t"] - dur * frac0
            hits = [(e["t"], e["string"], e["midi"], e["vel"], e.get("fret")) for e in st]
            stroke = Stroke(t_hit, v_from, v_to, dur, True, any(e.get("muted") for e in st), hits)
            actions.append((t_hit - settings.pick_approach - 0.05, "stroke", stroke))
        # releases: a note off frees whichever string plays it then
        for e in events:
            if e["type"] == "off":
                # the note this off ends started before it - its own group, even if the
                # same note is already fretted again for the next chord
                sfg = where(e["midi"], e["t"] - 1e-3, with_group=True)
                if sfg:
                    actions.append((e["t"], "release", (sfg[0], e["midi"], sfg[2])))
        actions.sort(key=lambda a: a[0])

        # 3. replay on the frame clock and bake
        last_t = max(e["t"] for e in events)
        f0 = int(math.floor(start_frame + (min(a[0] for a in actions) if actions else 0.0) * fps)) - 2
        f1 = int(math.ceil(start_frame + (last_t + 1.5) * fps))
        bone_vals = {pb.name: [] for pb in engine.controlled_bones()}
        objs = list(engine.string_objects())
        obj_vals = {o.name: [] for o in objs}
        frames = list(range(f0, f1 + 1))
        engine.on_strike = None
        ai = 0
        prev_t = None
        orig_frame = scene.frame_current
        for f in frames:
            scene.frame_set(f)              # a guitar animated at its root moves here
            t = (f - start_frame) / fps
            while ai < len(actions) and actions[ai][0] <= t:
                _, kind, payload = actions[ai]
                if kind == "fret":
                    engine.fret_chord(*payload)
                elif kind == "stroke":
                    engine.add_stroke(payload)
                else:
                    engine.release(payload[0], actions[ai][0], payload[1], payload[2])
                ai += 1
            engine.update(t, (t - prev_t) if prev_t is not None else 1.0 / fps)
            prev_t = t
            for pb in engine.controlled_bones():
                bone_vals[pb.name].append((pb.location.copy(), pb.rotation_quaternion.copy()))
            for o in objs:
                obj_vals[o.name].append(o.location.copy())

        # write keys over [f0, f1]
        for pb in engine.controlled_bones():
            pb.keyframe_insert("location", frame=f0, group=pb.name)
            if pb.parent is None:
                pb.keyframe_insert("rotation_quaternion", frame=f0, group=pb.name)
        curves = {(fc.data_path, fc.array_index): fc for fc in _fcurves(arm)}
        for pb in engine.controlled_bones():
            vals = bone_vals[pb.name]
            for axis in range(3):
                fc = curves[(f'pose.bones["{pb.name}"].location', axis)]
                _write_fcurve(fc, frames, [v[0][axis] for v in vals], f0, f1)
            if pb.parent is None:
                for axis in range(4):
                    fc = curves[(f'pose.bones["{pb.name}"].rotation_quaternion', axis)]
                    _write_fcurve(fc, frames, [v[1][axis] for v in vals], f0, f1)
        keyed_objs = 0
        for o in objs:
            vals = obj_vals[o.name]
            rest = engine.rest_loc[o.name]
            moving = [(v - rest).length > 1e-6 for v in vals]
            if not any(moving) and not (o.animation_data and o.animation_data.action):
                continue
            keep = [i for i in range(len(vals)) if moving[i] or (i > 0 and moving[i - 1])
                    or (i + 1 < len(vals) and moving[i + 1]) or i in (0, len(vals) - 1)]
            o.keyframe_insert("location", frame=f0, group="Guitar Strings")
            for axis in range(3):
                fc = next(c for c in _fcurves(o) if c.data_path == "location" and c.array_index == axis)
                _write_fcurve(fc, [frames[i] for i in keep], [vals[i][axis] for i in keep], f0, f1)
            keyed_objs += 1

        # the take now remembers the new strings and frets
        data["events"] = events
        take.save(scene, data)
        scene.frame_set(orig_frame)
    finally:
        sess._unmute_takes()
    msg = (f"Re-animated {len(groups)} note groups, {len(strokes)} strokes over frames {f0}-{f1}; "
           f"{keyed_objs} string controllers keyed")
    if settings.sound_auto:
        try:
            msg += " · " + take.build_sound(scene, settings)
        except Exception as e:
            msg += f" · couldn't build sound: {e}"
    return msg
