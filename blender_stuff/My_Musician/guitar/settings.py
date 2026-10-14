import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)

from ..piano import midi_io

MODES = (
    ('AUTO', "Auto", "Chords (notes starting together) are strummed, single notes picked"),
    ('MELODY', "Melody", "Every note is picked on its own"),
    ('CHORD', "Chord", "Notes starting together are strummed as one chord"),
    ('PATTERN', "Pattern", "Held notes are fretted as a chord and strummed by the Strum Pattern at the Tempo"),
)


def _is_armature(self, obj):
    return obj.type == 'ARMATURE'


def _port_items(self, context):
    return midi_io.port_enum_items(self.port_name)


def _get_port(self):
    return midi_io.port_enum_index(self.port_name)


def _set_port(self, value):
    name = midi_io.port_from_index(value)
    if name == self.port_name:
        return
    self.port_name = name
    from . import session
    if session.is_running():
        session.current().switch_port(self)


class GuitarLiveSettings(bpy.types.PropertyGroup):
    armature: PointerProperty(name="Hands Armature", type=bpy.types.Object, poll=_is_armature,
                              description="Armature with hand_control_l/_r and finger IK controls. "
                                          "The hand nearer the bridge strums, the other frets")
    # MIDI
    port: EnumProperty(name="MIDI Input", items=_port_items, get=_get_port, set=_set_port,
                       description="MIDI input the guitar listens on")
    port_name: StringProperty(name="MIDI Input Name", default="")
    channel: IntProperty(name="Channel", default=0, min=0, max=16,
                         description="Only listen to this MIDI channel (0 = all)")
    channel_per_string: BoolProperty(
        name="Channel = String", default=False,
        description="MIDI channel 1..6 says which string plays the note (guitar-controller style); "
                    "otherwise the add-on chooses strings")
    # playing
    mode: EnumProperty(name="Mode", items=MODES, default='AUTO')
    pattern: StringProperty(
        name="Strum Pattern", default="D-DU-UDU",
        description="One character per slot across a 4-beat bar: D down, U up, X muted down, "
                    "- the hand swings without touching the strings")
    tempo: FloatProperty(name="Tempo", default=100.0, min=30.0, max=300.0, description="Pattern tempo, BPM")
    fold_octaves: BoolProperty(
        name="Fold Into Range", default=True,
        description="Notes above or below the guitar are moved by octaves until they fit, instead of "
                    "being dropped")
    octave_far_notes: BoolProperty(
        name="Octave Far Notes", default=True,
        description="When one hand cannot fret a whole chord, play the fewest notes at their nearest "
                    "octave instead of leaving them out (the top melody note is kept if possible)")
    chord_window: FloatProperty(name="Chord Window", default=0.03, min=0.0, max=0.2, unit='TIME_ABSOLUTE',
                                description="Notes starting within this time are one chord")
    strum_spread: FloatProperty(name="Strum Spread", default=0.012, min=0.001, max=0.08, unit='TIME_ABSOLUTE',
                                description="Time between strings in a strum")
    pick_stroke: FloatProperty(name="Pick Stroke", default=0.05, min=0.01, max=0.3, unit='TIME_ABSOLUTE',
                               description="Duration of a single-note pick stroke")
    pick_approach: FloatProperty(name="Pick Approach", default=0.035, min=0.005, max=0.3, unit='TIME_ABSOLUTE',
                                 description="Time for the pick to go down into the strings before a stroke")
    pick_dig: FloatProperty(name="Pick Depth", default=0.3, min=0.0, max=2.0,
                            description="How far the pick tip goes through the strings (string spacings)")
    pick_lift: FloatProperty(name="Pick Lift", default=1.2, min=0.0, max=6.0,
                             description="Height of the pick tip above the strings between strokes (string spacings)")
    # fretting hand
    hand_half_life: FloatProperty(name="Hand Lag", default=0.05, min=0.0, max=1.0, unit='TIME_ABSOLUTE')
    press_half_life: FloatProperty(name="Press Lag", default=0.02, min=0.0, max=0.5, unit='TIME_ABSOLUTE')
    release_half_life: FloatProperty(name="Release Lag", default=0.05, min=0.0, max=0.5, unit='TIME_ABSOLUTE')
    finger_pad: FloatProperty(name="Fingertip Pad", default=0.25, min=0.0, max=2.0,
                              description="Fingertip thickness above the string (string spacings)")
    press_depth: FloatProperty(name="Press Depth", default=0.05, min=0.0, max=1.0,
                               description="How far a pressing fingertip pushes into the string (string spacings)")
    finger_arc: FloatProperty(name="Finger Arc", default=0.6, min=0.0, max=4.0,
                              description="Lift of a finger travelling to another string or fret (string spacings)")
    barre_overhang: FloatProperty(
        name="Barre Overhang", default=1.0, min=0.0, max=4.0,
        description="For barre chords the index finger's IK target sits this far (string spacings) past "
                    "the edge string away from the palm, so the finger lies straight across the fret")
    # fretting arm
    elbow_follow: FloatProperty(
        name="Elbow Follow", default=1.0, min=0.0, max=2.0, subtype='FACTOR',
        description="How much the fretting arm's elbow_controller travels with the hand along the neck")
    hand_drop: FloatProperty(
        name="Hand Drop", default=3.0, min=0.0, max=20.0,
        description="How far the fretting hand_control lowers (string spacings, world down) when fretting "
                    "the physically lowest string; proportionally less for higher strings")
    hand_forward: FloatProperty(
        name="Hand Forward", default=1.5, min=-10.0, max=20.0,
        description="How far the fretting hand_control comes out from the guitar's front (string spacings) "
                    "on the lowest string, giving the fingers room; proportionally less for higher strings")
    low_string_lag: FloatProperty(name="Low String Lag", default=0.12, min=0.0, max=2.0, unit='TIME_ABSOLUTE',
                                  description="How smoothly the hand follows the strings being fretted")
    # strings
    string_press: FloatProperty(name="String Press", default=0.25, min=0.0, max=2.0,
                                description="How far a fretted string bends towards the fretboard (string spacings)")
    string_vibration: FloatProperty(name="Vibration", default=0.25, min=0.0, max=2.0,
                                    description="Amplitude of a plucked string at full velocity (string spacings)")
    string_decay: FloatProperty(name="Ring Time", default=1.5, min=0.05, max=10.0, unit='TIME_ABSOLUTE',
                                description="Time for a plucked string's vibration to fade to a third")
    # sound / recording
    live_sound: BoolProperty(name="Play Guitar Sound Live", default=False,
                             description="Play the synthesised guitar in Blender while live (e.g. for Pattern mode)")
    record: BoolProperty(name="Record", default=False,
                         description="Record the performance (notes, plucks, hands and strings) from the current frame")
    sound_auto: BoolProperty(name="Build Sound After Recording", default=True)
    sound_volume: FloatProperty(name="Volume", default=0.7, min=0.0, max=2.0, subtype='FACTOR')
    sound_release: FloatProperty(name="Release", default=0.08, min=0.005, max=2.0, unit='TIME_ABSOLUTE',
                                 description="How quickly a note dies when the string is released")
    sound_stereo: FloatProperty(name="Stereo Width", default=0.5, min=0.0, max=1.0, subtype='FACTOR')


def register():
    bpy.utils.register_class(GuitarLiveSettings)
    bpy.types.Scene.guitar_live = PointerProperty(type=GuitarLiveSettings)


def unregister():
    del bpy.types.Scene.guitar_live
    bpy.utils.unregister_class(GuitarLiveSettings)
