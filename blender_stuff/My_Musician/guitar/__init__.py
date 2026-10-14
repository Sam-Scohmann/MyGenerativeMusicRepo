"""Guitar string setup: start/end empties, fret measurement, hooked string meshes.

Ported from GuitarSetup.py (Guitar String Animator 2.0) with its bugs fixed;
operator ids (guitar.*) and the scene data (scene.guitar_props) are unchanged,
so existing .blend files keep working.
"""
import json
import re

import blf
import bmesh
import bpy
import gpu
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
VIBRATE_DIVISIONS = 7  # extra vertices between the last fret and the bridge

STRING_START_RE = re.compile(r"^STRING_(\d+)$")
FRET_CTRL_RE = re.compile(r"^FRET_(\d+)_STRING_(\d+)_CTRL$")
VIBRATE_CTRL_RE = re.compile(r"^VIBRATE_(\d+)_STRING_(\d+)_CTRL$")

# Measurement session (the measured distances themselves live on the scene)
_measure = {"active": False, "start": None, "end": None, "area": None}
_draw_handlers = []


# -----------------------------
# NOTES
# -----------------------------
def parse_note(note_string):
    """'E2' -> semitones from C0, or None if it isn't a note name."""
    m = re.fullmatch(r"\s*([A-Ga-g]#?)(-?\d+)?\s*", note_string or "")
    if not m:
        return None
    name = m.group(1).upper()
    if name not in NOTE_NAMES:
        return None
    octave = int(m.group(2)) if m.group(2) else 2  # default octave, as before
    return NOTE_NAMES.index(name) + octave * 12


def get_note_at_fret(open_note_string, fret_number):
    """Note name at a fret, '' if the open note can't be parsed."""
    offset = parse_note(open_note_string)
    if offset is None:
        return ""
    total = offset + fret_number
    return f"{NOTE_NAMES[total % 12]}{total // 12}"


def parse_tunings(props):
    return [t.strip() for t in props.tuning_strings.split(',')]


# -----------------------------
# STORED MEASUREMENTS
# -----------------------------
def get_frets(scene):
    try:
        data = json.loads(scene.guitar_props.fret_data or "[]")
    except ValueError:
        return []
    return [f for f in data if isinstance(f, dict) and "distance" in f]


def set_frets(scene, frets):
    scene.guitar_props.fret_data = json.dumps(frets)


def string_pairs():
    """[(string number, start empty, end empty)] sorted by string number."""
    pairs = []
    for obj in bpy.data.objects:
        m = STRING_START_RE.match(obj.name)
        if m:
            end = bpy.data.objects.get(f"{obj.name}_END")
            if end:
                pairs.append((int(m.group(1)), obj, end))
    pairs.sort(key=lambda p: p[0])
    return pairs


def _world(obj):
    return obj.matrix_world.translation.copy()


# -----------------------------
# EMPTIES
# -----------------------------
class GUITAR_OT_create_start_end_empties(bpy.types.Operator):
    """Create start and end empties for strings"""
    bl_idname = "guitar.create_start_end_empties"
    bl_label = "Create Start/End Empties"
    bl_options = {'REGISTER', 'UNDO'}

    num_strings: bpy.props.IntProperty(name="Number of Strings", default=6, min=1, max=12,
                                       description="Number of strings to create empties for")

    def execute(self, context):
        for i in range(1, self.num_strings + 1):
            for name, y in ((f"STRING_{i}", 0.0), (f"STRING_{i}_END", 5.0)):
                if name not in bpy.data.objects:
                    empty = bpy.data.objects.new(name, None)
                    empty.empty_display_type = 'CUBE'
                    empty.empty_display_size = 0.2
                    empty.location = (i * 0.5, y, 0)
                    context.collection.objects.link(empty)
        self.report({'INFO'}, f"Created {self.num_strings} string start/end empties")
        return {'FINISHED'}


