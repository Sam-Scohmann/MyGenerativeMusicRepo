import bpy
import math

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

main_obj = bpy.context.active_object
keys = [o for o in bpy.context.selected_objects if o != main_obj]

if not main_obj or not keys:
    raise Exception("Select keys + active main object")


# -----------------------------
# CLEAN MAIN OBJECT PROPERTIES
# -----------------------------
# for k in list(main_obj.keys()):
#    if k != "_RNA_UI":
#        del main_obj[k]

# -----------------------------
# HELPER: extract note + octave
# expects names like C, C.001 etc OR C_1 etc
# -----------------------------
def parse_note(obj_name):
    base = obj_name.split(".")[0]  # remove .001
    return base


# -----------------------------
# SORT KEYS MUSICAL ORDER
# -----------------------------
def sort_key(obj):
    name = parse_note(obj.name)

    if name not in NOTE_NAMES:
        return (999, obj.location.x)

    note_index = NOTE_NAMES.index(name)
    return (obj.location.x // 1, note_index)


keys_sorted = sorted(keys, key=lambda o: o.location.x)

# Now regroup into octave chunks of 12
grouped = [keys_sorted[i:i + 12] for i in range(0, len(keys_sorted), 12)]

final_keys = []
for octave_idx, group in enumerate(grouped):
    # sort inside octave by NOTE_NAMES
    group_sorted = sorted(group, key=lambda o: NOTE_NAMES.index(parse_note(o.name)))

    for note_idx, obj in enumerate(group_sorted):
        final_keys.append((obj, octave_idx, note_idx))

# -----------------------------
# DRIVER SETUP
# -----------------------------
for global_index, (key, octave, note_idx) in enumerate(final_keys):

    # -----------------------------
    # DRIVER: rotation ← note_on * 5°
    # -----------------------------
    try:
        key.driver_remove("rotation_euler", 0)
    except:
        pass

    fcurve = key.driver_add("rotation_euler", 0)
    d = fcurve.driver
    d.type = 'SCRIPTED'

    var = d.variables.new()
    var.name = "n"
    var.type = 'SINGLE_PROP'

    target = var.targets[0]
    target.id = key
    target.data_path = '["note_on"]'

    d.expression = "n * 0.0872665"

    # -----------------------------
    # DRIVER: note_on ← main[prop]
    # -----------------------------
    note_name = NOTE_NAMES[note_idx]

    # ordered + unique property name
    prop_name = f"{note_name}_{octave}"  # {global_index:02d}_

    # -----------------------------
    # CREATE PROPERTY ON MAIN
    # -----------------------------
    main_obj[prop_name] = 0.0
    ui = main_obj.id_properties_ui(prop_name)
    ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)

    try:
        key.driver_remove('["note_on"]')
    except:
        pass

    fcurve = key.driver_add('["note_on"]')
    d = fcurve.driver
    d.type = 'SCRIPTED'

    var = d.variables.new()
    var.name = "v"
    var.type = 'SINGLE_PROP'

    target = var.targets[0]
    target.id = main_obj
    target.data_path = f'["{prop_name}"]'

    d.expression = "v"

print("Clean driver system rebuilt successfully.")