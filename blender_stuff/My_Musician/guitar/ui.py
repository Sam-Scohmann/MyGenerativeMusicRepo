import bpy
from bpy_extras.io_utils import ExportHelper

from . import rethink, session, take
from ..piano import midi_io


def _guitar(context):
    from .. import instrument_is
    return instrument_is(context, 'GUITAR')


class GUITAR_OT_live_start(bpy.types.Operator):
    """Start listening to MIDI and playing the guitar (hands, pick and strings)"""
    bl_idname = "guitar.live_start"
    bl_label = "Start Live"

    demo: bpy.props.BoolProperty(name="Test Piece", default=False)

    def execute(self, context):
        try:
            warning = session.start(context, demo=self.demo)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        s = session.current()
        if s.port_name:
            self.report({'INFO'}, f"Guitar listening on {s.port_name}")
        elif warning and not self.demo:
            self.report({'WARNING'}, warning)
        return {'FINISHED'}


class GUITAR_OT_live_stop(bpy.types.Operator):
    """Stop the live guitar and put the hands back"""
    bl_idname = "guitar.live_stop"
    bl_label = "Stop"

    def execute(self, context):
        msg = session.stop()
        if msg:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class GUITAR_OT_test_piece(bpy.types.Operator):
    """Play a built-in chord progression and scale through the guitar (no Sonic Pi needed)"""
    bl_idname = "guitar.test_piece"
    bl_label = "Play Test Piece"

    def execute(self, context):
        if not session.is_running():
            return bpy.ops.guitar.live_start(demo=True)
        session.current().start_demo()
        return {'FINISHED'}


class GUITAR_OT_all_off(bpy.types.Operator):
    """Release every string (panic)"""
    bl_idname = "guitar.all_off"
    bl_label = "All Notes Off"

    def execute(self, context):
        if session.is_running():
            session.current().all_off()
        return {'FINISHED'}


class GUITAR_OT_rethink(bpy.types.Operator):
    """Re-animate hands and strings from the recorded take: fingering planned over the whole
    take (positions, shortest shifts, chord voicings), pick strokes replayed with lookahead"""
    bl_idname = "guitar.rethink"
    bl_label = "Re-animate from Take"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return not session.is_running()

    def execute(self, context):
        wm = context.window_manager
        wm.progress_begin(0, 1)
        try:
            msg = rethink.rethink(context)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            wm.progress_end()
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class GUITAR_OT_build_sound(bpy.types.Operator):
    """Synthesise guitar audio for the recorded take and put it in the Video Sequencer"""
    bl_idname = "guitar.build_sound"
    bl_label = "Build Guitar Sound"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            msg = take.build_sound(context.scene, context.scene.guitar_live)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class GUITAR_OT_remove_sound(bpy.types.Operator):
    """Remove the guitar audio strip from the Video Sequencer"""
    bl_idname = "guitar.remove_sound"
    bl_label = "Remove Guitar Sound"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, f"Removed {take.remove_sound(context.scene)} strip(s)")
        return {'FINISHED'}


class GUITAR_OT_clear_take(bpy.types.Operator):
    """Forget the recorded guitar take (its notes and plucks; keyframes stay)"""
    bl_idname = "guitar.clear_take"
    bl_label = "Clear Take"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        take.clear(context.scene)
        return {'FINISHED'}


