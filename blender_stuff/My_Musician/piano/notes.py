NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def is_black(note_index):
    return "#" in NOTE_NAMES[note_index % 12]


def base_name(obj_name):
    """'C#.003' -> 'C#'"""
    return obj_name.split(".")[0]


def midi_to_prop(note_number, octave_offset):
    """MIDI note -> main-object property name, e.g. 60 -> 'C_2' with offset 3."""
    return f"{NOTE_NAMES[note_number % 12]}_{note_number // 12 - octave_offset}"


def prop_to_midi(prop_name, octave_offset):
    """'C#_2' -> 61 with offset 3. Returns None if the name doesn't parse."""
    try:
        name, octave = prop_name.rsplit("_", 1)
        return (int(octave) + octave_offset) * 12 + NOTE_NAMES.index(name)
    except ValueError:
        return None
