"""The piano-building scripts (0-4) as operators."""
import bpy
from mathutils import Vector

from .keymap import key_drivers
from .notes import NOTE_NAMES, base_name


def _add_driver(owner, data_path, expression, var_name, target_id, target_path, index=-1, driver_type='SCRIPTED'):
    try:
        owner.driver_remove(data_path, index)
    except (TypeError, RuntimeError):
        pass
    fc = owner.driver_add(data_path, index) if index >= 0 else owner.driver_add(data_path)
    d = fc.driver
    d.type = driver_type
    var = d.variables.new()
    var.name = var_name
    var.type = 'SINGLE_PROP'
    var.targets[0].id = target_id
    var.targets[0].data_path = target_path
    if driver_type == 'SCRIPTED':
        d.expression = expression
    # it may have been evaluated half-built; clear the sticky "invalid" flag
    d.is_valid = True
    return fc


# -----------------------------
# 0: PIVOT, LOCKS, NAMES, LIMITS
# -----------------------------
class PIANO_OT_prepare_octave(bpy.types.Operator):
    """Select the 12 keys of one octave (active = reference key): aligns their pivots
    to the active key's YZ, locks transforms, names them C..B by X and adds rotation limits"""
    bl_idname = "piano.prepare_octave"
    bl_label = "Prepare Base Octave"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        s = context.scene.piano_midi
        selected = context.selected_objects
        active = context.active_object
        if len(selected) != 12:
            self.report({'ERROR'}, f"Select exactly 12 key objects ({len(selected)} selected)")
            return {'CANCELLED'}

        for obj in selected:
            if obj.type != 'MESH':
                continue
            # move the origin onto the active key's YZ plane without moving the mesh
            delta_y = obj.location.y - active.location.y
            delta_z = obj.location.z - active.location.z
            for v in obj.data.vertices:
                v.co.y += delta_y
                v.co.z += delta_z
            obj.location.y = active.location.y
            obj.location.z = active.location.z

        for obj in selected:
            obj.lock_location = (True, True, True)
            obj.lock_rotation = (False, True, True)

        sorted_objs = sorted(selected, key=lambda o: o.location.x)
        for obj, note in zip(sorted_objs, NOTE_NAMES):
            obj.name = note

        for obj in sorted_objs:
            for c in [c for c in obj.constraints if c.type == 'LIMIT_ROTATION']:
                obj.constraints.remove(c)
            con = obj.constraints.new(type='LIMIT_ROTATION')
            con.use_limit_x = True
            con.owner_space = 'WORLD'
            con.min_x = 0.0
            con.max_x = s.black_limit if "#" in obj.name else s.white_limit

        self.report({'INFO'}, "Octave prepared")
        return {'FINISHED'}


# -----------------------------
# 1: DUPLICATE OCTAVES
# -----------------------------
class PIANO_OT_duplicate_octaves(bpy.types.Operator):
    """Select the 12 prepared keys: duplicates them into N octaves in the 'Piano Keys' collection"""
    bl_idname = "piano.duplicate_octaves"
    bl_label = "Duplicate Octaves"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: bpy.props.StringProperty(name="Collection", default="Piano Keys")

    def execute(self, context):
        s = context.scene.piano_midi
        source_keys = context.selected_objects
        if len(source_keys) != 12:
            self.report({'ERROR'}, f"Select exactly 12 base key objects ({len(source_keys)} selected)")
            return {'CANCELLED'}
        source_keys = sorted(source_keys, key=lambda o: o.location.x)

        xs = [(obj.matrix_world @ Vector(v)).x for obj in source_keys for v in obj.bound_box]
        octave_width = max(xs) - min(xs)

        col = bpy.data.collections.get(self.collection_name)
        if col is None:
            col = bpy.data.collections.new(self.collection_name)
            context.scene.collection.children.link(col)

        created = 0
        for octave in range(s.octaves):
            for base_obj in source_keys:
                new_obj = base_obj.copy()
                new_obj.data = base_obj.data.copy()
                new_obj.location = (base_obj.location.x + octave * (octave_width + s.octave_gap),
                                    base_obj.location.y, base_obj.location.z)
                col.objects.link(new_obj)
                new_obj["note_on"] = 0.0
                new_obj.id_properties_ui("note_on").update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
                created += 1

        self.report({'INFO'}, f"Created {created} keys, octave width {octave_width:.4f}")
        return {'FINISHED'}


