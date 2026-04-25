import bpy
import math

# -----------------------------
# CONFIG
# -----------------------------
WHITE_KEYS = {"C", "D", "E", "F", "G", "A", "B"}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# -----------------------------
# GET SELECTION
# -----------------------------
selected = bpy.context.selected_objects
active = bpy.context.active_object

if len(selected) != 12:
    raise Exception("Select exactly 12 objects.")

if active is None:
    raise Exception("No active object found.")

# -----------------------------
# STEP 1: ALIGN PIVOT (ORIGIN)
# Without changing object position
# -----------------------------
# Strategy:
# Move origin to active object's YZ plane by adjusting mesh data

for obj in selected:
    if obj.type != 'MESH':
        continue

    # Compute delta needed to shift origin in Y and Z only
    delta_y = obj.location.y - active.location.y
    delta_z = obj.location.z - active.location.z

    # Apply inverse translation to mesh data
    mesh = obj.data
    for v in mesh.vertices:
        v.co.y += delta_y
        v.co.z += delta_z

    # Move object location to compensate (so visible position stays same)
    obj.location.y = active.location.y
    obj.location.z = active.location.z

# -----------------------------
# STEP 2: LOCK TRANSFORMS
# -----------------------------
for obj in selected:
    # Lock all location axes
    obj.lock_location = (True, True, True)

    # Lock rotation except X
    obj.lock_rotation = (False, True, True)

# -----------------------------
# STEP 3: SORT BY X AND RENAME
# -----------------------------
# Sort objects by X location
sorted_objs = sorted(selected, key=lambda o: o.location.x)

for obj, note in zip(sorted_objs, NOTE_NAMES):
    obj.name = note

# -----------------------------
# STEP 4: ADD LIMIT ROTATION CONSTRAINT
# -----------------------------
for obj in sorted_objs:
    # Remove existing Limit Rotation (optional cleanup)
    for c in obj.constraints:
        if c.type == 'LIMIT_ROTATION':
            obj.constraints.remove(c)

    con = obj.constraints.new(type='LIMIT_ROTATION')
    con.use_limit_x = True
    con.use_limit_y = False
    con.use_limit_z = False

    con.owner_space = 'WORLD'

    # Determine if black or white key
    if "#" in obj.name:
        max_deg = 6.5
    else:
        max_deg = 4.5

    con.min_x = math.radians(0.0)
    con.max_x = math.radians(max_deg)

print("Done.")