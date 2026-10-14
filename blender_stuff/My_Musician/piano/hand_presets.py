"""Export / import / reset of the Hands panel settings as JSON."""
import json

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

FORMAT = "midi_piano_hands/hand_settings"
VERSION = 1

# Everything shown in the Hands panel
HAND_SETTINGS = (
    "use_hands",
    "split_note",
    "finger_seek",
    "finger_min_gap",
    "max_reach_in",
    "min_hand_distance",
    "max_hand_lift",
    "move_lift",
    "reach_in_lift",
    "play_lift",
    "hand_noise",
    "hand_jump_height",
    "hand_jump_threshold",
    "hand_half_life",
    "press_half_life",
    "release_half_life",
    "arc_height",
    "press_depth",
    "black_forward",
    "wrist_strength",
    "wrist_noise",
    "max_wrist_dip",
    "max_wrist_lift",
    "jump_lift",
    "jump_threshold",
    "wrist_inertia",
    "wrist_inertia_max",
    "finger_sway",
    "extra_reach_dip",
    "fingertip_pad_auto",
    "fingertip_pad",
    "home_delay",
    "touch_depth_white",
    "touch_depth_black",
    "restore_on_stop",
)


def to_dict(settings):
    return {
        "format": FORMAT,
        "version": VERSION,
        "settings": {name: getattr(settings, name) for name in HAND_SETTINGS},
    }


def apply_dict(settings, data):
    """Returns (applied names, skipped names). Raises ValueError for a file that isn't a hand preset."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Not a MIDI Piano Hands settings file")
    values = data.get("settings")
    if not isinstance(values, dict):
        raise ValueError("Settings file has no 'settings' section")

    applied, skipped = [], []
    for name, value in values.items():
        if name not in HAND_SETTINGS:
            skipped.append(name)
            continue
        current = getattr(settings, name)
        try:
            # coerce to the property's type; Blender clamps to its min/max
            setattr(settings, name, type(current)(value))
            applied.append(name)
        except (TypeError, ValueError):
            skipped.append(name)
    return applied, skipped


class PIANO_OT_export_hand_settings(bpy.types.Operator, ExportHelper):
    """Save the Hands settings to a JSON file"""
    bl_idname = "piano.export_hand_settings"
    bl_label = "Export Hand Settings"

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = "hand_settings.json"
        return super().invoke(context, event)

    def execute(self, context):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(to_dict(context.scene.piano_midi), f, indent=2)
        except OSError as e:
            self.report({'ERROR'}, f"Couldn't write {self.filepath}: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Hand settings saved to {self.filepath}")
        return {'FINISHED'}


class PIANO_OT_import_hand_settings(bpy.types.Operator, ImportHelper):
    """Load Hands settings from a JSON file"""
    bl_idname = "piano.import_hand_settings"
    bl_label = "Import Hand Settings"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, encoding="utf-8") as f:
                data = json.load(f)
            applied, skipped = apply_dict(context.scene.piano_midi, data)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self.report({'ERROR'}, f"Couldn't load {self.filepath}: {e}")
            return {'CANCELLED'}
        if skipped:
            self.report({'WARNING'}, f"Loaded {len(applied)} settings, ignored: {', '.join(skipped)}")
        else:
            self.report({'INFO'}, f"Loaded {len(applied)} hand settings")
        return {'FINISHED'}


class PIANO_OT_reset_hand_settings(bpy.types.Operator):
    """Reset the Hands settings to their defaults"""
    bl_idname = "piano.reset_hand_settings"
    bl_label = "Reset Hand Settings"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = context.scene.piano_midi
        for name in HAND_SETTINGS:
            s.property_unset(name)
        self.report({'INFO'}, "Hand settings reset to defaults")
        return {'FINISHED'}


classes = (
    PIANO_OT_export_hand_settings,
    PIANO_OT_import_hand_settings,
    PIANO_OT_reset_hand_settings,
)