# -----------------------------
# 2: KEY DRIVERS ON MAIN OBJECT
# -----------------------------
def key_props(keys):
    """[(key, expected property name)] for the key objects among `keys`.

    Octaves are counted along the keyboard (world X): a new octave starts
    whenever the note name wraps around (... A, A#, B, C ...). Objects that
    aren't keys are skipped without shifting anything, and a missing key
    doesn't throw the following octaves off either.
    """
    real = [k for k in keys if base_name(k.name) in NOTE_NAMES]
    real.sort(key=lambda o: o.matrix_world.translation.x)
    out = []
    octave, prev = -1, len(NOTE_NAMES)
    for key in real:
        index = NOTE_NAMES.index(base_name(key.name))
        if index <= prev:
            octave += 1
        prev = index
        out.append((key, f"{NOTE_NAMES[index]}_{octave}"))
    return out


def _note_on_driver(key):
    ad = key.animation_data
    return next((fc for fc in (ad.drivers if ad else []) if fc.data_path == '["note_on"]'), None)


def build_key_drivers(context, main_obj, keys, press_angle):
    plan = key_props(keys)  # (key, prop name)

    # Create every property first and let the depsgraph see them: drivers read
    # the evaluated copy, and one that fails to resolve gets flagged invalid.
    for key, prop_name in plan:
        if "note_on" not in key:
            key["note_on"] = 0.0
            key.id_properties_ui("note_on").update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
            key.update_tag()
        if prop_name not in main_obj:
            main_obj[prop_name] = 0.0
            main_obj.id_properties_ui(prop_name).update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
    main_obj.update_tag()
    context.view_layer.update()

    for key, prop_name in plan:
        # rotation <- note_on * press angle
        _add_driver(key, "rotation_euler", f"n * {press_angle:.7f}", "n", key, '["note_on"]', index=0)
        # note_on <- main[prop]
        _add_driver(key, '["note_on"]', "v", "v", main_obj, f'["{prop_name}"]')
    return len(plan)


class PIANO_OT_build_key_drivers(bpy.types.Operator):
    """Select all keys, then the main object last (active): creates C_0, C#_0, ... on the
    main object and drives every key's press rotation from them"""
    bl_idname = "piano.build_key_drivers"
    bl_label = "Build Key Drivers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        main_obj = context.active_object
        keys = [o for o in context.selected_objects if o != main_obj]
        if not main_obj or not keys:
            self.report({'ERROR'}, "Select the keys, then the main object as active")
            return {'CANCELLED'}
        built = build_key_drivers(context, main_obj, keys, context.scene.piano_midi.press_angle)
        context.scene.piano_midi.main_object = main_obj
        self.report({'INFO'}, f"Drivers built for {built} keys")
        return {'FINISHED'}


class PIANO_OT_repair_drivers(bpy.types.Operator):
    """Checks every key: it must read the main object's property for its position on the
    keyboard (e.g. the 3rd B from the left reads B_2), and its drivers must be valid.
    Fixes what's wrong"""
    bl_idname = "piano.repair_drivers"
    bl_label = "Check / Repair Drivers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.piano_midi.main_object is not None

    def execute(self, context):
        s = context.scene.piano_midi
        main = s.main_object
        fixed = []

        # every key with a note_on driver, whatever it currently points at
        keys = [o for o in context.scene.objects if _note_on_driver(o)]
        expected = dict(key_props(keys))
        for key in keys:
            prop = expected.get(key)
            var = _note_on_driver(key).driver.variables
            tgt = var[0].targets[0] if len(var) else None
            current = f"{tgt.id.name if tgt and tgt.id else None}{tgt.data_path if tgt else ''}"
            if prop and (tgt is None or tgt.id != main or tgt.data_path != f'["{prop}"]'):
                if prop not in main:
                    main[prop] = 0.0
                    main.id_properties_ui(prop).update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
                    main.update_tag()
                    context.view_layer.update()  # let the driver see the new property
                _add_driver(key, '["note_on"]', "v", "v", main, f'["{prop}"]')
                fixed.append(f"{key.name}: {current} -> {prop}")

        count = 0
        for key, _prop in key_drivers(main, context.scene.objects):
            count += 1
            for fc in key.animation_data.drivers:
                d = fc.driver
                if not d.is_valid:
                    fixed.append(f"{key.name} {fc.data_path} (flagged invalid)")
                    d.is_valid = True
                if fc.data_path == '["note_on"]' and d.type == 'SCRIPTED' and d.expression != "v":
                    fixed.append(f"{key.name} note_on ('{d.expression}')")
                    d.expression = "v"
                elif fc.data_path == "rotation_euler" and fc.array_index == 0 and d.type == 'SCRIPTED':
                    if not d.expression.startswith("n *") or not d.is_valid:
                        fixed.append(f"{key.name} rotation ('{d.expression}')")
                        d.expression = f"n * {s.press_angle:.7f}"
        if fixed:
            self.report({'WARNING'}, f"{count} keys checked, fixed: " + ", ".join(fixed))
        else:
            self.report({'INFO'}, f"{count} keys checked, all drivers OK")
        return {'FINISHED'}


