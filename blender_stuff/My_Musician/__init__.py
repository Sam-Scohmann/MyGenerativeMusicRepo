bl_info = {
    "name": "My Musician",
    "author": "Sam-Scohmann",
    "version": (2, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > My Musician",
    "description": "Instrument rigs played from MIDI: piano (keys + hands) and guitar string setup",
    "category": "Animation",
}

import bpy

from . import guitar, piano

INSTRUMENTS = (
    ('PIANO', "Piano", "Driven keyboard, live MIDI, rigged hands, recording and sound", 'OUTLINER_OB_SPEAKER', 0),
    ('GUITAR', "Guitar", "String empties, fret measurement and hooked string meshes", 'OUTLINER_DATA_GP_LAYER', 1),
)


def instrument_is(context, name):
    return getattr(context.scene, "my_musician_instrument", 'PIANO') == name


class MUSICIAN_PT_instrument(bpy.types.Panel):
    bl_label = "My Musician"
    bl_idname = "MUSICIAN_PT_instrument"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Musician"
    bl_order = 0

    def draw(self, context):
        row = self.layout.row(align=True)
        row.scale_y = 1.3
        row.prop(context.scene, "my_musician_instrument", expand=True)


def register():
    bpy.types.Scene.my_musician_instrument = bpy.props.EnumProperty(
        name="Instrument", items=INSTRUMENTS, default='PIANO',
        description="Which instrument's tools to show (saved per scene)")
    bpy.utils.register_class(MUSICIAN_PT_instrument)
    piano.register()
    guitar.register()


def unregister():
    guitar.unregister()
    piano.unregister()
    bpy.utils.unregister_class(MUSICIAN_PT_instrument)
    del bpy.types.Scene.my_musician_instrument
