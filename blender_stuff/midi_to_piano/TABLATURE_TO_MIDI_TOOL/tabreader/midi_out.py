"""Dependency-free MIDI output for Windows (winmm.dll via ctypes) plus a
threaded note player.

python-rtmidi has no prebuilt wheels for Python 3.13 on Windows, so this talks
to the multimedia API directly (same approach as My_Musician/piano/midi_winmm.py).
Use a loopback driver such as loopMIDI to route into a DAW or Blender.
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

MAXPNAMELEN = 32

_winmm = None
if sys.platform == "win32":
    try:
        _winmm = ctypes.WinDLL("winmm")
    except OSError:
        _winmm = None


class _MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * MAXPNAMELEN),
        ("wTechnology", wintypes.WORD),
        ("wVoices", wintypes.WORD),
        ("wNotes", wintypes.WORD),
        ("wChannelMask", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


if _winmm:
    _winmm.midiOutGetNumDevs.restype = wintypes.UINT
    _winmm.midiOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(_MIDIOUTCAPSW), wintypes.UINT]
    _winmm.midiOutGetDevCapsW.restype = wintypes.UINT
    _winmm.midiOutOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT,
                                   ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    _winmm.midiOutOpen.restype = wintypes.UINT
    _winmm.midiOutShortMsg.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _winmm.midiOutShortMsg.restype = wintypes.UINT
    for _fn in ("midiOutReset", "midiOutClose"):
        getattr(_winmm, _fn).argtypes = [wintypes.HANDLE]
        getattr(_winmm, _fn).restype = wintypes.UINT


def available():
    return _winmm is not None


def output_names():
    if not _winmm:
        return []
    names = []
    caps = _MIDIOUTCAPSW()
    for i in range(_winmm.midiOutGetNumDevs()):
        if _winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def resolve(name):
    """Exact match first, then case-insensitive substring. -> (index, full name)."""
    names = output_names()
    for i, n in enumerate(names):
        if n == name:
            return i, n
    low = name.strip().lower()
    for i, n in enumerate(names):
        if low and low in n.lower():
            return i, n
    return None, None


class MidiOut:
    def __init__(self, name):
        if not _winmm:
            raise RuntimeError("winmm MIDI is only available on Windows")
        idx, full = resolve(name)
        if idx is None:
            raise RuntimeError(f"MIDI output '{name}' not found. Available: {', '.join(output_names()) or 'none'}")
        self.name = full
        self._h = wintypes.HANDLE()
        err = _winmm.midiOutOpen(ctypes.byref(self._h), idx, 0, 0, 0)
        if err:
            raise RuntimeError(f"midiOutOpen failed for '{full}' (error {err}); is another app holding it?")
        self._lock = threading.Lock()

    def send(self, status, d1=0, d2=0):
        with self._lock:
            if self._h:
                _winmm.midiOutShortMsg(self._h, status | (d1 << 8) | (d2 << 16))

    def note_on(self, ch, note, vel):
        self.send(0x90 | ch, note & 0x7F, vel & 0x7F)

    def note_off(self, ch, note):
        self.send(0x80 | ch, note & 0x7F, 0)

    def all_notes_off(self, ch):
        self.send(0xB0 | ch, 123, 0)

    def close(self):
        with self._lock:
            if self._h:
                _winmm.midiOutReset(self._h)
                _winmm.midiOutClose(self._h)
                self._h = None


class Player:
    """Plays a list of Notes (beats) on a MidiOut in a background thread.

    on_position(beat) is called ~30x/s from the player thread; on_done() at the end.
    """

    def __init__(self, out, channel, tempo, notes, start_beat, end_beat,
                 on_position=None, on_done=None, speed=1.0):
        self.out, self.ch = out, channel
        self.spb = 60.0 / (tempo * speed)
        self.start_beat, self.end_beat = start_beat, end_beat
        self.on_position, self.on_done = on_position, on_done
        ev = []
        for n in notes:
            if n.start + n.dur <= start_beat or n.start >= end_beat:
                continue
            s = max(n.start, start_beat)
            e = min(n.start + n.dur, end_beat)
            ev.append((s, 1, n.pitch, n.velocity))
            ev.append((e - 1e-4, 0, n.pitch, 0))   # off just before a re-strike
        ev.sort(key=lambda e: (e[0], e[1]))
        self.events = ev
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    @property
    def running(self):
        return self._thread.is_alive()

    def _run(self):
        t0 = time.perf_counter()
        on = {}
        i = 0
        last_pos = -1
        try:
            while not self._stop.is_set():
                beat = self.start_beat + (time.perf_counter() - t0) / self.spb
                while i < len(self.events) and self.events[i][0] <= beat:
                    _, kind, pitch, vel = self.events[i]
                    if kind:
                        if on.get(pitch):
                            self.out.note_off(self.ch, pitch)
                        self.out.note_on(self.ch, pitch, vel)
                        on[pitch] = on.get(pitch, 0) + 1
                    elif on.get(pitch):
                        self.out.note_off(self.ch, pitch)
                        on[pitch] = 0
                    i += 1
                if self.on_position and time.perf_counter() - last_pos > 1 / 30:
                    last_pos = time.perf_counter()
                    self.on_position(beat)
                if beat >= self.end_beat and i >= len(self.events):
                    break
                nxt = self.events[i][0] if i < len(self.events) else self.end_beat
                wait = (nxt - beat) * self.spb
                time.sleep(max(0.0005, min(wait, 1 / 60)))
        finally:
            for p, c in on.items():
                if c:
                    self.out.note_off(self.ch, p)
            self.out.all_notes_off(self.ch)
            if self.on_done:
                self.on_done()