class GUITAR_OT_export_midi(bpy.types.Operator, ExportHelper):
    """Export the recorded guitar take as a .mid file"""
    bl_idname = "guitar.export_midi"
    bl_label = "Export Guitar MIDI"

    filename_ext = ".mid"
    filter_glob: bpy.props.StringProperty(default="*.mid;*.midi", options={'HIDDEN'})
    bpm: bpy.props.FloatProperty(name="Tempo (BPM)", default=120.0, min=20.0, max=400.0)
    per_string: bpy.props.BoolProperty(
        name="Channel per String", default=False,
        description="Write each string on its own MIDI channel (1-6) so the fingering survives a round trip")

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.clean_name(context.scene.name) + "_guitar.mid"
        return super().invoke(context, event)

    def execute(self, context):
        try:
            n = take.export_midi(context.scene, self.filepath, self.bpm, self.per_string)
        except (RuntimeError, OSError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Exported {n} notes to {self.filepath}")
        return {'FINISHED'}


# -----------------------------
# PANELS
# -----------------------------
class _GuitarPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Musician"

    @classmethod
    def poll(cls, context):
        return _guitar(context)


class GUITAR_PT_live(_GuitarPanel, bpy.types.Panel):
    bl_label = "MIDI Guitar Live"
    bl_order = 5

    def draw(self, context):
        layout = self.layout
        s = context.scene.guitar_live
        layout.prop(s, "armature")
        box = layout.box()
        box.label(text="MIDI Input")
        row = box.row(align=True)
        row.prop(s, "port", text="")
        row.operator("piano.refresh_ports", text="", icon='FILE_REFRESH')
        sess = session.current()
        if sess and sess.port_name:
            box.label(text=f"Listening on {sess.port_name}", icon='RADIOBUT_ON')
        elif sess and sess.port_error:
            box.label(text=sess.port_error, icon='ERROR')
        row = box.row(align=True)
        row.prop(s, "channel")
        row.prop(s, "channel_per_string", toggle=True)

        layout.prop(s, "mode", expand=True)
        row = layout.row(align=True)
        row.prop(s, "fold_octaves", toggle=True)
        row.prop(s, "octave_far_notes", toggle=True)
        if s.mode == 'PATTERN':
            col = layout.column(align=True)
            col.prop(s, "pattern")
            col.prop(s, "tempo")

        col = layout.column(align=True)
        col.scale_y = 1.4
        if sess:
            col.operator("guitar.live_stop", icon='PAUSE', depress=True)
            row = layout.row(align=True)
            row.operator("guitar.all_off", icon='CANCEL')
            row.operator("guitar.test_piece", text="Test Piece", icon='PLAY_SOUND')
            e = sess.engine
            layout.label(text=f"Strums with {e.strum.pb.name}, frets with {e.fret.pb.name} · "
                              f"position {round(e.p)}", icon='INFO')
            if sess.moved:
                layout.label(text=f"{sess.moved} note(s) played an octave away to fit the hand", icon='INFO')
            if sess.dropped:
                layout.label(text=f"{sess.dropped} note(s) didn't fit one hand shape (played the rest)",
                             icon='ERROR')
        else:
            col.operator("guitar.live_start", icon='PLAY')
            layout.operator("guitar.test_piece", icon='PLAY_SOUND')
        row = layout.row(align=True)
        row.prop(s, "record", icon='REC')
        row.prop(s, "live_sound", icon='SPEAKER')
        if sess and sess.message:
            layout.label(text=sess.message, icon='SOUND')


class GUITAR_PT_motion(_GuitarPanel, bpy.types.Panel):
    bl_label = "Hands, Pick & Strings"
    bl_parent_id = "GUITAR_PT_live"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.guitar_live
        col = layout.column(align=True)
        col.label(text="Picking Hand")
        col.prop(s, "strum_spread")
        col.prop(s, "pick_stroke")
        col.prop(s, "pick_approach")
        col.prop(s, "pick_dig")
        col.prop(s, "pick_lift")
        col.prop(s, "chord_window")
        col = layout.column(align=True)
        col.label(text="Fretting Hand")
        col.prop(s, "hand_half_life")
        col.prop(s, "press_half_life")
        col.prop(s, "release_half_life")
        col.prop(s, "finger_pad")
        col.prop(s, "press_depth")
        col.prop(s, "finger_arc")
        col.prop(s, "barre_overhang")
        col = layout.column(align=True)
        col.label(text="Fretting Arm")
        col.prop(s, "elbow_follow")
        col.prop(s, "hand_drop")
        col.prop(s, "hand_forward")
        col.prop(s, "low_string_lag")
        col = layout.column(align=True)
        col.label(text="Strings")
        col.prop(s, "string_press")
        col.prop(s, "string_vibration")
        col.prop(s, "string_decay")


class GUITAR_PT_take(_GuitarPanel, bpy.types.Panel):
    bl_label = "Guitar Recording & Sound"
    bl_parent_id = "GUITAR_PT_live"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.guitar_live
        data = take.load(context.scene)
        if data:
            strikes = sum(1 for e in data["events"] if e["type"] == "strike")
            layout.label(text=f"Take: {strikes} plucks from frame {data['start_frame']}", icon='REC')
        else:
            layout.label(text="No take recorded yet", icon='INFO')
        col = layout.column()
        col.scale_y = 1.3
        col.operator("guitar.rethink", icon='HAND')
        layout.operator("guitar.export_midi", text="Export MIDI File...", icon='EXPORT')
        layout.separator()
        layout.prop(s, "sound_auto")
        col = layout.column(align=True)
        col.prop(s, "sound_volume")
        col.prop(s, "sound_release")
        col.prop(s, "sound_stereo")
        row = layout.row(align=True)
        row.operator("guitar.build_sound", icon='SOUND')
        row.operator("guitar.remove_sound", text="", icon='TRASH')
        layout.operator("guitar.clear_take", icon='X')


classes = (
    GUITAR_OT_live_start,
    GUITAR_OT_live_stop,
    GUITAR_OT_test_piece,
    GUITAR_OT_all_off,
    GUITAR_OT_rethink,
    GUITAR_OT_build_sound,
    GUITAR_OT_remove_sound,
    GUITAR_OT_clear_take,
    GUITAR_OT_export_midi,
    GUITAR_PT_live,
    GUITAR_PT_motion,
    GUITAR_PT_take,
)
