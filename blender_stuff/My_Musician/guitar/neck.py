"""The guitar neck as a grid of (string, fret), read from the Guitar setup.

The setup builds, per string s:
    STRING_s / STRING_s_END        nut and bridge ends
    FRET_f_STRING_s_CTRL           fret wire f (hooked vertex), with a "note" property
    VIBRATE_i_STRING_s_CTRL        points between the last fret and the bridge

Everything is re-read from the *evaluated* objects, so a guitar animated at
its root (GUITAR_ROOT or any parent) is followed every tick.

Neck frame (world space):
    u   along the strings, nut -> bridge
    v   across the strings, string 1 -> highest-numbered string
    n   out of the fretboard, towards the player's hands
"""
import re

import bpy
from mathutils import Matrix, Vector

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
START_RE = re.compile(r"^STRING_(\d+)$")
FRET_RE = re.compile(r"^FRET_(\d+)_STRING_(\d+)_CTRL$")
VIB_RE = re.compile(r"^VIBRATE_(\d+)_STRING_(\d+)_CTRL$")
TOUCH = 0.7  # where a fingertip presses between two fret wires (0 = previous wire, 1 = this wire)


def note_to_midi(name):
    """'E2' -> 40 (scientific pitch: C4 = 60)."""
    m = re.fullmatch(r"\s*([A-Ga-g]#?)(-?\d+)\s*", name or "")
    if not m:
        return None
    return (int(m.group(2)) + 1) * 12 + NOTE_NAMES.index(m.group(1).upper())


class Neck:
    def __init__(self):
        objs = bpy.data.objects
        self.strings = {}       # s -> (start obj, end obj)
        self.frets = {}         # s -> {f: obj}
        self.vibrate = {}       # s -> [obj sorted by index]
        for o in objs:
            m = START_RE.match(o.name)
            if m and objs.get(f"{o.name}_END"):
                self.strings[int(m.group(1))] = (o, objs[f"{o.name}_END"])
            m = FRET_RE.match(o.name)
            if m:
                self.frets.setdefault(int(m.group(2)), {})[int(m.group(1))] = o
            m = VIB_RE.match(o.name)
            if m:
                self.vibrate.setdefault(int(m.group(2)), []).append((int(m.group(1)), o))
        self.vibrate = {s: [o for _, o in sorted(v, key=lambda x: x[0])] for s, v in self.vibrate.items()}
        self.numbers = sorted(s for s in self.strings if self.frets.get(s))
        if len(self.numbers) < 2:
            raise RuntimeError("No guitar strings found: build them in the Guitar setup panel first "
                               "(STRING_n empties + Create Strings)")
        self.max_fret = min(max(self.frets[s]) for s in self.numbers)

        # tuning: each fret controller knows its note; open = note - fret
        self.open = {}
        for s in self.numbers:
            f, o = min(self.frets[s].items())
            midi = note_to_midi(o.get("note", ""))
            if midi is None:
                raise RuntimeError(f"Fret controllers of string {s} have no note names: "
                                   "run Update Note Names in the Guitar setup panel")
            self.open[s] = midi - f
        self.update()

    # -----------------------------
    def world(self, obj):
        return obj.evaluated_get(self.depsgraph).matrix_world.translation.copy()

    def update(self):
        """Re-read the neck from the evaluated scene (call once per tick)."""
        self.depsgraph = bpy.context.evaluated_depsgraph_get()
        first, last = self.numbers[0], self.numbers[-1]
        nut1, br1 = self.world(self.strings[first][0]), self.world(self.strings[first][1])
        nut_last = self.world(self.strings[last][0])
        u = (br1 - nut1).normalized()
        v = nut_last - nut1
        v = (v - u * v.dot(u)).normalized()
        self.u, self.v, self.n = u, v, u.cross(v)
        self.origin = nut1
        self.spacing = (nut_last - nut1).length / max(len(self.numbers) - 1, 1)
        self.frame = Matrix((
            (u.x, v.x, self.n.x, nut1.x),
            (u.y, v.y, self.n.y, nut1.y),
            (u.z, v.z, self.n.z, nut1.z),
            (0, 0, 0, 1)))
        self.frame_inv = self.frame.inverted()
        self._points = {}
        for s in self.numbers:
            start = self.world(self.strings[s][0])
            wires = [start] + [self.world(self.frets[s][f]) for f in range(1, self.max_fret + 1)]
            self._points[s] = wires

    def local(self, p):
        """World point -> neck frame (u, v, n)."""
        return self.frame_inv @ p

    def to_world(self, p):
        return self.frame @ p

    # -----------------------------
    def string_v(self, s):
        return self.local(self._points[s][0]).y

    def fret_point(self, s, f):
        """World point where a finger presses string s at fret f (f >= 1),
        or the nut for f = 0."""
        wires = self._points[s]
        if f <= 0:
            return wires[0].copy()
        f = min(f, self.max_fret)
        return wires[f - 1].lerp(wires[f], TOUCH)

    def wire(self, s, f):
        return self._points[s][min(max(f, 0), self.max_fret)].copy()

    def positions(self, midi):
        """Every (string, fret) that sounds `midi`."""
        out = []
        for s in self.numbers:
            f = midi - self.open[s]
            if 0 <= f <= self.max_fret:
                out.append((s, f))
        return out

    def midi_of(self, s, f):
        return self.open[s] + f

    def fold(self, midi):
        """A note moved by octaves into the guitar's range."""
        lo, hi = self.note_range()
        while midi > hi:
            midi -= 12
        while midi < lo:
            midi += 12
        return midi

    def note_range(self):
        return min(self.open.values()), max(self.open.values()) + self.max_fret