# -----------------------------
# MEASUREMENT
# -----------------------------
def _surface_point(context, region, coord):
    """3D point under `coord` (region pixels): on the guitar's surface if
    something is hit, otherwise at the 3D cursor's depth."""
    rv3d = region.data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    hit, location, *_ = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
    if hit:
        return location
    return view3d_utils.region_2d_to_location_3d(region, rv3d, coord, context.scene.cursor.location)


def _draw_3d():
    if not _measure["active"]:
        return
    start, end = _measure["start"], _measure["end"]
    if start is None:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.point_size_set(8.0)
    gpu.state.line_width_set(2.0)
    shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
    batch_for_shader(shader, 'POINTS', {"pos": [start]}).draw(shader)
    if end is not None:
        shader.uniform_float("color", (1.0, 0.0, 0.0, 1.0))
        batch_for_shader(shader, 'LINES', {"pos": [start, end]}).draw(shader)
    gpu.state.point_size_set(1.0)
    gpu.state.line_width_set(1.0)


def _draw_text():
    if not _measure["active"]:
        return
    context = bpy.context
    if context.area != _measure["area"]:
        return
    frets = get_frets(context.scene)
    font_id = 0
    top = context.region.height - 100
    blf.size(font_id, 16)
    blf.color(font_id, 1, 1, 1, 1)
    step = "Click to set end point" if _measure["start"] is not None else "Click to set start point"
    lines = [f"Fret {len(frets)}: {step}   (Right-click: cancel point, Esc: finish)",
             f"Measured frets: {len(frets)}"]
    lines += [f"  Fret {f['fret']}: {f['distance']:.3f}" for f in frets[-3:]]
    for i, line in enumerate(lines):
        blf.position(font_id, 20, top - i * 20, 0)
        blf.draw(font_id, line)


def _add_draw_handlers():
    if not _draw_handlers:
        _draw_handlers.append(('WINDOW', bpy.types.SpaceView3D.draw_handler_add(_draw_3d, (), 'WINDOW', 'POST_VIEW')))
        _draw_handlers.append(('WINDOW', bpy.types.SpaceView3D.draw_handler_add(_draw_text, (), 'WINDOW', 'POST_PIXEL')))


def _remove_draw_handlers():
    while _draw_handlers:
        region_type, handler = _draw_handlers.pop()
        bpy.types.SpaceView3D.draw_handler_remove(handler, region_type)


def _end_measuring():
    _measure.update(active=False, start=None, end=None)
    area = _measure["area"]
    _measure["area"] = None
    _remove_draw_handlers()
    try:
        if area:
            area.tag_redraw()
    except ReferenceError:
        pass


