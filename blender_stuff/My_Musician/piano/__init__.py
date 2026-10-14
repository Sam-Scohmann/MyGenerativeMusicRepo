"""Piano: driven keyboard, live MIDI, rigged hands, recording, sound, MIDI export."""
import bpy

from . import live, midi_io, settings, ui


def register():
    settings.register()
    for cls in ui.classes:
        bpy.utils.register_class(cls)
    live.register()
    midi_io.refresh_ports()


def unregister():
    live.unregister()
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    settings.unregister()
