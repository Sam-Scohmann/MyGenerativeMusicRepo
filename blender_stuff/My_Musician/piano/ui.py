import bpy

from types import SimpleNamespace

from . import hand_presets, live, midi_export, midi_io, piano_sound, rethink
from .hands import find_sides
from .keymap import describe
from .setup_ops import classes as setup_classes


def note_label(midi):
    """60 -> 'C4 (60)' in Sonic Pi / scientific pitch naming."""
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1} ({midi})"


# -----------------------------
# LIVE OPERATORS
# -----------------------------
class PIANO_OT_start(bpy.types.Operator):
    """Start listening to MIDI and animating keys + hands"""
    bl_idname = "piano.start"
    bl_label = "Start Live"

    demo: bpy.props.BoolProperty(name="Demo", default=False,
                                 description="Also play the built-in test piece")

    def execute(self, context):
        try:
            warning = live.start(context, with_midi=True)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if self.demo:
            live.session().start_demo()
        s = live.session()
        gaps = s.key_gaps()
        if gaps:
            self.report({'WARNING'}, "No key responds to " + ", ".join(note_label(n) for n in gaps)
                        + ": run Build Piano ▸ Check / Repair Drivers")
        elif warning and not self.demo:
            self.report({'WARNING'}, warning)
        elif s.port_name:
            self.report({'INFO'}, f"Listening on {s.port_name}")
        return {'FINISHED'}


class PIANO_OT_play_demo(bpy.types.Operator):
    """Play the built-in test piece through the rig (no Sonic Pi needed)"""
    bl_idname = "piano.play_demo"
    bl_label = "Play Test Piece"

    def execute(self, context):
        if not live.is_running():
            return bpy.ops.piano.start(demo=True)
        live.session().start_demo()
        return {'FINISHED'}


class PIANO_OT_stop(bpy.types.Operator):
    """Stop listening and release all keys"""
    bl_idname = "piano.stop"
    bl_label = "Stop"

    def execute(self, context):
        live.stop()
        return {'FINISHED'}


class PIANO_OT_all_off(bpy.types.Operator):
    """Release every key (panic)"""
    bl_idname = "piano.all_notes_off"
    bl_label = "All Notes Off"

    def execute(self, context):
        if live.is_running():
            live.session().all_notes_off(context.scene.piano_midi)
        return {'FINISHED'}


class PIANO_OT_refresh_ports(bpy.types.Operator):
    """Re-scan MIDI inputs"""
    bl_idname = "piano.refresh_ports"
    bl_label = "Refresh MIDI Ports"

    def execute(self, context):
        names = midi_io.scan_ports(force=True)
        self.report({'INFO'}, f"{len(names)} MIDI input(s): {', '.join(names) or 'none'}")
        return {'FINISHED'}


class PIANO_OT_install_deps(bpy.types.Operator):
    """pip-install mido and python-rtmidi into Blender's user modules folder"""
    bl_idname = "piano.install_deps"
    bl_label = "Install MIDI Support"

    def execute(self, context):
        ok, tail = midi_io.install_dependencies()
        if ok and midi_io.get_mido():
            midi_io.refresh_ports()
            self.report({'INFO'}, "mido installed")
            return {'FINISHED'}
        self.report({'ERROR'}, "Install failed (see system console): " + " ".join(tail))
        return {'CANCELLED'}


class PIANO_OT_build_sound(bpy.types.Operator):
    """Synthesise piano audio for the recorded take and put it in the Video Sequencer"""
    bl_idname = "piano.build_sound"
    bl_label = "Build Piano Sound"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.piano_midi.main_object is not None

    def execute(self, context):
        s = context.scene.piano_midi
        try:
            msg = piano_sound.build_sound(context.scene, s.main_object, s)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class PIANO_OT_remove_sound(bpy.types.Operator):
    """Remove the piano audio strip from the Video Sequencer"""
    bl_idname = "piano.remove_sound"
    bl_label = "Remove Piano Sound"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, f"Removed {piano_sound.remove_sound(context.scene)} strip(s)")
        return {'FINISHED'}