# -----------------------------
# 3: GLOWING KEYS
# -----------------------------
class PIANO_OT_glow_drivers(bpy.types.Operator):
    """Select keys: makes their materials unique and drives every Mix node factor from note_on"""
    bl_idname = "piano.glow_drivers"
    bl_label = "Glowing Keys"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        done = 0
        for obj in context.selected_objects:
            if not obj.material_slots or "note_on" not in obj:
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or not mat.use_nodes:
                    continue
                if mat.users > 1:
                    mat = mat.copy()
                    slot.material = mat
                for node in mat.node_tree.nodes:
                    if node.type not in ('MIX', 'MIX_SHADER') or not node.inputs:
                        continue
                    fc = node.inputs[0].driver_add("default_value")
                    d = fc.driver
                    d.type = 'AVERAGE'
                    while d.variables:
                        d.variables.remove(d.variables[0])
                    var = d.variables.new()
                    var.name = "note_on"
                    var.type = 'SINGLE_PROP'
                    var.targets[0].id_type = 'OBJECT'
                    var.targets[0].id = obj
                    var.targets[0].data_path = '["note_on"]'
                    done += 1
        self.report({'INFO'}, f"Added {done} glow drivers")
        return {'FINISHED'}


# -----------------------------
# 4: FINGER GUIDE CURVES
# -----------------------------
class PIANO_OT_build_guides(bpy.types.Operator):
    """Active curve: each spline is assigned to the key beneath it (ray cast), split into
    '<key>Guide' curves parented to that key, plus a '<key>GuideEmp' empty following it.
    The hands use these guides to know where fingertips touch each key"""
    bl_idname = "piano.build_guides"
    bl_label = "Build Finger Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'OBJECT' and obj is not None and obj.type == 'CURVE'

    def execute(self, context):
        curve_obj = context.active_object
        depsgraph = context.evaluated_depsgraph_get()
        named = {}
        for spline in curve_obj.data.splines:
            if spline.type == 'BEZIER':
                points = [bp.co.copy() for bp in spline.bezier_points]
            else:
                points = [Vector(p.co[:3]) for p in spline.points]
            if not points:
                continue
            world_points = [curve_obj.matrix_world @ p for p in points]
            hits = []
            for wp in world_points:
                hit, _loc, _n, _i, hit_obj, _m = context.scene.ray_cast(
                    depsgraph, wp + Vector((0, 0, 0.2)), Vector((0, 0, -1)))
                if hit:
                    hits.append(hit_obj.name)
            if not hits:
                continue
            key_name = max(set(hits), key=hits.count)
            named.setdefault(key_name, []).append(world_points)

        created = []
        for key_name, splines in named.items():
            guide_name = key_name + "Guide"
            data = bpy.data.curves.new(name=guide_name, type='CURVE')
            data.dimensions = '3D'
            data.use_path = True
            for pts in splines:
                sp = data.splines.new(type='POLY')
                sp.points.add(len(pts) - 1)
                for i, p in enumerate(pts):
                    sp.points[i].co = (p.x, p.y, p.z, 1.0)
            guide = bpy.data.objects.new(guide_name, data)
            context.collection.objects.link(guide)

            key_obj = bpy.data.objects.get(key_name)
            if key_obj:
                guide.parent = key_obj
                guide.matrix_parent_inverse = key_obj.matrix_world.inverted()

            emp = bpy.data.objects.new(guide_name + "Emp", None)
            emp.empty_display_size = 0.02
            context.collection.objects.link(emp)
            con = emp.constraints.new(type='FOLLOW_PATH')
            con.target = guide
            created.append(guide)

        curve_obj.hide_set(True)
        self.report({'INFO'}, f"Created {len(created)} guide curves")
        return {'FINISHED'}


classes = (
    PIANO_OT_prepare_octave,
    PIANO_OT_duplicate_octaves,
    PIANO_OT_build_key_drivers,
    PIANO_OT_repair_drivers,
    PIANO_OT_glow_drivers,
    PIANO_OT_build_guides,
)
