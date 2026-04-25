import bpy
import mido

# -----------------------------
# GLOBAL STATE (persistent)
# -----------------------------
if "midi_state" not in globals():
    midi_state = {
        "port": None,
        "timer_running": False
    }

# -----------------------------
# CONFIG
# -----------------------------
MAIN_OBJECT_NAME = "Plane"
for name in mido.get_input_names():
    if 'svg_midi' in name:
        MIDI_PORT_NAME = name

# -----------------------------
# CLEANUP EXISTING PORT
# -----------------------------
# if midi_state["port"]:
#    try:
#        midi_state["port"].close()
#        print("Closed previous MIDI port")
#    except:
#        pass
#    midi_state["port"] = None

# -----------------------------
# OPEN PORT
# -----------------------------
try:
    midi_state["port"] = mido.open_input(MIDI_PORT_NAME)
    print(f"Connected to MIDI: {MIDI_PORT_NAME}")
except Exception as e:
    raise Exception(f"Failed to open MIDI port: {e}")

# -----------------------------
# NOTE MAPPING
# -----------------------------
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B"]


def midi_to_name(note_number):
    note = NOTE_NAMES[note_number % 12]
    octave = (note_number // 12) - 5 + 2
    print(f"{note}_{octave}")
    return f"{note}_{octave}"


# -----------------------------
# TIMER LOOP
# -----------------------------
def midi_poll():
    port = midi_state["port"]

    if not port:
        return None  # stop

    obj = bpy.data.objects.get(MAIN_OBJECT_NAME)
    if not obj:
        return 0.1

    for msg in port.iter_pending():
        print(msg)
        if msg.type in ("note_on", "note_off"):

            name = midi_to_name(msg.note)

            if name not in obj:
                print(name, "not in object")
                continue

            if msg.type == "note_on" and msg.velocity > 0:
                val = msg.velocity / 127.0
            else:
                val = 0.0
            print(name, val)
            obj[name] = val

            # Force animation update
            obj.update_tag()
            bpy.context.view_layer.update()

            # Force all objects to re-evaluate animation
            for ob in bpy.context.scene.objects:
                ob.update_tag()

            bpy.context.scene.frame_set(bpy.context.scene.frame_current)

            print("DEBUG>>>>>>>> ", obj[name])

    return 0.01


# -----------------------------
# PREVENT MULTIPLE TIMERS
# -----------------------------
if not midi_state["timer_running"]:
    bpy.app.timers.register(midi_poll)
    midi_state["timer_running"] = True
    print("MIDI listener started")
else:
    print("Timer already running — not adding another")


