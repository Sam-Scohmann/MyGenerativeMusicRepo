"""Discovers the keyboard from the existing driver setup.

Every key has a driver on ["note_on"] that reads a property on the main
object (e.g. Piano_body["C#_2"]). That property name tells us the MIDI note,
so no naming convention on the key objects themselves is required.
"""
import statistics

from mathutils import Vector

from .notes import NOTE_NAMES, is_black, prop_to_midi


def find_main_object(scene):
    for obj in scene.objects:
        if "C_0" in obj.keys():
            return obj
    return None


def key_drivers(main_obj, objects):
    """Yields (key_object, prop_name) for every key driven by main_obj."""
    for obj in objects:
        ad = obj.animation_data
        if not ad:
            continue
        for fc in ad.drivers:
            if fc.data_path != '["note_on"]':
                continue
            for var in fc.driver.variables:
                tgt = var.targets[0]
                if tgt.id == main_obj and tgt.data_path.startswith('["'):
                    yield obj, tgt.data_path[2:-2]


def _polyline_point(points, frac):
    """Point at `frac` of the total length along a polyline."""
    if len(points) == 1:
        return points[0].copy()
    seg = [(a - b).length for a, b in zip(points[1:], points[:-1])]
    remaining = sum(seg) * max(0.0, min(1.0, frac))
    for (a, b), length in zip(zip(points[:-1], points[1:]), seg):
        if remaining <= length and length > 0:
            return a.lerp(b, remaining / length)
        remaining -= length
    return points[-1].copy()


def _guide_world_points(guide):
    sp = guide.data.splines[0] if guide.data.splines else None
    if sp is None:
        return []
    if sp.type == 'BEZIER':
        cos = [bp.co for bp in sp.bezier_points]
    else:
        cos = [Vector(p.co[:3]) for p in sp.points]
    return [guide.matrix_world @ Vector(c) for c in cos]


def _bbox_touch_local(key, frac):
    """Fallback without a guide curve: top of the key, `frac` from the end
    farthest from the pivot (keys pivot at the back, so that's the front)."""
    bb = [Vector(c) for c in key.bound_box]
    ys = [v.y for v in bb]
    front, back = (min(ys), max(ys)) if abs(min(ys)) > abs(max(ys)) else (max(ys), min(ys))
    x = (min(v.x for v in bb) + max(v.x for v in bb)) / 2
    z = max(v.z for v in bb)
    return Vector((x, front + (back - front) * frac, z))


class KeyInfo:
    __slots__ = ("note", "prop", "obj", "black", "touch_local", "lateral")

    def __init__(self, note, prop, obj, touch_local):
        self.note = note
        self.prop = prop
        self.obj = obj
        self.black = is_black(note)
        self.touch_local = touch_local
        self.lateral = 0.0

    def touch_world(self):
        """Follows the key as it rotates down."""
        return self.obj.matrix_world @ self.touch_local


class KeyMap:
    def __init__(self, scene, main_obj, settings):
        self.main = main_obj
        self.octave_offset = settings.octave_offset
        self.keys = {}  # midi note -> KeyInfo
        for key, prop in key_drivers(main_obj, scene.objects):
            note = prop_to_midi(prop, settings.octave_offset)
            if note is None:
                continue
            frac = settings.touch_depth_black if is_black(note) else settings.touch_depth_white
            guide = next((c for c in key.children if c.type == 'CURVE' and c.name.endswith("Guide")), None)
            pts = _guide_world_points(guide) if guide else []
            if pts:
                touch_local = key.matrix_world.inverted() @ _polyline_point(pts, frac)
            else:
                touch_local = _bbox_touch_local(key, frac)
            self.keys[note] = KeyInfo(note, prop, key, touch_local)

        # Props on the main object that have no key (still set them so the
        # user's custom drivers keep working)
        self.props = {prop_to_midi(p, settings.octave_offset): p for p in main_obj.keys()
                      if prop_to_midi(p, settings.octave_offset) is not None}

    def __bool__(self):
        return bool(self.keys)

    def get(self, note):
        return self.keys.get(note)

    def prop_for(self, note):
        info = self.keys.get(note)
        return info.prop if info else self.props.get(note)

    def note_range(self):
        return (min(self.keys), max(self.keys)) if self.keys else (0, 0)

    def keyboard_frame(self, to_local):
        """Returns (axis, forward, up, white_key_width) in the space given by
        the `to_local` matrix, and stores each key's lateral coordinate."""
        lo, hi = self.note_range()
        p_lo = to_local @ self.keys[lo].touch_world()
        p_hi = to_local @ self.keys[hi].touch_world()
        up = (to_local.to_3x3() @ Vector((0, 0, 1))).normalized()
        axis = p_hi - p_lo
        axis -= up * axis.dot(up)
        axis.normalize()
        forward = up.cross(axis)

        whites = sorted((n, k) for n, k in self.keys.items() if not k.black)
        blacks = [k for k in self.keys.values() if k.black]
        if whites and blacks:
            w_mean = sum(((to_local @ k.touch_world()) for _, k in whites), Vector()) / len(whites)
            b_mean = sum(((to_local @ k.touch_world()) for k in blacks), Vector()) / len(blacks)
            if (b_mean - w_mean).dot(forward) < 0:
                forward.negate()

        for k in self.keys.values():
            k.lateral = (to_local @ k.touch_world()).dot(axis)
        diffs = [b.lateral - a.lateral for (_, a), (_, b) in zip(whites, whites[1:])]
        width = statistics.median(diffs) if diffs else 1.0
        return axis, forward, up, abs(width)


def describe(note, octave_offset):
    return f"{NOTE_NAMES[note % 12]}_{note // 12 - octave_offset}"
