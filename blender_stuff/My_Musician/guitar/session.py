"""The live guitar session: MIDI in -> fingering -> picking/strumming -> hands,
strings, optional live sound and recording."""
import math
import time

import bpy

from . import fingering, synth, take
from .engine import GuitarEngine
from .neck import Neck
from ..piano import midi_io
from ..piano.live import _fcurves

TEST_PIECE_TEMPO = 96


def find_armature(scene):
    for o in scene.objects:
        if o.type == 'ARMATURE' and "hand_control_l" in o.pose.bones and "hand_control_r" in o.pose.bones:
            return o
    return None


def test_piece():
    """A plain I-V-vi-IV progression strummed, then a C major scale picked.
    Returns [(seconds, 'on'|'off', midi, velocity)]."""
    beat = 60.0 / TEST_PIECE_TEMPO
    chords = [(48, 52, 55, 60, 64), (43, 47, 50, 55, 59, 67), (45, 52, 57, 60, 64), (41, 48, 53, 57, 60)]
    out, t = [], 0.0
    for rep in range(2):
        for chord in chords:
            for k in range(2):               # two strums per chord
                for m in chord:
                    out += [(t, "on", m, 92 - 8 * k), (t + beat * 0.9, "off", m, 0)]
                t += beat
    t += beat
    scale = [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67, 65, 64, 62, 60, 59, 57, 55, 53, 52, 50, 48]
    for m in scale:
        out += [(t, "on", m, 84), (t + beat * 0.45, "off", m, 0)]
        t += beat * 0.5
    return sorted(out, key=lambda e: (e[0], e[1] == "on"))


class LiveSound:
    """Plays plucks through Blender's audio device (aud), if there is one."""

    def __init__(self):
        self.device = None
        try:
            import aud
            self.aud = aud
            self.device = aud.Device()
        except Exception as e:  # no audio device (e.g. background mode)
            print(f"[MyMusician] Live guitar sound unavailable: {e}")
        self.handles = []

    def play(self, midi, vel, muted, volume):
        if self.device is None:
            return
        import numpy as np
        sig = synth.note(midi, vel, muted) * (volume * (0.3 + 0.7 * vel))
        try:
            snd = self.aud.Sound.buffer(np.ascontiguousarray(sig.reshape(-1, 1)), float(synth.RATE))
            self.handles.append(self.device.play(snd))
            self.handles = self.handles[-24:]
        except Exception as e:
            print(f"[MyMusician] Live guitar sound failed: {e}")
            self.device = None


