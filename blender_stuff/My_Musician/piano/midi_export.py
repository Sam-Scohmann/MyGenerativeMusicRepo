"""Export what was played as a Standard MIDI File (no dependencies).

Two sources:
    TAKE     the recorded take, read back from the note-property keyframes
             (survives saving the .blend; timing is snapped to frames)
    SESSION  the note log of the live session (exact timing, every note
             played, but only kept until Blender closes)
"""
import struct

import bpy
from bpy_extras.io_utils import ExportHelper

from . import live, piano_sound

PPQ = 480  # ticks per quarter note


# -----------------------------
# EVENTS: (seconds, note, velocity 0-127, 0 = off)
# -----------------------------
def events_from_take(scene, main, octave_offset):
    fps = scene.render.fps / scene.render.fps_base
    notes = piano_sound.extract_events(main, octave_offset, fps)
    if not notes:
        return []
    first = min(on for on, _, _, _ in notes)
    out = []
    for on, off, midi, value in notes:
        vel = max(1, min(127, round(value * 127)))
        out.append(((on - first) / fps, midi, vel))
        out.append(((off - first) / fps, midi, 0))
    return out


def events_from_log(log):
    if not log:
        return []
    first = min(t for t, _, vel in log if vel > 0) if any(vel > 0 for _, _, vel in log) else log[0][0]
    out = []
    held = set()
    for t, note, vel in log:
        t = max(0.0, t - first)
        if vel > 0:
            if note in held:  # re-struck while held: close the old one first
                out.append((t, note, 0))
            held.add(note)
            out.append((t, note, vel))
        elif note in held:
            held.discard(note)
            out.append((t, note, 0))
    end = max((t for t, _, _ in out), default=0.0)
    out.extend((end + 0.5, note, 0) for note in held)  # still sounding at export time
    return out


# -----------------------------
# SMF WRITER
# -----------------------------
def _vlq(value):
    """MIDI variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def write_midi(path, events, bpm=120.0, channel=1, track_name="Piano"):
    """events: (seconds, note, velocity) with velocity 0 meaning note off, or
    (seconds, note, velocity, channel 1-16) to put each event on its own channel."""
    tempo = round(60_000_000 / bpm)  # microseconds per quarter note
    ticks_per_second = PPQ * bpm / 60.0
    ch = (channel - 1) & 0x0F

    timed = sorted(((round(e[0] * ticks_per_second), e[2] > 0, e[1], e[2],
                     ((e[3] - 1) & 0x0F) if len(e) > 3 else ch) for e in events),
                   key=lambda e: (e[0], e[1]))  # offs before ons on the same tick
    track = bytearray()
    name = track_name.encode("utf-8")[:127]
    track += b"\x00\xFF\x03" + _vlq(len(name)) + name
    track += b"\x00\xFF\x51\x03" + tempo.to_bytes(3, "big")
    track += b"\x00\xFF\x58\x04\x04\x02\x18\x08"  # 4/4
    last = 0
    for tick, is_on, note, vel, c in timed:
        track += _vlq(tick - last)
        track += bytes((0x90 | c, note, vel)) if is_on else bytes((0x80 | c, note, 0))
        last = tick
    track += b"\x00\xFF\x2F\x00"

    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ))
        f.write(b"MTrk" + struct.pack(">I", len(track)) + track)
    return sum(1 for e in timed if e[1])


# -----------------------------
# OPERATOR
# -----------------------------
SOURCES = (
    ('TAKE', "Recorded Take", "Notes keyframed on the main object (saved with the .blend, frame-accurate)"),
    ('SESSION', "Live Session", "Every note of the current / last live session (exact timing, lost on restart)"),
)


class PIANO_OT_export_midi(bpy.types.Operator, ExportHelper):
    """Export the played notes as a .mid file"""
    bl_idname = "piano.export_midi"
    bl_label = "Export MIDI"

    filename_ext = ".mid"
    filter_glob: bpy.props.StringProperty(default="*.mid;*.midi", options={'HIDDEN'})

    source: bpy.props.EnumProperty(name="Source", items=SOURCES)
    bpm: bpy.props.FloatProperty(
        name="Tempo (BPM)", default=120.0, min=20.0, max=400.0,
        description="Written as the file's tempo. Timing is exact at any value; "
                    "matching the song's tempo makes notes line up with bars in a DAW")
    channel: bpy.props.IntProperty(name="Channel", default=1, min=1, max=16)

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.clean_name(context.scene.name) + "_piano.mid"
        # default to the live session if nothing has been recorded
        main = context.scene.piano_midi.main_object
        if not (main and piano_sound._note_fcurves(main, context.scene.piano_midi.octave_offset)):
            self.source = 'SESSION'
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "source")
        layout.prop(self, "bpm")
        layout.prop(self, "channel")

    def execute(self, context):
        s = context.scene.piano_midi
        if self.source == 'TAKE':
            if s.main_object is None:
                self.report({'ERROR'}, "No main object set")
                return {'CANCELLED'}
            events = events_from_take(context.scene, s.main_object, s.octave_offset)
            empty = "No recorded take (record one, or export the Live Session)"
        else:
            events = events_from_log(live.note_log())
            empty = "No notes played in this Blender session yet"
        if not events:
            self.report({'ERROR'}, empty)
            return {'CANCELLED'}
        try:
            count = write_midi(self.filepath, events, self.bpm, self.channel,
                               track_name=f"{context.scene.name} piano")
        except OSError as e:
            self.report({'ERROR'}, f"Couldn't write {self.filepath}: {e}")
            return {'CANCELLED'}
        seconds = max(t for t, _, _ in events)
        self.report({'INFO'}, f"Exported {count} notes ({seconds:.1f}s) to {self.filepath}")
        return {'FINISHED'}


classes = (PIANO_OT_export_midi,)