class PIANO_OT_fix_recording(bpy.types.Operator):
    """Fix takes recorded with the earlier version, where keys stayed down
    from the start until they were first played"""
    bl_idname = "piano.fix_recording"
    bl_label = "Fix Old Recording"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.piano_midi.main_object is not None

    def execute(self, context):
        s = context.scene.piano_midi
        fixed = piano_sound.fix_recording(s.main_object, s.octave_offset)
        s.main_object.update_tag()
        self.report({'INFO'}, f"Fixed {fixed} key(s)" if fixed else "Nothing to fix")
        return {'FINISHED'}


class PIANO_OT_rethink_hands(bpy.types.Operator):
    """Re-animate the hands from the whole recorded take: plan the hand split and
    fingering with lookahead, move hands ahead of the notes, and replace the
    take's hand keyframes (keys and sound are unchanged)"""
    bl_idname = "piano.rethink_hands"
    bl_label = "Re-animate Hands from Take"
    bl_options = {'REGISTER', 'UNDO'}

    approach: bpy.props.FloatProperty(name="Approach", default=0.25, min=0.02, max=1.0, unit='TIME_ABSOLUTE',
                                      description="How early a finger starts moving to its next key")
    descend: bpy.props.FloatProperty(name="Descend", default=0.05, min=0.01, max=0.3, unit='TIME_ABSOLUTE',
                                     description="Time from hovering over a key to striking it")
    lift: bpy.props.FloatProperty(name="Lift", default=0.07, min=0.01, max=0.5, unit='TIME_ABSOLUTE',
                                  description="Time for a finger to come off a released key")
    hover: bpy.props.FloatProperty(name="Hover Height", default=0.4, min=0.0, max=2.0,
                                   description="Height (in white-key widths) a finger hovers over its next key")
    hand_lead: bpy.props.FloatProperty(name="Hand Lead", default=0.06, min=0.0, max=0.5, unit='TIME_ABSOLUTE',
                                       description="How long before a note the hand is already in place")
    home_after: bpy.props.FloatProperty(name="Return Home After", default=1.0, min=0.0, max=10.0,
                                        unit='TIME_ABSOLUTE',
                                        description="Seconds after the last note before the hands go back to rest")

    @classmethod
    def poll(cls, context):
        return context.scene.piano_midi.main_object is not None and not live.is_running()

    def execute(self, context):
        wm = context.window_manager
        opts = SimpleNamespace(approach=self.approach, descend=self.descend, lift=self.lift,
                               hover=self.hover, hand_lead=self.hand_lead, home_after=self.home_after,
                               press_depth=context.scene.piano_midi.press_depth)
        wm.progress_begin(0, 100)
        try:
            msg = rethink.rethink(context, opts, progress=lambda x: wm.progress_update(int(x * 100)))
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            wm.progress_end()
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class PIANO_OT_set_rest_pose(bpy.types.Operator):
    """Key the hands' current pose as their rest pose (the hands' final keyframe,
    at the scene's end frame or later). Re-animate Hands starts from and returns to it"""
    bl_idname = "piano.set_rest_pose"
    bl_label = "Set Rest Pose"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.piano_midi.armature is not None and not live.is_running()

    def execute(self, context):
        frame = live.key_rest_pose(context.scene.piano_midi.armature, context.scene)
        self.report({'INFO'}, f"Hand rest pose keyed at frame {frame}")
        return {'FINISHED'}


class PIANO_OT_apply_rest_pose(bpy.types.Operator):
    """Put the hands in their rest pose: where they are at the hands' final keyframe
    (or, with no keys, where they are now)"""
    bl_idname = "piano.apply_rest_pose"
    bl_label = "Go to Rest Pose"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.piano_midi.armature is not None and not live.is_running()

    def execute(self, context):
        live.apply_rest_pose(context.scene.piano_midi.armature)
        return {'FINISHED'}


# -----------------------------
# PANELS
# -----------------------------
class _PianoPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Musician"

    @classmethod
    def poll(cls, context):
        from .. import instrument_is
        return instrument_is(context, 'PIANO')