class Session:
    def __init__(self, scene, settings):
        self.scene_name = scene.name
        self.neck = Neck()
        arm = settings.armature or find_armature(scene)
        if arm is None:
            raise RuntimeError("No hands armature with hand_control_l / hand_control_r found")
        settings.armature = arm
        self.arm = arm
        self.engine = GuitarEngine(self.neck, arm, settings)
        self.engine.on_strike = self._on_strike
        self.muted = {}
        self._mute_takes()
        self.state = {"p": self.engine.p0, "t": -1e9, "strings": [], "ringing": {}}
        self.pending = []        # [(time, midi, vel, string hint)]
        self.held = {}           # midi -> (string, fret, vel)
        self.pattern_on = False
        self.pattern_t0 = 0.0
        self.pattern_slot = -1
        self.port = None
        self.port_name = None
        self.port_error = None
        self.demo = []
        self.demo_t0 = 0.0
        self.start = time.perf_counter()
        self.last = self.start
        self.recorder = None
        self.last_key_frame = None
        self.sound = LiveSound() if settings.live_sound else None
        self.message = None
        self.dropped = 0         # notes that didn't fit a hand shape (or the guitar's range)
        self.moved = 0           # notes played an octave away so the chord fits one hand
        self.played_as = {}      # sent midi -> midi actually fretted
        self.unstruck = set()    # strings fretted for a note whose pluck hasn't happened yet
        self.late_offs = []      # note-offs waiting for their note to be plucked first

    @property
    def scene(self):
        return bpy.data.scenes.get(self.scene_name)

    # -----------------------------
    # MIDI
    # -----------------------------
    def open_port(self, settings):
        name = settings.port_name
        if not name:
            self.port_error = "No MIDI input selected: running without MIDI (test piece only)"
        elif not midi_io.is_connected(name):
            self.port_error = f"MIDI input '{name}' isn't connected"
        else:
            try:
                self.port = midi_io.open_input(name)
                self.port_name = name
                self.port_error = None
            except Exception as e:
                self.port_error = f"Failed to open '{name}' (is the piano listening on it?): {e}"
        return self.port_error

    def close_port(self):
        if self.port:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
        self.port_name = None

    def switch_port(self, settings):
        self.close_port()
        self.all_off()
        self.open_port(settings)

    # -----------------------------
    # NOTES
    # -----------------------------
    def note_on(self, midi, vel, now, string_hint=None):
        if self.scene.guitar_live.fold_octaves:
            midi = self.neck.fold(midi)
        self.pending.append((now, midi, vel / 127.0, string_hint))
        if self.recorder:
            self.recorder.add(now, type="on", midi=midi, vel=round(vel / 127.0, 3))

    def note_off(self, midi, now):
        settings = self.scene.guitar_live
        if settings.fold_octaves:
            midi = self.neck.fold(midi)
        if self.recorder:
            self.recorder.add(now, type="off", midi=midi)
        if any(p[1] == midi for p in self.pending):
            # a short note (or a slow tick) - its note-off came before it was played:
            # play it now and let it go once the pick has struck it
            self._flush(settings, now, force=True)
        info = self.held.get(midi)
        if info is not None and info[0] in self.unstruck:
            if midi not in self.late_offs:
                self.late_offs.append(midi)
            return
        self._release(midi, now)

    def _release(self, midi, now):
        info = self.held.pop(midi, None)
        if info is None:
            return
        string = info[0]
        played = self.played_as.pop(midi, midi)   # the octave it was actually played at
        if self.engine.string_state[string]["midi"] in (played, None):
            self.engine.release(string, now, played)
            self.state["ringing"].pop(string, None)
            if self.recorder:
                self.recorder.add(now, type="release", string=string)

    def all_off(self):
        now = time.perf_counter()
        for midi in list(self.held):
            self.note_off(midi, now)
        self.pending = []
        self.demo = []
        self.pattern_on = False
        self.unstruck = set()
        for midi in self.late_offs:
            self._release(midi, now)
        self.late_offs = []

    def _flush(self, settings, now, force=False):
        if not self.pending or (not force and now - self.pending[0][0] < settings.chord_window):
            return
        group, self.pending = self.pending, []
        midis = [m for _, m, _, _ in group]
        vel = {m: v for _, m, v, _ in group}
        hints = {m: h for _, m, _, h in group if h}
        # notes still held keep their strings unless this group needs them
        ringing = {s: m for m, (s, f, v) in self.held.items()}
        self.state["ringing"] = ringing
        choice = fingering.choose_live(self.neck, midis, self.state, now, hints,
                                       octaves=settings.octave_far_notes)
        if choice is None:
            return
        voicing, p, fingers, barre = choice
        played = {voicing.sent(m) for m, _, _ in voicing.notes}
        self.dropped += len(set(midis) - played)
        self.moved += len(voicing.source)
        # keep fingers of held notes on strings this voicing doesn't touch
        for m, (s, f, v) in list(self.held.items()):
            if s in voicing.strings:
                del self.held[m]
        held_fingers = {(s, f): 0 for m, (s, f, v) in self.held.items()}
        self.engine.fret_chord(voicing, p, {**{k: 0 for k in held_fingers}, **fingers}, barre)
        self.state.update(p=p, t=now, strings=voicing.strings)
        notes = [(s, m, vel[voicing.sent(m)], f) for m, s, f in voicing.notes]
        for s, m, v, f in notes:
            self.held[voicing.sent(m)] = (s, f, v)   # keyed by the note that was sent
            if voicing.sent(m) in self.late_offs:      # re-struck: the old note-off is done
                self.late_offs.remove(voicing.sent(m))
            self.played_as[voicing.sent(m)] = m
        mode = settings.mode
        if mode != 'PATTERN':
            self.unstruck |= {s for s, _, _, _ in notes}
        if mode == 'PATTERN':
            if not self.pattern_on:
                self.pattern_on = True
                self.pattern_t0 = now + 0.05
                self.pattern_slot = -1
            return
        if mode == 'MELODY' or (mode == 'AUTO' and len(notes) == 1):
            for s, m, v, f in sorted(notes, key=lambda n: n[0]):
                self.engine.pluck(s, m, v, f, now)
        else:
            self.engine.strum_chord(notes, now)

    def _pattern(self, settings, now):
        if not self.pattern_on:
            return
        if not self.held:
            self.pattern_on = False
            return
        pat = settings.pattern.strip() or "D"
        slot_len = (60.0 / settings.tempo) * 4.0 / len(pat)
        # schedule the next slot a little ahead so the pick can approach
        nxt = self.pattern_slot + 1
        t_slot = self.pattern_t0 + nxt * slot_len
        if now < t_slot - settings.pick_approach - 0.02:
            return
        self.pattern_slot = nxt
        c = pat[nxt % len(pat)].upper()
        notes = [(s, m, v, f) for m, (s, f, v) in self.held.items()]
        direction = 1 if nxt % 2 == 0 else -1
        if c == "D":
            self.engine.strum_chord(notes, now, direction=1, at=t_slot)
        elif c == "U":
            # up strokes usually catch only the top strings
            top = sorted(notes, key=lambda n: n[0])[-4:]
            self.engine.strum_chord(top, now, direction=-1, at=t_slot)
        elif c == "X":
            self.engine.strum_chord(notes, now, direction=1, muted=True, at=t_slot)
        else:
            span = sorted({n[0] for n in notes}) or self.neck.numbers
            self.engine.strum_chord([], now, direction=direction, dig=False, at=t_slot, span=span)

    def _on_strike(self, t, string, midi, vel, fret, direction, muted):
        settings = self.scene.guitar_live
        self.state["ringing"][string] = midi
        self.unstruck.discard(string)
        if self.sound is not None:
            self.sound.play(midi, vel, muted, settings.sound_volume)
        if self.recorder:
            self.recorder.add(t, type="strike", midi=midi, vel=round(vel, 3), string=string,
                              fret=fret, dir=direction, muted=muted)

    def start_demo(self):
        self.demo = test_piece()
        self.demo_t0 = time.perf_counter()

    # -----------------------------
    # TICK
    # -----------------------------
    def tick(self, settings):
        now = time.perf_counter()
        dt = min(now - self.last, 0.1)
        self.last = now
        if self.port:
            for msg in self.port.iter_pending():
                if msg.type not in ("note_on", "note_off"):
                    continue
                if settings.channel and msg.channel != settings.channel - 1:
                    continue
                hint = msg.channel + 1 if settings.channel_per_string else None
                if msg.type == "note_on" and msg.velocity > 0:
                    self.note_on(msg.note, msg.velocity, now, hint)
                else:
                    self.note_off(msg.note, now)
        while self.demo and self.demo[0][0] <= now - self.demo_t0:
            _, kind, m, v = self.demo.pop(0)
            if kind == "on":
                self.note_on(m, v, now)
            else:
                self.note_off(m, now)
        self._flush(settings, now)
        self._pattern(settings, now)
        self.engine.update(now, dt)
        self._late_offs(now)
        if settings.record:
            self._record(now)
        elif self.recorder:
            self._finish_recording(settings)

    def _late_offs(self, now):
        waiting = []
        for midi in self.late_offs:
            info = self.held.get(midi)
            if info is None:
                continue                     # its string was taken by a later chord
            if info[0] in self.unstruck:
                waiting.append(midi)
            else:
                self._release(midi, now)
        self.late_offs = waiting

    # -----------------------------
    # RECORDING
    # -----------------------------
    def _keyable(self):
        yield from ((pb, ("location", "rotation_quaternion") if pb.parent is None else ("location",))
                    for pb in self.engine.controlled_bones())

    def _record(self, now):
        scene = self.scene
        if self.recorder is None:
            self.recorder = take.Recorder(scene, now)
            self.last_key_frame = None
            self.keyed_strings = set()
        frame = int(round(self.recorder.frame(now)))
        if frame == self.last_key_frame:
            return
        for pb, paths in self._keyable():
            for path in paths:
                pb.keyframe_insert(path, frame=frame, group=pb.name)
        dirty = set(self.engine.dirty_strings)
        for obj in self.engine.string_objects(dirty | self.keyed_strings):
            obj.keyframe_insert("location", frame=frame, group="Guitar Strings")
        self.keyed_strings = dirty
        self.engine.dirty_strings = set()
        self.last_key_frame = frame
        self._mute_takes()

    def _finish_recording(self, settings):
        rec, self.recorder = self.recorder, None
        if rec is None:
            return
        scene = self.scene
        count = rec.finish(scene)
        # close the take with the rest pose, so the final keyframe stays the rest pose
        end = self.last_key_frame or scene.frame_current
        frame = end + max(1, round(0.75 * rec.fps))
        for pb, paths in self._keyable():
            saved = pb.matrix_basis.copy()
            start = self.engine.start_location(pb)
            if pb.parent is None:
                pb.matrix_basis = start
            else:
                pb.location = start
            for path in paths:
                pb.keyframe_insert(path, frame=frame, group=pb.name)
            pb.matrix_basis = saved
        self.message = f"Recorded {count} guitar events"
        if settings.sound_auto:
            try:
                self.message += " · " + take.build_sound(scene, settings)
            except Exception as e:
                self.message += f" · couldn't build sound: {e}"
        print(f"[MyMusician] {self.message}")

    # -----------------------------
    # TAKE MUTING (recorded keys must not fight live input)
    # -----------------------------
    def _take_fcurves(self):
        bones = {f'pose.bones["{pb.name}"]' for pb in self.engine.controlled_bones()}
        for fc in _fcurves(self.arm):
            if any(fc.data_path.startswith(b) for b in bones):
                yield fc
        for obj in self.engine.string_objects():
            yield from _fcurves(obj)

    def _mute_takes(self):
        for fc in self._take_fcurves():
            key = (fc.id_data.name, fc.data_path, fc.array_index)
            if key not in self.muted:
                self.muted[key] = (fc, fc.mute)
                fc.mute = True

    def _unmute_takes(self):
        for fc, was in self.muted.values():
            try:
                fc.mute = was
            except ReferenceError:
                pass
        self.muted = {}

    def stop(self, settings):
        self.close_port()
        now = time.perf_counter()
        self.all_off()
        if self.recorder:
            self.engine.update(now, 0.0)
            self.last_key_frame = None
            self._record(now + 1.0 / max(self.recorder.fps, 1))
            self._finish_recording(settings)
        self._unmute_takes()
        self.engine.restore()


_session = None


def current():
    return _session


def is_running():
    return _session is not None


def _timer():
    global _session
    if _session is None:
        return None
    scene = _session.scene
    if scene is None:
        stop()
        return None
    try:
        _session.tick(scene.guitar_live)
    except ReferenceError:
        _session = None
        return None
    wm = bpy.context.window_manager
    if wm:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    return 1.0 / 60.0


def start(context, demo=False):
    global _session
    if _session:
        return None
    settings = context.scene.guitar_live
    s = Session(context.scene, settings)
    warning = s.open_port(settings)
    _session = s
    if demo:
        s.start_demo()
    bpy.app.timers.register(_timer, first_interval=0.0)
    return warning


def stop():
    global _session
    s, _session = _session, None
    if s is None:
        return None
    if s.scene is not None:
        s.stop(s.scene.guitar_live)
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    return s.message


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    stop()


def register():
    bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    stop()
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
