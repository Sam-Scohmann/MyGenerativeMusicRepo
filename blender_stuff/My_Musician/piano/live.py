"""The live session: one timer that drains MIDI (and/or the demo), presses
keys through the main object's properties and moves the hands."""
import time

import bpy
from mathutils import Vector

from . import demo, midi_io, piano_sound
from .hands import HandsEngine, controlled_bone_names, find_sides
from .keymap import KeyMap, find_main_object


class Session:
    def __init__(self, scene, settings, rest_from_keys=False):
        """rest_from_keys: put the hands in their keyed rest pose first (Re-animate).
        Live sessions never touch the pose: the pose the hands are in when
        Start is pressed is their rest pose."""
        self.scene_name = scene.name
        self.main = settings.main_object or find_main_object(scene)
        if self.main is None:
            raise RuntimeError("No main object: set one, or select the object holding C_0, C#_0, ... properties")
        settings.main_object = self.main

        self.keymap = KeyMap(scene, self.main, settings)
        arm = settings.armature or next((o for o in scene.objects
                                         if o.type == 'ARMATURE' and find_sides(o)), None)
        self.arm = arm if settings.use_hands else None
        if self.arm:
            settings.armature = arm

        # mute recorded takes before anything is evaluated (see _mute_takes)
        self.muted = {}  # (action name, data path, index) -> (fcurve, original mute)
        self._mute_takes()
        if self.arm and rest_from_keys:
            apply_rest_pose(self.arm)

        self._zero_props()
        bpy.context.view_layer.update()  # keys back at rest before reading touch points
        self.keymap = KeyMap(scene, self.main, settings)
        if not self.keymap:
            raise RuntimeError(f"No keys are driven by '{self.main.name}'. Run 'Build Key Drivers' first")

        self.engine = None
        if self.arm:
            self.engine = HandsEngine(self.arm, self.keymap, settings) or None

        self.port = None
        self.port_name = None
        self.port_error = None
        self.demo_events = []
        self.demo_start = 0.0
        self.last_time = time.perf_counter()
        # every note played this session: (seconds since start, midi note, velocity 0-127)
        self.start_time = self.last_time
        self.note_log = []
        self.ignored = {}  # midi note -> times played without a key to press

        self.record_start_time = None
        self.record_start_frame = 0
        self.last_record_frame = 0
        self.last_record_message = None
        self.prop_values = {}
        self.recorded_props = {}

    @property
    def scene(self):
        return bpy.data.scenes.get(self.scene_name)

    # -----------------------------
    # MIDI
    # -----------------------------
    def key_gaps(self):
        """MIDI notes inside the keyboard's range that no key responds to."""
        lo, hi = self.keymap.note_range()
        return [n for n in range(lo, hi + 1) if n not in self.keymap.keys]

    def open_port(self, settings):
        """Opens settings.port_name. Returns a warning string, or None on success."""
        self.port_error = self._open(settings.port_name)
        return self.port_error

    def _open(self, name):
        if not name:
            return "No MIDI input selected: running without MIDI (test piece only)"
        if not midi_io.backend_name():
            return "No MIDI support (install mido): running without MIDI (test piece only)"
        if not midi_io.is_connected(name):
            return f"MIDI input '{name}' isn't connected: running without MIDI"
        try:
            self.port = midi_io.open_input(name)
            self.port_name = name
        except Exception as e:
            return f"Failed to open MIDI input '{name}': {e}"
        return None

    def switch_port(self, settings):
        """Called when the user picks another input while live."""
        self.close_port()
        self.all_notes_off(settings)  # notes from the old input would never get their note-off
        warning = self.open_port(settings)
        print(f"[PianoMIDI] {warning or f'Listening on {self.port_name}'}")

    def close_port(self):
        if self.port:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
        self.port_name = None

    # -----------------------------
    # NOTES
    # -----------------------------
    def _zero_props(self):
        for prop in self.keymap.props.values():
            if self.main.get(prop):
                self.main[prop] = 0.0
        self.main.update_tag()

    def set_note(self, note, velocity, now, settings):
        prop = self.keymap.prop_for(note)
        if prop is None:
            if velocity > 0:
                self.ignored[note] = self.ignored.get(note, 0) + 1
            return
        value = velocity / 127.0
        self.note_log.append((now - self.start_time, note, int(velocity)))
        self.main[prop] = value
        self.main.update_tag()
        self.prop_values[prop] = value
        info = self.keymap.get(note)
        if info:
            # Also write the key's own note_on (its driver computes the same value).
            # Tagging the key re-copies its original properties into the evaluated
            # copy, and with the stale 0 there the key sometimes didn't go down.
            info.obj["note_on"] = value
            info.obj.update_tag()
        if self.engine:
            if value > 0:
                self.engine.note_on(note, now, settings)
            else:
                self.engine.note_off(note, now)

    def all_notes_off(self, settings):
        now = time.perf_counter()
        for note, prop in self.keymap.props.items():
            if self.prop_values.get(prop, 0.0) or self.main.get(prop, 0.0):
                self.set_note(note, 0, now, settings)
        self.demo_events = []

    # -----------------------------
    # TAKE MUTING
    # -----------------------------
    def _take_fcurves(self):
        """F-curves of recorded takes that drive what we animate live."""
        props = {f'["{p}"]' for p in self.keymap.props.values()}
        yield from (fc for fc in _fcurves(self.main) if fc.data_path in props)
        if self.arm:
            bones = {f'pose.bones["{name}"].location' for name in controlled_bone_names(self.arm)}
            yield from (fc for fc in _fcurves(self.arm) if fc.data_path in bones)

    def _mute_takes(self):
        """While live, recorded keyframes must not be evaluated: Blender writes
        animated values back onto the properties, which would undo live input."""
        for fc in self._take_fcurves():
            key = (fc.id_data.name, fc.data_path, fc.array_index)
            if key not in self.muted:
                self.muted[key] = (fc, fc.mute)
                fc.mute = True

    def _unmute_takes(self):
        for fc, was_muted in self.muted.values():
            try:
                fc.mute = was_muted
            except ReferenceError:
                pass
        self.muted = {}

    def start_demo(self):
        self.demo_events = demo.events()
        self.demo_start = time.perf_counter()

    # -----------------------------
    # TICK
    # -----------------------------
    def tick(self, settings):
        now = time.perf_counter()
        dt = min(now - self.last_time, 0.1)
        self.last_time = now

        if self.port:
            for msg in self.port.iter_pending():
                if msg.type not in ("note_on", "note_off"):
                    continue
                if settings.channel and msg.channel != settings.channel - 1:
                    continue
                vel = msg.velocity if msg.type == "note_on" else 0
                self.set_note(msg.note, vel, now, settings)

        while self.demo_events and self.demo_events[0][0] <= now - self.demo_start:
            _, kind, note, vel = self.demo_events.pop(0)
            self.set_note(note, vel if kind == "on" else 0, now, settings)

        if self.engine:
            self.engine.update(dt, now, settings)

        if settings.record:
            self._record(now)
        elif self.record_start_time is not None:
            self._finish_recording(settings)

    # -----------------------------
    # RECORDING
    # -----------------------------
    def _record(self, now, force=False):
        scene = self.scene
        if self.record_start_time is None:
            self._begin_recording(now)
        fps = scene.render.fps / scene.render.fps_base
        frame = self.record_start_frame + round((now - self.record_start_time) * fps)
        if frame <= self.last_record_frame:
            if not force:
                return
            frame = self.last_record_frame + 1

        for prop, value in self.prop_values.items():
            last = self.recorded_props.get(prop, (None, 0.0))
            if last[1] == value:
                continue
            path = f'["{prop}"]'
            if last[0] is not None and last[0] < frame - 1:
                # hold the previous value until just before the change
                self.main[prop] = last[1]
                self.main.keyframe_insert(path, frame=frame - 1, group="Piano Keys")
                self.main[prop] = value
            self.main.keyframe_insert(path, frame=frame, group="Piano Keys")
            self.recorded_props[prop] = (frame, value)

        if self.engine:
            for pb in self.engine.controlled_bones():
                pb.keyframe_insert("location", frame=frame, group=pb.name)
        self.last_record_frame = frame
        self._mute_takes()  # mute F-curves this frame created

    def _begin_recording(self, now):
        """Key every note property at the first frame, so keys that haven't
        been played yet stay up instead of inheriting their first pressed value."""
        self.record_start_time = now
        self.record_start_frame = self.scene.frame_current
        self.recorded_props = {}
        for prop in self.keymap.props.values():
            value = float(self.prop_values.get(prop, self.main.get(prop, 0.0)))
            path = f'["{prop}"]'
            if value > 0:
                # already held when recording started: up before the take
                self.main[prop] = 0.0
                self.main.keyframe_insert(path, frame=self.record_start_frame - 1, group="Piano Keys")
                self.main[prop] = value
            self.main.keyframe_insert(path, frame=self.record_start_frame, group="Piano Keys")
            self.recorded_props[prop] = (self.record_start_frame, value)
            self.prop_values.setdefault(prop, value)
        self.last_record_frame = self.record_start_frame - 1

    def _finish_recording(self, settings):
        start, end = self.record_start_frame, self.last_record_frame
        self.record_start_time = None
        self.last_record_message = None
        if self.engine and end > start:
            self._key_rest_after_take(end)
        if settings.sound_auto and end > start:
            try:
                self.last_record_message = piano_sound.build_sound(self.scene, self.main, settings)
            except Exception as e:  # never lose the take because of audio
                self.last_record_message = f"Couldn't build piano sound: {e}"
            print(f"[PianoMIDI] {self.last_record_message}")

    def _key_rest_after_take(self, end):
        """The hands' final keyframe is their rest pose. If this take is now the
        last thing keyed, close it with the rest pose so that stays true."""
        final = final_key_frame(self.arm)
        if final is not None and final > end:
            return  # the artist keyed something later: that stays the rest pose
        scene = self.scene
        frame = end + max(1, round(0.75 * scene.render.fps / scene.render.fps_base))
        for pb in self.engine.controlled_bones():
            live_loc = pb.location.copy()
            pb.location = self.engine.start_location(pb)
            pb.keyframe_insert("location", frame=frame, group=pb.name)
            pb.location = live_loc
        self._mute_takes()

    # -----------------------------
    def stop(self, settings):
        self.close_port()
        self.all_notes_off(settings)
        if self.record_start_time is not None:
            # key the released notes so the take doesn't end with keys held down
            self._record(time.perf_counter(), force=True)
            self._finish_recording(settings)
        self._unmute_takes()
        if self.engine and settings.restore_on_stop:
            self.engine.restore()
        self.main.update_tag()


