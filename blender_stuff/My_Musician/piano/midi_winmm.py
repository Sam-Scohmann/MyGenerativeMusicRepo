"""Dependency-free MIDI input for Windows, using winmm.dll through ctypes.

Blender 5.x ships Python 3.13, for which python-rtmidi has no prebuilt
Windows wheels, so mido can't open ports without a C++ build toolchain.
This talks to the Windows multimedia API directly instead.
"""
import ctypes
import queue
import sys
from ctypes import wintypes

CALLBACK_FUNCTION = 0x00030000
MIM_DATA = 0x3C3
MAXPNAMELEN = 32

_winmm = None
if sys.platform == "win32":
    try:
        _winmm = ctypes.WinDLL("winmm")
    except OSError:
        _winmm = None


class _MIDIINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("dwSupport", wintypes.DWORD),
    ]


_MidiInProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT,
                                 ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)

if _winmm:
    _winmm.midiInGetNumDevs.restype = wintypes.UINT
    _winmm.midiInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(_MIDIINCAPSW), wintypes.UINT]
    _winmm.midiInGetDevCapsW.restype = wintypes.UINT
    _winmm.midiInOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT,
                                  ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    _winmm.midiInOpen.restype = wintypes.UINT
    for _fn in ("midiInStart", "midiInStop", "midiInReset", "midiInClose"):
        getattr(_winmm, _fn).argtypes = [wintypes.HANDLE]
        getattr(_winmm, _fn).restype = wintypes.UINT


def available():
    return _winmm is not None


def input_names():
    if not _winmm:
        return []
    names = []
    caps = _MIDIINCAPSW()
    for i in range(_winmm.midiInGetNumDevs()):
        if _winmm.midiInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


class Message:
    """The subset of mido.Message the live session uses."""
    __slots__ = ("type", "channel", "note", "velocity")

    def __init__(self, status, data1, data2):
        kind = status & 0xF0
        self.channel = status & 0x0F
        self.note = data1
        self.velocity = data2
        if kind == 0x90:
            self.type = "note_on"
        elif kind == 0x80:
            self.type = "note_off"
        else:
            self.type = "other"

    def __repr__(self):
        return f"Message({self.type}, ch={self.channel}, note={self.note}, vel={self.velocity})"


class InputPort:
    def __init__(self, name):
        names = input_names()
        if name not in names:
            raise OSError(f"MIDI input '{name}' not found")
        self.name = name
        self._queue = queue.SimpleQueue()
        # keep a reference: winmm calls this from its own thread
        self._callback = _MidiInProc(self._on_midi)
        self._handle = wintypes.HANDLE()
        err = _winmm.midiInOpen(ctypes.byref(self._handle), names.index(name),
                                ctypes.cast(self._callback, ctypes.c_void_p).value, 0, CALLBACK_FUNCTION)
        if err:
            raise OSError(f"midiInOpen failed (error {err}): the port may be in use by another program")
        _winmm.midiInStart(self._handle)

    def _on_midi(self, _handle, msg, _instance, param1, _param2):
        if msg == MIM_DATA:
            self._queue.put(Message(param1 & 0xFF, (param1 >> 8) & 0x7F, (param1 >> 16) & 0x7F))

    def iter_pending(self):
        while True:
            try:
                yield self._queue.get_nowait()
            except queue.Empty:
                return

    def close(self):
        if self._handle:
            _winmm.midiInStop(self._handle)
            _winmm.midiInReset(self._handle)
            _winmm.midiInClose(self._handle)
            self._handle = None
