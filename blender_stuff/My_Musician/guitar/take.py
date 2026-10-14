"""The recorded guitar take: a stream of note and strike events stored with
the scene, plus the sound and MIDI built from it.

Stored as JSON in scene["guitar_take"]:
    {"fps": 24, "start_frame": 1, "events": [
        {"t": 0.0,  "type": "on",     "midi": 52, "vel": 0.8},          # MIDI as it came in
        {"t": 0.02, "type": "strike", "midi": 52, "vel": 0.8, "string": 4, "fret": 2,
                                       "dir": "D", "muted": false},       # pick crossing a string
        {"t": 0.40, "type": "off",    "midi": 52},
        {"t": 0.41, "type": "release","string": 4}, ...]}
Times are seconds from the start of the take.
"""
import json
import os
import tempfile

import bpy

from . import synth
from ..piano import midi_export, piano_sound

PROP = "guitar_take"
STRIP_TAG = "guitar_take_strip"


def load(scene):
    try:
        data = json.loads(scene.get(PROP, "") or "{}")
    except ValueError:
        return None
    return data if data.get("events") else None


def save(scene, data):
    scene[PROP] = json.dumps(data, separators=(",", ":"))


def clear(scene):
    if PROP in scene:
        del scene[PROP]


class Recorder:
    """Collects events while live; merged into the scene's take on finish."""

    def __init__(self, scene, now):
        self.scene_name = scene.name
        self.t0 = now
        self.fps = scene.render.fps / scene.render.fps_base
        self.start_frame = scene.frame_current
        self.events = []

    def add(self, now, **event):
        event["t"] = round(now - self.t0, 5)
        self.events.append(event)

    def frame(self, now):
        return self.start_frame + (now - self.t0) * self.fps

    def finish(self, scene):
        """Merge into the stored take (a new take replaces events that overlap it)."""
        old = load(scene)
        events = self.events
        if old:
            shift = (old["start_frame"] - self.start_frame) / self.fps
            end = max((e["t"] for e in events), default=0.0)
            kept = [dict(e, t=round(e["t"] + shift, 5)) for e in old["events"]
                    if not (0.0 <= e["t"] + shift <= end + 0.5)]
            events = sorted(kept + events, key=lambda e: e["t"])
            start = min(self.start_frame, old["start_frame"])
            if start != self.start_frame:
                d = (self.start_frame - start) / self.fps
                events = [dict(e, t=round(e["t"] + d, 5)) for e in events]
        else:
            start = self.start_frame
        save(scene, {"fps": self.fps, "start_frame": start, "events": events})
        return len(self.events)


# -----------------------------
# EVENTS -> NOTES
# -----------------------------
def strikes(data):
    """[(t, midi, vel, end, string, muted)] - each pluck lasts until the
    string is released, re-plucked, or plays another note."""
    out = []
    open_on = {}  # string -> index in out
    for e in sorted(data["events"], key=lambda e: e["t"]):
        if e["type"] == "strike":
            s = e["string"]
            if s in open_on:
                i = open_on[s]
                out[i] = out[i][:3] + (e["t"],) + out[i][4:]
            out.append((e["t"], e["midi"], e["vel"], None, s, bool(e.get("muted"))))
            open_on[s] = len(out) - 1
        elif e["type"] == "release" and e.get("string") in open_on:
            i = open_on.pop(e["string"])
            out[i] = out[i][:3] + (e["t"],) + out[i][4:]
    return out


def midi_events(data, per_string=False):
    """(seconds, note, velocity[, channel]) for MIDI export: the plucks as heard."""
    out = []
    for t, midi, vel, end, string, muted in strikes(data):
        v = max(1, min(127, round(vel * 127)))
        end = end if end is not None else t + 1.0
        if per_string:
            out += [(t, midi, v, string), (end, midi, 0, string)]
        else:
            out += [(t, midi, v), (end, midi, 0)]
    first = min((e[0] for e in out), default=0.0)
    return [(e[0] - first,) + e[1:] for e in out]


def export_midi(scene, path, bpm=120.0, per_string=False):
    data = load(scene)
    if not data:
        raise RuntimeError("No recorded guitar take: record one first")
    events = midi_events(data, per_string)
    return midi_export.write_midi(path, events, bpm, 1, track_name=f"{scene.name} guitar")


# -----------------------------
# SOUND
# -----------------------------
def _audio_path(scene):
    stem = bpy.path.clean_name(os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled")
    name = f"{stem}_{bpy.path.clean_name(scene.name)}_guitar.wav"
    if bpy.data.filepath:
        return os.path.join(bpy.path.abspath("//guitar_audio"), name), True
    return os.path.join(tempfile.gettempdir(), "my_musician", name), False


def remove_sound(scene):
    se = scene.sequence_editor
    if not se:
        return 0
    ours = [s for s in se.strips_all if s.get(STRIP_TAG)]
    for strip in ours:
        se.strips.remove(strip)
    return len(ours)


def build_sound(scene, settings):
    data = load(scene)
    if not data:
        raise RuntimeError("No recorded guitar take: record one first")
    notes = strikes(data)
    if not notes:
        raise RuntimeError("The take has no plucked notes")
    buf, first = synth.render(notes, synth.RATE, settings.sound_volume, settings.sound_release,
                              settings.sound_stereo)
    path, saved = _audio_path(scene)
    piano_sound.write_wav(path, buf, synth.RATE)
    remove_sound(scene)
    for snd in list(bpy.data.sounds):
        if snd.users == 0 and bpy.path.abspath(snd.filepath) == path:
            bpy.data.sounds.remove(snd)
    se = scene.sequence_editor or scene.sequence_editor_create()
    used = {s.channel for s in se.strips_all}
    channel = next(c for c in range(1, 129) if c not in used)
    frame = int(round(data["start_frame"] + first * data["fps"]))
    strip = se.strips.new_sound("Guitar Take", bpy.path.relpath(path) if saved else path, channel, frame)
    strip[STRIP_TAG] = True
    where = path if saved else f"{path} (save the .blend to keep it next to it)"
    return f"Guitar sound: {len(notes)} plucks, {len(buf) / synth.RATE:.1f}s from frame {frame} -> {where}"