class GUITAR_OT_start_measure(bpy.types.Operator):
    """Start recording fret measurements: click a start and an end point for each fret in the 3D view"""
    bl_idname = "guitar.start_measure"
    bl_label = "Start Recording Fret Measurements"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        area = context.area if context.area and context.area.type == 'VIEW_3D' else None
        if area is None:
            # started from a panel in another editor: use the largest 3D view
            areas = [a for a in context.screen.areas if a.type == 'VIEW_3D']
            area = max(areas, key=lambda a: a.width * a.height, default=None)
        if area is None:
            self.report({'ERROR'}, "No 3D View to measure in")
            return {'CANCELLED'}
        set_frets(context.scene, [])
        _measure.update(active=True, start=None, end=None, area=area)
        _add_draw_handlers()
        context.window_manager.modal_handler_add(self)
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return self.invoke(context, None)

    def _window_region(self, context, event):
        """The 3D view's main region under the mouse, or None."""
        area = _measure["area"]
        for region in area.regions:
            if region.type == 'WINDOW' and (region.x <= event.mouse_x < region.x + region.width
                                            and region.y <= event.mouse_y < region.y + region.height):
                return region
        return None

    def modal(self, context, event):
        if not _measure["active"]:
            _end_measuring()
            return {'FINISHED'}
        try:
            _measure["area"].tag_redraw()
        except ReferenceError:  # the 3D view was closed
            _end_measuring()
            return {'CANCELLED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            count = len(get_frets(context.scene))
            _end_measuring()
            self.report({'INFO'}, f"Recorded {count} fret measurements")
            return {'FINISHED'}

        if event.type not in {'LEFTMOUSE', 'RIGHTMOUSE'} or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        region = self._window_region(context, event)
        if region is None:
            return {'PASS_THROUGH'}  # clicks on panels/buttons aren't measurements

        if event.type == 'RIGHTMOUSE':
            _measure.update(start=None, end=None)
            self.report({'INFO'}, "Measurement point cancelled")
            return {'RUNNING_MODAL'}

        point = _surface_point(context, region, (event.mouse_x - region.x, event.mouse_y - region.y))

        frets = get_frets(context.scene)
        if _measure["start"] is None or _measure["end"] is not None:
            _measure.update(start=point, end=None)
            self.report({'INFO'}, f"Start point set for fret {len(frets)}")
        else:
            _measure["end"] = point
            distance = (point - _measure["start"]).length
            frets.append({"fret": len(frets), "distance": distance})
            set_frets(context.scene, frets)
            self.report({'INFO'}, f"Fret {len(frets) - 1} distance: {distance:.4f}")
        return {'RUNNING_MODAL'}


class GUITAR_OT_stop_measure(bpy.types.Operator):
    """Stop recording fret measurements"""
    bl_idname = "guitar.stop_measure"
    bl_label = "Stop Recording Fret Measurements"

    def execute(self, context):
        _end_measuring()
        self.report({'INFO'}, f"Measurement stopped. {len(get_frets(context.scene))} frets recorded.")
        return {'FINISHED'}


class GUITAR_OT_reset_measurements(bpy.types.Operator):
    """Reset all recorded fret measurements"""
    bl_idname = "guitar.reset_measurements"
    bl_label = "Reset Measurements"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        set_frets(context.scene, [])
        self.report({'INFO'}, "Measurements reset")
        return {'FINISHED'}


# -----------------------------
# STRINGS
# -----------------------------
def _control_empty(context, name, display_type, size, location):
    """Get or create a controller empty and put it at `location`."""
    empty = bpy.data.objects.get(name)
    if empty is None:
        empty = bpy.data.objects.new(name, None)
        empty.empty_display_type = display_type
        empty.empty_display_size = size
        context.collection.objects.link(empty)
    if empty.parent is None:
        empty.location = location
    else:
        empty.matrix_world.translation = location
    return empty


def _hook(obj, name, target, vertex_index, vertex_co):
    """Hook one vertex to `target` so that it stays exactly where it is now."""
    mod = obj.modifiers.new(name=name, type='HOOK')
    mod.object = target
    mod.vertex_indices_set([vertex_index])
    mod.center = vertex_co
    # deform = obj^-1 @ target @ matrix_inverse; identity at the current pose
    mod.matrix_inverse = (obj.matrix_world.inverted() @ target.matrix_world).inverted()
    return mod


def create_guitar_string(context, string_number, start_empty, end_empty, fret_data, open_note):
    """A mesh edge chain for one string, every vertex hooked to a controller
    empty. Returns (object, warnings)."""
    warnings = []
    obj_name = f"STRING_{string_number}_CURVE"
    old = bpy.data.objects.get(obj_name)
    if old:
        old_mesh = old.data if old.type == 'MESH' else None
        bpy.data.objects.remove(old, do_unlink=True)
        if old_mesh and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    mesh = bpy.data.meshes.new(f"STRING_{string_number}_MESH")
    obj = bpy.data.objects.new(obj_name, mesh)
    context.collection.objects.link(obj)
    obj.matrix_world = Matrix.Identity(4)

    start_pos, end_pos = _world(start_empty), _world(end_empty)
    string_vec = end_pos - start_pos
    string_length = string_vec.length
    if string_length == 0:
        bpy.data.objects.remove(obj, do_unlink=True)
        return None, [f"String {string_number}: start and end empties are at the same place"]
    string_dir = string_vec.normalized()

    vertices = [start_pos]
    hooks = [("Hook_Start", start_empty)]

    cumulative = 0.0
    for i, fret in enumerate(fret_data):
        cumulative += fret["distance"]
        if cumulative > string_length:
            cumulative = string_length
            warnings.append(f"String {string_number}: fret {i + 1} is past the string's end, clamped")
        pos = start_pos + string_dir * cumulative
        ctrl = _control_empty(context, f"FRET_{i + 1}_STRING_{string_number}_CTRL", 'CUBE',
                              0.12 * fret["distance"], pos)
        ctrl["note"] = get_note_at_fret(open_note, i + 1)
        ctrl["fret_number"] = i + 1
        ctrl["string_number"] = string_number
        ctrl["fret_ratio"] = cumulative / string_length
        vertices.append(pos)
        hooks.append((f"Hook_Fret_{i + 1}", ctrl))

    if fret_data:
        # vibrating section between the last fret and the bridge
        last_fret = vertices[-1]
        remaining = (end_pos - last_fret).length
        for idx in range(1, VIBRATE_DIVISIONS + 1):
            pos = last_fret + string_dir * (remaining * idx / (VIBRATE_DIVISIONS + 0.5))
            ctrl = _control_empty(context, f"VIBRATE_{idx}_STRING_{string_number}_CTRL", 'SPHERE', 0.01, pos)
            ctrl["type"] = "vibrating_section"
            ctrl["string_number"] = string_number
            ctrl["fret_ratio"] = (pos - start_pos).length / string_length
            vertices.append(pos)
            hooks.append((f"Hook_Vibrate_{idx}", ctrl))

    vertices.append(end_pos)
    hooks.append(("Hook_End", end_empty))

    bm = bmesh.new()
    verts = [bm.verts.new(v) for v in vertices]
    for a, b in zip(verts, verts[1:]):
        bm.edges.new((a, b))
    bm.to_mesh(mesh)
    bm.free()

    # new controllers only get their world matrix once the depsgraph has seen them
    context.view_layer.update()
    for index, ((name, target), co) in enumerate(zip(hooks, vertices)):
        _hook(obj, name, target, index, co)

    subdiv = obj.modifiers.new(name="Subdivision", type='SUBSURF')
    subdiv.levels = 2
    subdiv.render_levels = 3
    return obj, warnings


class GUITAR_OT_create_strings(bpy.types.Operator):
    """Create strings with fret positions"""
    bl_idname = "guitar.create_strings"
    bl_label = "Create Animation Strings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        frets = get_frets(context.scene)
        if not frets:
            self.report({'ERROR'}, "No fret measurements found. Please measure frets first.")
            return {'CANCELLED'}
        pairs = string_pairs()
        if not pairs:
            self.report({'ERROR'}, "No start/end empty pairs found. Please create them first.")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        tunings = parse_tunings(context.scene.guitar_props)
        created, warnings = [], []
        for number, start, end in pairs:
            open_note = tunings[number - 1] if number - 1 < len(tunings) else "E2"
            if parse_note(open_note) is None:
                warnings.append(f"String {number}: '{open_note}' isn't a note name, no note names set")
            obj, warns = create_guitar_string(context, number, start, end, frets, open_note)
            warnings += warns
            if obj:
                created.append(obj)

        msg = f"Created {len(created)} strings with {len(frets)} frets"
        if warnings:
            self.report({'WARNING'}, msg + ". " + "; ".join(warnings))
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class GUITAR_OT_update_fret_positions(bpy.types.Operator):
    """Move fret (and vibrating-section) controllers to the same proportions along
    each string after its start/end empties were moved"""
    bl_idname = "guitar.update_fret_positions"
    bl_label = "Update Fret Positions"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ends = {number: (start, end) for number, start, end in string_pairs()}
        moved = 0
        for ctrl in bpy.data.objects:
            m = FRET_CTRL_RE.match(ctrl.name) or VIBRATE_CTRL_RE.match(ctrl.name)
            if not m:
                continue
            pair = ends.get(int(m.group(2)))
            if not pair:
                continue
            start_pos, end_pos = _world(pair[0]), _world(pair[1])
            length = (end_pos - start_pos).length
            if length == 0:
                continue
            ratio = ctrl.get("fret_ratio")
            if ratio is None:  # created by the old add-on: keep its distance from the start
                ratio = min((_world(ctrl) - start_pos).length / length, 1.0)
                ctrl["fret_ratio"] = ratio
            new_pos = start_pos.lerp(end_pos, ratio)
            if ctrl.parent is None:
                ctrl.location = new_pos
            else:
                ctrl.matrix_world.translation = new_pos
            moved += 1
        self.report({'INFO'}, f"Fret positions updated ({moved} controllers)")
        return {'FINISHED'}


class GUITAR_OT_update_note_names(bpy.types.Operator):
    """Update note names on fret controllers based on tunings"""
    bl_idname = "guitar.update_note_names"
    bl_label = "Update Note Names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        tunings = parse_tunings(context.scene.guitar_props)
        updated, bad = 0, set()
        for ctrl in bpy.data.objects:
            m = FRET_CTRL_RE.match(ctrl.name)
            if not m:
                continue
            fret, string = int(m.group(1)), int(m.group(2))
            if string - 1 >= len(tunings):
                continue
            if parse_note(tunings[string - 1]) is None:
                bad.add(tunings[string - 1])
                continue
            ctrl["note"] = get_note_at_fret(tunings[string - 1], fret)
            updated += 1
        if bad:
            self.report({'WARNING'}, f"Updated {updated} controllers; not note names: {', '.join(sorted(bad))}")
        else:
            self.report({'INFO'}, f"Note names updated ({updated} controllers)")
        return {'FINISHED'}


# -----------------------------
# SAVE / LOAD
# -----------------------------
class GUITAR_OT_save_data(bpy.types.Operator, ExportHelper):
    """Save fret measurements and tunings to file"""
    bl_idname = "guitar.save_data"
    bl_label = "Save Guitar Data"

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = "guitar_data.json"
        return super().invoke(context, event)

    def execute(self, context):
        props = context.scene.guitar_props
        data = {
            'fret_distances': get_frets(context.scene),
            'tuning_strings': props.tuning_strings,
            'num_strings': props.num_strings,
        }
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            self.report({'ERROR'}, f"Couldn't write {self.filepath}: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Data saved to {self.filepath}")
        return {'FINISHED'}


class GUITAR_OT_load_data(bpy.types.Operator, ImportHelper):
    """Load fret measurements and tunings from file"""
    bl_idname = "guitar.load_data"
    bl_label = "Load Guitar Data"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, encoding='utf-8') as f:
                data = json.load(f)
            frets = [f for f in data.get('fret_distances', [])
                     if isinstance(f, dict) and isinstance(f.get('distance'), (int, float))]
        except (OSError, ValueError, AttributeError) as e:
            self.report({'ERROR'}, f"Failed to load data: {e}")
            return {'CANCELLED'}
        set_frets(context.scene, frets)
        props = context.scene.guitar_props
        props.tuning_strings = data.get('tuning_strings', "E2,A2,D3,G3,B3,E4")
        props.num_strings = data.get('num_strings', 6)
        self.report({'INFO'}, f"Loaded {len(frets)} frets from {self.filepath}")
        return {'FINISHED'}