def _bone_fcurves(arm):
    """{(bone name, axis): F-curve} for the controlled bones' keyed locations."""
    names = set(controlled_bone_names(arm))
    out = {}
    for fc in _fcurves(arm):
        if fc.data_path.startswith('pose.bones["') and fc.data_path.endswith('"].location'):
            name = fc.data_path[len('pose.bones["'):-len('"].location')]
            if name in names and len(fc.keyframe_points):
                out[(name, fc.array_index)] = fc
    return out


def final_key_frame(arm):
    """Frame of the last keyframe on any controlled bone, or None."""
    curves = _bone_fcurves(arm)
    return max((max(p.co[0] for p in fc.keyframe_points) for fc in curves.values()), default=None)


def rest_pose(arm):
    """The hands' rest pose, as the artist keyed it: every controlled bone's
    location at the final keyframe of the hands. Bones (or axes) without keys
    keep their current value, which is the same on every frame."""
    curves = _bone_fcurves(arm)
    final = final_key_frame(arm)
    rest = {}
    for name in controlled_bone_names(arm):
        loc = arm.pose.bones[name].location.copy()
        for axis in range(3):
            fc = curves.get((name, axis))
            if fc is not None:
                loc[axis] = fc.evaluate(final)
        rest[name] = loc
    return rest


