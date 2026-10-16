"""Tablature image -> MIDI.

vision.py   finds staffs, bar lines, bar numbers, fret glyphs and rhythm stems
glyphs.py   classifies digit glyphs (fret numbers, bar numbers, tempo)
timing.py   turns detected columns into timed notes per bar
midi_out.py dependency-free Windows MIDI output (winmm via ctypes)
midi_file.py dependency-free Standard MIDI File writer
app.py      PySide6 drag-and-drop UI
"""