class PIANO_PT_live(_PianoPanel, bpy.types.Panel):
    bl_label = "MIDI Piano Live"

    def draw(self, context):
        layout = self.layout
        s = context.scene.piano_midi

        col = layout.column(align=True)
        col.prop(s, "main_object")
        col.prop(s, "armature")
        if s.armature and not find_sides(s.armature):
            col.label(text="No hand_control_l / _r bones", icon='ERROR')

        box = layout.box()
        if midi_io.backend_name() is None:
            box.label(text="mido not installed", icon='ERROR')
            box.operator("piano.install_deps", icon='IMPORT')
        else:
            box.label(text="MIDI Input")
            row = box.row(align=True)
            row.prop(s, "port", text="")
            row.operator("piano.refresh_ports", text="", icon='FILE_REFRESH')
            sess = live.session()
            if sess and sess.port_name:
                box.label(text=f"Listening on {sess.port_name}", icon='RADIOBUT_ON')
            elif sess and sess.port_error:
                box.label(text=sess.port_error, icon='ERROR')
            elif not s.port_name:
                box.label(text="Pick the input Sonic Pi sends to", icon='INFO')
            elif not midi_io.is_connected(s.port_name):
                box.label(text="Selected input isn't connected", icon='ERROR')
            else:
                box.label(text=f"{midi_io.backend_name()}: ready", icon='CHECKMARK')
        row = box.row(align=True)
        row.prop(s, "channel")
        row.prop(s, "octave_offset")
        lo = describe(36, s.octave_offset)
        box.label(text=f"MIDI 36 (C2) → {lo},  60 (C4) → {describe(60, s.octave_offset)}")

        col = layout.column(align=True)
        col.scale_y = 1.4
        if live.is_running():
            sess = live.session()
            col.operator("piano.stop", icon='PAUSE', depress=True)
            row = layout.row(align=True)
            row.operator("piano.all_notes_off", icon='CANCEL')
            row.operator("piano.play_demo", text="Test Piece", icon='PLAY_SOUND')
            info = sess.port_name or "no MIDI port (demo only)"
            layout.label(text=f"{len(sess.keymap.keys)} keys, "
                              f"{len(sess.engine.hands) if sess.engine else 0} hands, {info}", icon='INFO')
            gaps = sess.key_gaps()
            if gaps:
                layout.label(text="No key for: " + ", ".join(note_label(n) for n in gaps), icon='ERROR')
            if sess.ignored:
                total = sum(sess.ignored.values())
                notes = ", ".join(note_label(n) for n in sorted(sess.ignored)[:6])
                layout.label(text=f"{total} note(s) ignored, no key: {notes}", icon='ERROR')
        else:
            col.operator("piano.start", icon='PLAY')
            layout.operator("piano.play_demo", text="Play Test Piece", icon='PLAY_SOUND')

        row = layout.row()
        row.prop(s, "record", icon='REC')
        if s.record:
            layout.label(text="Keys from the current frame on", icon='KEYINGSET')
        sess = live.session()
        if sess and sess.last_record_message:
            layout.label(text=sess.last_record_message, icon='SOUND')


