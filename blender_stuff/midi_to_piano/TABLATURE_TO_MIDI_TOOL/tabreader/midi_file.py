"""Minimal Standard MIDI File (format 0) writer."""
import struct

PPQ = 480


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def write(path, song, channel=0, program=None, track_name="Tab"):
    events = []   # (tick, order, bytes)   order: offs before ons at same tick
    tempo_us = int(round(60_000_000 / song.tempo))
    num, den = song.time_sig
    events.append((0, 0, b"\xFF\x03" + _vlq(len(track_name)) + track_name.encode("ascii", "replace")))
    events.append((0, 0, b"\xFF\x51\x03" + tempo_us.to_bytes(3, "big")))
    events.append((0, 0, bytes([0xFF, 0x58, 0x04, num, max(0, den.bit_length() - 1), 24, 8])))
    if program is not None:
        events.append((0, 1, bytes([0xC0 | channel, program & 0x7F])))
    for n in song.notes:
        on = int(round(n.start * PPQ))
        off = max(on + 1, int(round((n.start + n.dur) * PPQ)) - 1)
        events.append((on, 2, bytes([0x90 | channel, n.pitch & 0x7F, n.velocity & 0x7F])))
        events.append((off, 1, bytes([0x80 | channel, n.pitch & 0x7F, 0])))
    events.sort(key=lambda e: (e[0], e[1]))

    body = bytearray()
    last = 0
    for tick, _, data in events:
        body += _vlq(tick - last) + data
        last = tick
    body += _vlq(0) + b"\xFF\x2F\x00"

    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ))
        f.write(b"MTrk" + struct.pack(">I", len(body)) + bytes(body))
