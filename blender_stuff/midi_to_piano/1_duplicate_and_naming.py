import bpy
from mathutils import Vector

# -----------------------------
# CONFIG
# -----------------------------
octaves = 4  # typically 3–5
gap = 0.00313  # meters between octaves

# -----------------------------
# GET ORIGINAL KEYS
# -----------------------------
source_keys = bpy.context.selected_objects

if len(source_keys) != 12:
    raise Exception("Select exactly 12 base key objects.")

# Sort for consistency
source_keys = sorted(source_keys, key=lambda o: o.location.x)

# -----------------------------
# COMPUTE OCTAVE DIMENSION (X span)
# -----------------------------
min_x = min(obj.bound_box[0][0] for obj in source_keys)
max_x = max(obj.bound_box[6][0] for obj in source_keys)


# Convert local bbox to world X positions
def world_x(obj, local_x):
    return obj.matrix_world @ (local_x, 0, 0)


min_world_x = min((obj.matrix_world @ Vector((v[0], 0, 0))).x for obj in source_keys for v in obj.bound_box)
max_world_x = max((obj.matrix_world @ Vector((v[0], 0, 0))).x for obj in source_keys for v in obj.bound_box)

octave_dimension_x = max_world_x - min_world_x

# -----------------------------
# CREATE / GET COLLECTION
# -----------------------------
collection_name = "Piano Keys"

if collection_name in bpy.data.collections:
    piano_col = bpy.data.collections[collection_name]
else:
    piano_col = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(piano_col)

# -----------------------------
# DUPLICATION
# -----------------------------
created = []

for octave in range(octaves):
    for base_obj in source_keys:
        new_obj = base_obj.copy()
        new_obj.data = base_obj.data.copy()

        # Offset using full octave width
        x_offset = octave * (octave_dimension_x + gap)

        new_obj.location = (
            base_obj.location.x + x_offset,
            base_obj.location.y,
            base_obj.location.z
        )

        # Link to collection
        piano_col.objects.link(new_obj)

        # -----------------------------
        # CUSTOM PROPERTY
        # -----------------------------
        new_obj["note_on"] = 0.0

        ui = new_obj.id_properties_ui("note_on")
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)

        created.append(new_obj)

print(f"Created {len(created)} keys using octave span = {octave_dimension_x:.4f} m")