# -----------------------------
# UI
# -----------------------------
class GuitarProperties(bpy.types.PropertyGroup):
    num_strings: bpy.props.IntProperty(name="Number of Strings", default=6, min=1, max=12,
                                       description="Number of strings")
    tuning_strings: bpy.props.StringProperty(name="Tunings", default="E2,A2,D3,G3,B3,E4",
                                             description="Comma-separated list of string tunings")
    fret_data: bpy.props.StringProperty(name="Fret Measurements", default="[]", options={'HIDDEN'},
                                        description="Measured fret distances (JSON), saved with the file")


class GUITAR_PT_main_panel(bpy.types.Panel):
    """Guitar string setup: empties, fret measurement, hooked strings"""
    bl_label = "Guitar String Setup"
    bl_idname = "GUITAR_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'My Musician'
    bl_order = 10
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        from .. import instrument_is
        return instrument_is(context, 'GUITAR')

    def draw(self, context):
        layout = self.layout
        props = context.scene.guitar_props
        frets = get_frets(context.scene)

        box = layout.box()
        box.label(text="Create Empties", icon='EMPTY_DATA')
        box.operator("guitar.create_start_end_empties",
                     text="Create Start/End Empties").num_strings = props.num_strings

        box = layout.box()
        box.label(text="String Tunings", icon='SOUND')
        box.prop(props, "num_strings")
        box.prop(props, "tuning_strings", text="Tunings (comma-separated)")
        tunings = parse_tunings(props)
        col = box.column(align=True)
        for i in range(min(props.num_strings, len(tunings))):
            ok = parse_note(tunings[i]) is not None
            col.label(text=f"String {i + 1}: {tunings[i]}" + ("" if ok else "  (not a note)"),
                      icon='NONE' if ok else 'ERROR')

        box = layout.box()
        box.label(text="Fret Measurement", icon='VIEWZOOM')
        row = box.row(align=True)
        row.operator("guitar.start_measure", text="Start Recording", icon='PLAY',
                     depress=_measure["active"])
        row.operator("guitar.stop_measure", text="Stop", icon='PAUSE')
        if frets:
            box.label(text=f"Recorded: {len(frets)} frets")
            col = box.column(align=True)
            for fret in frets[-5:]:
                col.label(text=f"  Fret {fret['fret']}: {fret['distance']:.3f}")

        box = layout.box()
        box.label(text="String Creation", icon='CURVE_PATH')
        box.operator("guitar.create_strings", text="Create Strings", icon='MESH_DATA')

        box = layout.box()
        box.label(text="Utilities", icon='TOOL_SETTINGS')
        if frets:
            box.operator("guitar.reset_measurements", text="Reset Measurements", icon='TRASH')
        box.operator("guitar.update_fret_positions", text="Update Fret Positions", icon='SNAP_GRID')
        box.operator("guitar.update_note_names", text="Update Note Names", icon='FILE_TEXT')

        box = layout.box()
        box.label(text="Save/Load Data", icon='FILE_FOLDER')
        row = box.row(align=True)
        row.operator("guitar.save_data", text="Save", icon='EXPORT')
        row.operator("guitar.load_data", text="Load", icon='IMPORT')

        box = layout.box()
        box.label(text="Setup Instructions:", icon='INFO')
        for line in ("1. Set number of strings & tunings",
                     "2. Click 'Create Start/End Empties'",
                     "3. Position the empties",
                     "4. Click 'Start Recording'",
                     "5. Click start/end of each fret on the guitar",
                     "   (right-click cancels a point)",
                     "6. Press ESC when done",
                     "7. Click 'Create Strings'",
                     "",
                     "Fret controllers have 'note' property",
                     "with musical note names"):
            box.label(text=line)


classes = (
    GuitarProperties,
    GUITAR_OT_create_start_end_empties,
    GUITAR_OT_start_measure,
    GUITAR_OT_stop_measure,
    GUITAR_OT_reset_measurements,
    GUITAR_OT_create_strings,
    GUITAR_OT_update_fret_positions,
    GUITAR_OT_update_note_names,
    GUITAR_OT_save_data,
    GUITAR_OT_load_data,
    GUITAR_PT_main_panel,
)


def register():
    from . import session, settings, ui
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.guitar_props = bpy.props.PointerProperty(type=GuitarProperties)
    settings.register()
    for cls in ui.classes:
        bpy.utils.register_class(cls)
    session.register()


def unregister():
    from . import session, settings, ui
    session.unregister()
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    settings.unregister()
    _end_measuring()
    del bpy.types.Scene.guitar_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