class PIANO_PT_hands(_PianoPanel, bpy.types.Panel):
    bl_label = "Hands"
    bl_parent_id = "PIANO_PT_live"

    def draw_header(self, context):
        self.layout.prop(context.scene.piano_midi, "use_hands", text="")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.piano_midi

        row = layout.row(align=True)
        row.operator("piano.import_hand_settings", text="Import", icon='IMPORT')
        row.operator("piano.export_hand_settings", text="Export", icon='EXPORT')
        row.operator("piano.reset_hand_settings", text="", icon='LOOP_BACK')
        row = layout.row(align=True)
        row.operator("piano.set_rest_pose", icon='KEYFRAME_HLT')
        row.operator("piano.apply_rest_pose", icon='LOOP_BACK')
        layout.label(text="Start keeps the pose the hands are in", icon='INFO')

        layout.active = s.use_hands
        layout.prop(s, "split_note", text=f"Split ({describe(s.split_note, s.octave_offset)})")
        layout.prop(s, "min_hand_distance")
        col = layout.column(align=True)
        col.prop(s, "finger_seek")
        col.prop(s, "finger_min_gap")
        col.prop(s, "max_reach_in")
        col = layout.column(align=True)
        col.label(text="Hand Lift (world Z)")
        col.prop(s, "max_hand_lift")
        col.prop(s, "move_lift")
        col.prop(s, "hand_jump_height")
        col.prop(s, "hand_jump_threshold")
        col.prop(s, "reach_in_lift")
        col.prop(s, "play_lift")
        col.prop(s, "hand_noise")
        col = layout.column(align=True)
        col.prop(s, "hand_half_life")
        col.prop(s, "press_half_life")
        col.prop(s, "release_half_life")
        col = layout.column(align=True)
        col.prop(s, "arc_height")
        col.prop(s, "press_depth")
        col.prop(s, "black_forward")
        col = layout.column(align=True)
        col.prop(s, "wrist_strength")
        col.prop(s, "wrist_noise")
        col.prop(s, "max_wrist_lift")
        col.prop(s, "max_wrist_dip")
        col.prop(s, "extra_reach_dip")
        col = layout.column(align=True)
        col.prop(s, "jump_lift")
        col.prop(s, "jump_threshold")
        col = layout.column(align=True)
        col.prop(s, "wrist_inertia")
        col.prop(s, "wrist_inertia_max")
        layout.prop(s, "finger_sway")
        col = layout.column(align=True)
        col.prop(s, "fingertip_pad_auto")
        sub = col.row()
        sub.active = not s.fingertip_pad_auto
        sub.prop(s, "fingertip_pad")
        layout.prop(s, "home_delay")
        col = layout.column(align=True)
        col.prop(s, "touch_depth_white")
        col.prop(s, "touch_depth_black")
        layout.prop(s, "restore_on_stop")
        if live.is_running():
            layout.label(text="Touch points & rest pose are read on Start", icon='INFO')


class PIANO_PT_sound(_PianoPanel, bpy.types.Panel):
    bl_label = "Recording & Sound"
    bl_parent_id = "PIANO_PT_live"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.piano_midi
        layout.operator("piano.fix_recording", icon='KEYFRAME')
        col = layout.column()
        col.scale_y = 1.3
        col.operator("piano.rethink_hands", icon='HAND')
        layout.operator("piano.export_midi", text="Export MIDI File...", icon='EXPORT')

        layout.separator()
        layout.prop(s, "sound_auto")
        col = layout.column(align=True)
        col.prop(s, "sound_volume")
        col.prop(s, "sound_release")
        col.prop(s, "sound_stereo")
        row = layout.row(align=True)
        row.operator("piano.build_sound", icon='SOUND')
        row.operator("piano.remove_sound", text="", icon='TRASH')
        layout.label(text="Plays from the Video Sequencer", icon='INFO')


class PIANO_PT_build(_PianoPanel, bpy.types.Panel):
    bl_label = "Build Piano"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.piano_midi

        layout.label(text="1. Select 12 keys (active = reference)")
        row = layout.row(align=True)
        row.prop(s, "white_limit")
        row.prop(s, "black_limit")
        layout.operator("piano.prepare_octave", icon='PIVOT_ACTIVE')

        layout.label(text="2. Select the 12 prepared keys")
        row = layout.row(align=True)
        row.prop(s, "octaves")
        row.prop(s, "octave_gap")
        layout.operator("piano.duplicate_octaves", icon='DUPLICATE')

        layout.label(text="3. Select keys, then main object")
        layout.prop(s, "press_angle")
        layout.operator("piano.build_key_drivers", icon='DRIVER')
        layout.operator("piano.repair_drivers", icon='CHECKMARK')

        layout.label(text="4. Select keys with Mix-node materials")
        layout.operator("piano.glow_drivers", icon='LIGHT')

        layout.label(text="5. Active: curve drawn over the keys")
        layout.operator("piano.build_guides", icon='CURVE_PATH')


classes = setup_classes + hand_presets.classes + midi_export.classes + (
    PIANO_OT_start,
    PIANO_OT_play_demo,
    PIANO_OT_stop,
    PIANO_OT_all_off,
    PIANO_OT_refresh_ports,
    PIANO_OT_install_deps,
    PIANO_OT_build_sound,
    PIANO_OT_remove_sound,
    PIANO_OT_fix_recording,
    PIANO_OT_rethink_hands,
    PIANO_OT_set_rest_pose,
    PIANO_OT_apply_rest_pose,
    PIANO_PT_live,
    PIANO_PT_hands,
    PIANO_PT_sound,
    PIANO_PT_build,
)
