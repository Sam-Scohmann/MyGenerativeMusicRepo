import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import midi_io


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
    from . import live  # live imports settings indirectly; avoid a cycle at load time
    if live.is_running():
        live.session().switch_port(self)


class PianoMidiSettings(bpy.types.PropertyGroup):
    # -----------------------------
    # RIG
    # -----------------------------
    main_object: PointerProperty(
        name="Main Object", type=bpy.types.Object,
        description="Object holding the per-note properties (C_0, C#_0, ...) that drive the keys")
    armature: PointerProperty(
        name="Hands Armature", type=bpy.types.Object, poll=_is_armature,
        description="Armature with hand_control_l/_r and the finger IK control bones")

    # -----------------------------
    # MIDI
    # -----------------------------
    port: EnumProperty(name="MIDI Input", items=_port_items, get=_get_port, set=_set_port,
                       description="MIDI input Blender listens on (the port Sonic Pi sends to). "
                                   "The list shows the inputs currently connected to this computer")
    port_name: StringProperty(name="MIDI Input Name", default="",
                              description="Name of the chosen MIDI input (saved with the file)")
    channel: IntProperty(name="Channel", default=0, min=0, max=16,
                         description="Only listen to this MIDI channel (0 = all channels)")
    octave_offset: IntProperty(
        name="Octave Offset", default=3, min=-2, max=10,
        description="Property octave = MIDI note // 12 - offset. 3 maps MIDI 36 (C2) to C_0")
    tick_interval: FloatProperty(name="Tick", default=1 / 60, min=0.005, max=0.1,
                                 subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                                 description="Seconds between updates")

    # -----------------------------
    # HANDS
    # -----------------------------
    use_hands: BoolProperty(name="Animate Hands", default=True)
    split_note: IntProperty(
        name="Split Note", default=60, min=0, max=127,
        description="Notes below this go to the bass hand, the rest to the treble hand (60 = middle C)")
    touch_depth_white: FloatProperty(
        name="White Touch", default=0.15, min=0.0, max=1.0, subtype='FACTOR',
        description="Where fingers land along a white key's guide curve (0 = front edge)")
    touch_depth_black: FloatProperty(
        name="Black Touch", default=0.15, min=0.0, max=1.0, subtype='FACTOR',
        description="Where fingers land along a black key's guide curve (0 = front edge)")
    finger_seek: FloatProperty(
        name="Seek Angle", default=35.0, min=1.0, max=60.0,
        description="Degrees a finger may turn sideways at its knuckle to seek a key (thumb and pinkie "
                    "swing forward over the keys this way). Fingers never stretch: beyond this the hand moves")
    max_reach_in: FloatProperty(
        name="Max Reach-in", default=1.5, min=0.0, max=6.0,
        description="Furthest the hand may slide towards the back of the piano (white-key widths) so a "
                    "finger can reach its key; fingers that would need more fall short instead")
    finger_min_gap: FloatProperty(
        name="Min Finger Gap", default=4.0, min=0.0, max=30.0,
        description="Smallest sideways angle (degrees) kept between neighbouring fingers' middle segments; "
                    "a finger can't turn further into its neighbour, idle neighbours make room")
    min_hand_distance: FloatProperty(
        name="Min Hand Distance", default=5.0, min=0.0, max=20.0,
        description="The two hands (hand_control centres) keep at least this many white keys apart. "
                    "Not a split: either hand can play anywhere, but an idle hand moves aside and busy "
                    "hands avoid fingerings that bring them closer")
    max_hand_lift: FloatProperty(
        name="Max Hand Lift", default=1.0, min=0.0, max=6.0,
        description="Highest hand_control rises along world Z (white-key widths), from all lift sources "
                    "together. It never rises so far that a pressing finger can't reach its key")
    move_lift: FloatProperty(
        name="Move Lift", default=0.1, min=0.0, max=1.0,
        description="World-Z arc of hand_control on every sideways move, per white key travelled "
                    "(pressed fingers stay on their keys)")
    reach_in_lift: FloatProperty(
        name="Lift per Reach-in", default=0.5, min=0.0, max=3.0,
        description="hand_control rises this much per unit it slides towards the back of the piano, "
                    "so fingers arch over the keys")
    play_lift: FloatProperty(
        name="Play Lift", default=0.2, min=0.0, max=3.0,
        description="World-Z lift of hand_control while playing (white-key widths at all fingers down), "
                    "following the damped finger activity")
    hand_noise: FloatProperty(
        name="Hand Noise", default=0.03, min=0.0, max=0.5,
        description="Slow random world-Z wobble of hand_control (white-key widths)")
    hand_jump_height: FloatProperty(
        name="Hand Jump Height", default=0.5, min=0.0, max=10.0, soft_max=5.0,
        description="hand_control arcs up by this much (white-key widths) mid-move when it travels "
                    "sideways further than Hand Jump Threshold (only while no key is held by that hand)")
    hand_jump_threshold: FloatProperty(
        name="Hand Jump Threshold", default=2.0, min=0.0, max=20.0,
        description="Sideways moves longer than this (white keys) make hand_control jump up")
    hand_half_life: FloatProperty(name="Hand Lag", default=0.04, min=0.0, max=1.0,
                                  subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                                  description="Time for the hand to cover half the distance to its target")
    press_half_life: FloatProperty(name="Press Lag", default=0.018, min=0.0, max=0.5,
                                   subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                                   description="Time for a finger to cover half the distance to a key")
    release_half_life: FloatProperty(name="Release Lag", default=0.05, min=0.0, max=0.5,
                                     subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                                     description="Time for a finger to cover half the distance back to rest")
    press_depth: FloatProperty(name="Press Depth", default=0.01, min=0.0, max=0.2,
                               description="How far into the key surface fingertips are pushed (in white-key widths)")
    arc_height: FloatProperty(name="Arc", default=0.35, min=0.0, max=2.0,
                              description="Lift (in white-key widths) of a finger travelling sideways to a key")
    black_forward: FloatProperty(name="Black Key Reach-in", default=0.8, min=0.0, max=4.0,
                                 description="How far (in white-key widths) the hand slides in when playing black keys")
    wrist_strength: FloatProperty(
        name="Wrist Follow", default=0.5, min=0.0, max=10.0, soft_max=5.0,
        description="How much the wrist rises and dips with the fingers (damped average of how far "
                    "they are up or down)")
    wrist_noise: FloatProperty(
        name="Wrist Noise", default=0.04, min=0.0, max=0.5,
        description="Slow random wobble of the wrist height, in white-key widths, for realism")
    max_wrist_lift: FloatProperty(
        name="Max Wrist Lift", default=1.0, min=0.0, max=5.0,
        description="Highest the wrist rises above its rest height, in white-key widths")
    jump_lift: FloatProperty(
        name="Jump Lift", default=0.25, min=0.0, max=2.0,
        description="When the hand travels further than Jump Threshold, the wrist arcs up during the move: "
                    "this much lift per white key of travel beyond the threshold")
    jump_threshold: FloatProperty(
        name="Jump Threshold", default=3.0, min=0.0, max=20.0,
        description="Hand moves longer than this (white keys) make the wrist lift")
    wrist_inertia: FloatProperty(
        name="Wrist Inertia", default=0.3, min=0.0, max=1.0, subtype='FACTOR',
        description="0 = the wrist follows exactly; higher = heavier, it lags and overshoots a little")
    wrist_inertia_max: FloatProperty(
        name="Max Inertia Offset", default=0.3, min=0.0, max=2.0,
        description="The wrist never lags or overshoots its target height by more than this (white-key widths)")
    finger_sway: FloatProperty(
        name="Idle Finger Sway", default=1.0, min=0.0, max=5.0, soft_max=3.0,
        description="How much idle fingers dangle sideways when the hand moves (ragdoll inertia), "
                    "always within the finger bones' IK Z limits")
    max_wrist_dip: FloatProperty(
        name="Max Wrist Dip", default=0.15, min=0.0, max=1.0,
        description="The wrist never goes more than this (white-key widths) below its rest height, "
                    "and never below hand_control")
    extra_reach_dip: FloatProperty(
        name="Extra Dip for Reach", default=0.3, min=0.0, max=1.0,
        description="When no other finger can take a note that's lower than a finger can reach, the "
                    "wrist may dip this much more (white-key widths) beyond Max Wrist Dip")
    fingertip_pad_auto: BoolProperty(
        name="Auto Fingertip Pad", default=True,
        description="Measure on the hand mesh how far each fingertip's skin reaches below its IK tip, "
                    "and lift pressing targets by that so pads rest on the keys")
    fingertip_pad: FloatProperty(
        name="Fingertip Pad", default=0.1, min=0.0, max=0.5,
        description="Manual fingertip pad thickness (white-key widths), used when Auto is off")
    home_delay: FloatProperty(name="Return Home After", default=2.0, min=0.0, max=30.0,
                              subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                              description="Idle seconds before a hand drifts back to its rest position (0 = never)")
    restore_on_stop: BoolProperty(name="Restore Pose on Stop", default=True)

    # -----------------------------
    # RECORDING
    # -----------------------------
    record: BoolProperty(name="Record Keyframes", default=False,
                         description="Bake the live performance (keys + hands) into keyframes from the current frame")

    # -----------------------------
    # SOUND
    # -----------------------------
    sound_auto: BoolProperty(name="Build Sound After Recording", default=True,
                             description="Rebuild the piano audio strip whenever a recording ends")
    sound_volume: FloatProperty(name="Volume", default=0.6, min=0.0, max=2.0, subtype='FACTOR')
    sound_release: FloatProperty(name="Release", default=0.12, min=0.005, max=2.0,
                                 subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                                 description="How quickly a note dies away after its key comes up")
    sound_stereo: FloatProperty(name="Stereo Width", default=0.6, min=0.0, max=1.0, subtype='FACTOR',
                                description="Pan low notes left and high notes right")

    # -----------------------------
    # BUILD TOOL OPTIONS
    # -----------------------------
    octaves: IntProperty(name="Octaves", default=4, min=1, max=10)
    octave_gap: FloatProperty(name="Gap", default=0.00313, min=0.0, unit='LENGTH',
                              description="Gap between duplicated octaves")
    press_angle: FloatProperty(name="Press Angle", default=0.0872665, min=0.0, max=0.5,
                               subtype='ANGLE', description="Key rotation at full velocity")
    white_limit: FloatProperty(name="White Limit", default=0.0785398, subtype='ANGLE', min=0.0)
    black_limit: FloatProperty(name="Black Limit", default=0.113446, subtype='ANGLE', min=0.0)


def get(context=None):
    return (context or bpy.context).scene.piano_midi


def register():
    bpy.utils.register_class(PianoMidiSettings)
    bpy.types.Scene.piano_midi = PointerProperty(type=PianoMidiSettings)


def unregister():
    del bpy.types.Scene.piano_midi
    bpy.utils.unregister_class(PianoMidiSettings)
