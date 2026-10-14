"""Built-in test performance so the rig can be checked without Sonic Pi."""

SIXTEENTH = 0.15  # seconds


def _n(name):
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
             "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    return (int(name[-1]) + 1) * 12 + names[name[:-1]]  # Sonic Pi numbering: C4 = 60


# Für Elise, bars 1-8 (3/8, one entry per sixteenth: (rh notes, lh notes, lh length))
_FUR_ELISE = [
    # pickup
    (["E5"], [], 0), (["D#5"], [], 0),
    # bar 1
    (["E5"], [], 0), (["D#5"], [], 0), (["E5"], [], 0), (["B4"], [], 0), (["D5"], [], 0), (["C5"], [], 0),
    # bar 2
    (["A4"], ["A2"], 1), ([], ["E3"], 1), ([], ["A3"], 1), (["C4"], [], 0), (["E4"], [], 0), (["A4"], [], 0),
    # bar 3
    (["B4"], ["E2"], 1), ([], ["E3"], 1), ([], ["G#3"], 1), (["E4"], [], 0), (["G#4"], [], 0), (["B4"], [], 0),
    # bar 4
    (["C5"], ["A2"], 1), ([], ["E3"], 1), ([], ["A3"], 1), (["E4"], [], 0), (["E5"], [], 0), (["D#5"], [], 0),
    # bar 5
    (["E5"], [], 0), (["D#5"], [], 0), (["E5"], [], 0), (["B4"], [], 0), (["D5"], [], 0), (["C5"], [], 0),
    # bar 6
    (["A4"], ["A2"], 1), ([], ["E3"], 1), ([], ["A3"], 1), (["C4"], [], 0), (["E4"], [], 0), (["A4"], [], 0),
    # bar 7
    (["B4"], ["E2"], 1), ([], ["E3"], 1), ([], ["G#3"], 1), (["E4"], [], 0), (["C5"], [], 0), (["B4"], [], 0),
    # bar 8
    (["A4"], ["A2"], 3), ([], ["E3"], 2), ([], ["A3"], 1), ([], [], 0), ([], [], 0), ([], [], 0),
]

_SCALE = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5",
          "D5", "C5", "B4", "A4", "G4", "F4", "E4", "D4", "C4"]

_CHORDS = [
    (["C4", "E4", "G4"], ["C3", "G3"]),
    (["A3", "C4", "F4"], ["F2", "C3"]),
    (["B3", "D4", "G4"], ["G2", "D3"]),
    (["C4", "E4", "G4", "C5"], ["C2", "C3"]),
]


def events():
    """Returns a time-sorted list of (seconds, 'on'|'off', note, velocity)."""
    out = []
    t = 0.0

    def play(note, start, length, vel=90):
        out.append((start, "on", _n(note), vel))
        out.append((start + length * 0.9, "off", _n(note), 0))

    for rh, lh, lh_len in _FUR_ELISE:
        for n in rh:
            play(n, t, SIXTEENTH * (2 if n in ("A4", "B4", "C5") and lh else 1))
        for n in lh:
            play(n, t, SIXTEENTH * max(1, lh_len), 70)
        t += SIXTEENTH

    t += SIXTEENTH * 4
    for n in _SCALE:
        play(n, t, SIXTEENTH)
        t += SIXTEENTH

    t += SIXTEENTH * 4
    for rh, lh in _CHORDS:
        for n in rh:
            play(n, t, SIXTEENTH * 6, 80)
        for n in lh:
            play(n, t, SIXTEENTH * 6, 70)
        t += SIXTEENTH * 8

    out.sort(key=lambda e: (e[0], e[1] == "on"))  # offs before ons at the same instant
    return out
