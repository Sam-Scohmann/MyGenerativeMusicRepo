"""Piano audio for recorded takes.

Notes are read back from the main object's keyframed note properties
(C_0, C#_0, ...), each one is synthesised, and the whole take is mixed into a
single stereo WAV that is placed in the Video Sequencer, so it plays in sync
with timeline playback and is included in video renders.

(Speaker objects would need one NLA sound clip per note, and Blender's Python
API can only create those through an NLA-editor operator.)
"""
import math
import os
import tempfile
import wave

import bpy
import numpy as np

from .notes import prop_to_midi

STRIP_TAG = "piano_midi_take"   # custom property marking the strip we own
SAMPLE_RATE = 48000

_note_cache = {}  # (midi, rate) -> (soft, hard) float32 arrays


# -----------------------------
# SYNTH
# -----------------------------
def _synth_layer(midi, rate, brightness, rng):
    """A simple modal piano model: detuned strings with stretched partials,
    hammer-position comb, two-stage decay and a short hammer thump."""
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    length = float(np.clip(4.5 - (midi - 36) * 0.06, 1.5, 4.5))
    t = np.arange(int(length * rate), dtype=np.float32) / rate

    tau0 = float(np.clip(3.2 * 2.0 ** (-(midi - 48) / 18.0), 0.35, 5.0))
    inharm = 1e-4 * (1.0 + max(0, midi - 36) / 24.0)
    strings = 2 if midi < 45 else 3
    detune_cents = (-0.8, 0.8) if strings == 2 else (-0.9, 0.0, 0.9)
    nyquist_limit = min(rate * 0.45, 12000.0)

    out = np.zeros_like(t)
    n = 1
    while n <= 28:
        fn = n * f0 * math.sqrt(1.0 + inharm * n * n)
        if fn > nyquist_limit:
            break
        # struck at 1/7 of the string: the 7th partial is weak
        amp = (abs(math.sin(math.pi * n / 7.0)) + 0.12) / n ** (1.9 - 0.8 * brightness)
        tau = tau0 / (1.0 + 0.22 * (n - 1) * (1.0 + 0.5 * brightness))
        env = 0.65 * np.exp(-t / (tau * 0.22)) + 0.35 * np.exp(-t / tau)
        for cents in detune_cents:
            f = fn * 2.0 ** (cents / 1200.0)
            out += (amp / strings) * env * np.sin(2.0 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
        n += 1

    out *= 1.0 - np.exp(-t / 0.0015)  # 1.5 ms attack

    thump_len = int(0.03 * rate)
    noise = rng.standard_normal(thump_len).astype(np.float32)
    noise = np.convolve(noise, np.ones(24, dtype=np.float32) / 24, mode="same")
    out[:thump_len] += noise * np.exp(-t[:thump_len] / 0.006) * (0.15 + 0.25 * brightness)

    peak = float(np.max(np.abs(out))) or 1.0
    # keep low notes from overpowering: gentle loudness tilt
    return (out / peak * (0.55 + 0.45 * min(1.0, (midi - 24) / 48.0))).astype(np.float32)


def note_layers(midi, rate):
    key = (midi, rate)
    if key not in _note_cache:
        rng = np.random.default_rng(midi)
        _note_cache[key] = (_synth_layer(midi, rate, 0.15, rng), _synth_layer(midi, rate, 1.0, rng))
    return _note_cache[key]


# -----------------------------
# EVENTS FROM KEYFRAMES
# -----------------------------
def _note_fcurves(main, octave_offset):
    ad = main.animation_data
    if not ad or not ad.action:
        return []
    fcurves = None
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        bag = action_get_channelbag_for_slot(ad.action, ad.action_slot)
        fcurves = bag.fcurves if bag else []
    except ImportError:
        fcurves = ad.action.fcurves
    out = []
    for fc in fcurves:
        if fc.data_path.startswith('["') and fc.data_path.endswith('"]'):
            midi = prop_to_midi(fc.data_path[2:-2], octave_offset)
            if midi is not None:
                out.append((midi, fc))
    return out


def extract_events(main, octave_offset, fps):
    """Returns sorted (frame_on, frame_off, midi, velocity 0..1) from the
    keyframed note properties: a note starts when its value rises from 0."""
    events = []
    for midi, fc in _note_fcurves(main, octave_offset):
        points = sorted((p.co[0], p.co[1]) for p in fc.keyframe_points)
        prev = 0.0
        start = None
        for frame, value in points:
            if value > 0 and (prev <= 0 or (start is not None and value != prev)):
                if start is not None:  # re-struck while held
                    events.append((start[0], frame, midi, start[1]))
                start = (frame, value)
            elif value <= 0 < prev and start is not None:
                events.append((start[0], frame, midi, start[1]))
                start = None
            prev = value
        if start is not None:
            events.append((start[0], points[-1][0] + fps, midi, start[1]))
    events.sort()
    return events


# -----------------------------
# MIX
# -----------------------------
def mix(events, fps, rate, settings):
    """Mix events into a stereo float32 buffer. Returns (buffer, first frame)."""
    first = min(e[0] for e in events)
    release = max(settings.sound_release, 0.005)
    tail = int(release * 5 * rate)
    end = max(int((e[1] - first) / fps * rate) + tail for e in events)
    buf = np.zeros((end + rate // 10, 2), dtype=np.float32)

    for on, off, midi, vel in events:
        soft, hard = note_layers(midi, rate)
        note = soft + (hard - soft) * vel
        held = max(int((off - on) / fps * rate), int(0.02 * rate))
        if held < len(note):
            # dampers: exponential release after the key comes up
            n = min(len(note), held + tail)
            note = note[:n].copy()
            note[held:] *= np.exp(-np.arange(n - held, dtype=np.float32) / (release * rate))
        gain = settings.sound_volume * (0.25 + 0.75 * vel)
        pan = float(np.clip((midi - 60) / 30.0, -1.0, 1.0)) * settings.sound_stereo
        left = gain * math.cos((pan + 1) * math.pi / 4)
        right = gain * math.sin((pan + 1) * math.pi / 4)
        i = int((on - first) / fps * rate)
        buf[i:i + len(note), 0] += note * left
        buf[i:i + len(note), 1] += note * right

    peak = float(np.max(np.abs(buf))) if len(buf) else 0.0
    if peak > 0.98:
        buf *= 0.98 / peak
    return buf, first


def write_wav(path, buf, rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = (np.clip(buf, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


# -----------------------------
# SEQUENCER
# -----------------------------
def _audio_path(scene):
    name = bpy.path.clean_name(scene.name) + "_piano.wav"
    if bpy.data.filepath:
        return os.path.join(bpy.path.abspath("//piano_audio"), name), True
    return os.path.join(tempfile.gettempdir(), "piano_midi_hands", name), False


def remove_sound(scene):
    se = scene.sequence_editor
    if not se:
        return 0
    ours = [s for s in se.strips_all if s.get(STRIP_TAG)]
    for strip in ours:
        se.strips.remove(strip)
    return len(ours)


def build_sound(scene, main, settings):
    """(Re)builds the piano audio strip for the recorded take. Returns a message."""
    fps = scene.render.fps / scene.render.fps_base
    events = extract_events(main, settings.octave_offset, fps)
    if not events:
        raise RuntimeError(f"No recorded notes on '{main.name}' (record a take first)")

    rate = SAMPLE_RATE
    buf, first = mix(events, fps, rate, settings)
    path, saved = _audio_path(scene)
    write_wav(path, buf, rate)

    remove_sound(scene)
    # drop the previous take's sound datablock so the new file is really loaded
    for snd in list(bpy.data.sounds):
        if snd.users == 0 and bpy.path.abspath(snd.filepath) == path:
            bpy.data.sounds.remove(snd)
    se = scene.sequence_editor or scene.sequence_editor_create()
    used = {s.channel for s in se.strips_all}
    channel = next(c for c in range(1, 129) if c not in used)
    filepath = bpy.path.relpath(path) if saved else path
    strip = se.strips.new_sound("Piano Take", filepath, channel, int(round(first)))
    strip[STRIP_TAG] = True

    seconds = len(buf) / rate
    where = path if saved else f"{path} (save the .blend to keep it next to it)"
    return f"Piano sound: {len(events)} notes, {seconds:.1f}s, channel {channel} -> {where}"


# -----------------------------
# REPAIR OLD TAKES
# -----------------------------
def fix_recording(main, octave_offset):
    """Takes recorded before v1.2 have no key at their first frame, so keys
    show their first pressed value from the start. Adds a 0 key before each
    note property's first press. Returns the number of keys fixed."""
    fixed = 0
    for _midi, fc in _note_fcurves(main, octave_offset):
        points = sorted(fc.keyframe_points, key=lambda p: p.co[0])
        if points and points[0].co[1] > 0:
            fc.keyframe_points.insert(points[0].co[0] - 1, 0.0)
            fc.update()
            fixed += 1
    return fixed
