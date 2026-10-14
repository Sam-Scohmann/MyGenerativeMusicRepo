"""MIDI input backends: Windows' own winmm (no dependencies), else mido.
The rest of the add-on (test playback, setup tools) works without either."""
import os
import subprocess
import sys
import time

import bpy

from . import midi_winmm

NO_PORT = "__none__"
SCAN_INTERVAL = 1.0  # seconds a port scan is reused (the dropdown asks on every redraw)

# EnumProperty callbacks must keep their strings alive (Blender bug otherwise)
_port_items_cache = []
_scan = {"time": -1e9, "names": []}


def modules_dir():
    return bpy.utils.user_resource('SCRIPTS', path="modules", create=True)


def _ensure_modules_on_path():
    path = modules_dir()
    if path not in sys.path:
        sys.path.append(path)


def get_mido():
    _ensure_modules_on_path()
    try:
        import mido
        return mido
    except ImportError:
        return None


def backend_name():
    if midi_winmm.available():
        return "Windows MIDI"
    if get_mido():
        return "mido"
    return None


def input_names():
    if midi_winmm.available():
        return midi_winmm.input_names()
    mido = get_mido()
    if not mido:
        return []
    try:
        return mido.get_input_names()
    except Exception as e:  # backend missing (no python-rtmidi) etc.
        print(f"[PianoMIDI] Can't list MIDI inputs: {e}")
        return []


def scan_ports(force=False):
    """Names of the MIDI inputs currently connected (rescanned at most once a second)."""
    now = time.monotonic()
    if force or now - _scan["time"] > SCAN_INTERVAL:
        _scan["names"] = input_names()
        _scan["time"] = now
    return _scan["names"]


def refresh_ports():
    scan_ports(force=True)


def port_enum_items(selected):
    items = [(NO_PORT, "None", "Don't listen to MIDI (test piece only)", 'CANCEL', 0)]
    names = scan_ports()
    for name in names:
        items.append((name, name, f"Listen on '{name}'", 'CHECKBOX_HLT' if name == selected else 'NONE', len(items)))
    if selected and selected not in names:
        # keep a saved choice visible while that device is unplugged / loopMIDI is closed
        items.append((selected, f"{selected} (not connected)", "This input isn't connected right now",
                      'ERROR', len(items)))
    _port_items_cache[:] = items
    return _port_items_cache


def port_enum_index(selected):
    for item in port_enum_items(selected):
        if item[0] == (selected or NO_PORT):
            return item[4]
    return 0


def port_from_index(index):
    for item in _port_items_cache:
        if item[4] == index:
            return "" if item[0] == NO_PORT else item[0]
    return ""


def is_connected(name):
    return bool(name) and name in scan_ports()


def open_input(name):
    if midi_winmm.available():
        return midi_winmm.InputPort(name)
    return get_mido().open_input(name)


def install_dependencies():
    """pip-install mido + python-rtmidi into Blender's user modules folder."""
    target = modules_dir()
    py = sys.executable
    env = dict(os.environ, PYTHONNOUSERSITE="1")
    subprocess.run([py, "-m", "ensurepip", "--upgrade"], env=env, check=False)
    result = subprocess.run(
        [py, "-m", "pip", "install", "--upgrade", "--target", target, "mido", "python-rtmidi"],
        env=env, capture_output=True, text=True)
    print(result.stdout)
    print(result.stderr)
    _ensure_modules_on_path()
    return result.returncode == 0, result.stderr.strip().splitlines()[-1:] if result.stderr else []