def apply_rest_pose(arm):
    for name, loc in rest_pose(arm).items():
        arm.pose.bones[name].location = loc


def key_rest_pose(arm, scene):
    """Key the hands' current pose as their rest pose: on the hands' final
    keyframe (replacing it), or at the scene's end frame if that's later.
    Returns the frame."""
    final = final_key_frame(arm)
    frame = max(scene.frame_end, int(final)) if final is not None else scene.frame_end
    for name in controlled_bone_names(arm):
        arm.pose.bones[name].keyframe_insert("location", frame=frame, group=name)
    return frame


def _fcurves(id_data):
    ad = id_data.animation_data
    if not ad or not ad.action:
        return []
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
    except ImportError:
        return list(ad.action.fcurves)
    bag = action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return list(bag.fcurves) if bag else []


_session = None
_last_note_log = []  # kept after Stop so the session can still be exported


def note_log():
    """Notes of the running session, or of the last one after Stop."""
    return list(_session.note_log) if _session else list(_last_note_log)


def session():
    return _session


def is_running():
    return _session is not None


def _timer():
    global _session
    if _session is None:
        return None
    scene = _session.scene
    if scene is None or not hasattr(scene, "piano_midi"):
        stop()
        return None
    settings = scene.piano_midi
    try:
        _session.tick(settings)
    except ReferenceError:
        # objects were deleted from under us
        print("[PianoMIDI] Rig objects changed, stopping")
        _session = None
        return None
    _redraw()
    return settings.tick_interval


def _redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def start(context, with_midi=True):
    """Returns a warning string (or None). Raises RuntimeError on failure."""
    global _session
    if _session:
        return None
    settings = context.scene.piano_midi
    s = Session(context.scene, settings)
    warning = s.open_port(settings) if with_midi else None
    _session = s
    bpy.app.timers.register(_timer, first_interval=0.0)
    return warning


def stop():
    global _session
    global _last_note_log
    s, _session = _session, None
    if s is None:
        return
    scene = s.scene
    if scene is not None:
        s.stop(scene.piano_midi)
    _last_note_log = s.note_log  # includes the note-offs Stop just sent
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    stop()


def register():
    bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    stop()
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
