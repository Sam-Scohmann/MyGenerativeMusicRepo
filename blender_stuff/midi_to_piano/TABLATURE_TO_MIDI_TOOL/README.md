# Tab Reader: tablature image to MIDI

Drop a tab screenshot (Songsterr-style bass/guitar tab **or drum notation**) and it reads the
staffs, bar numbers, fret numbers or drum noteheads, and rhythm. You can then audition rows on a
MIDI port, stream the whole song, or export a `.mid`.

```
pip install -r requirements.txt
python run_tab_reader.py                 # UI (or double-click "Tab Reader.bat")
python tab2midi.py tab.png --overlay debug.png   # headless
```

## UI
- Drag and drop an image anywhere on the window, or use **Open image…**.
- Each detected staff row is shown cropped from the source, with a ▶ button to play just that row.
  The header says whether every bar's rhythm added up to the time signature.
- **Show detections** swaps the crops for the debug overlay. Green boxes are frets, purple boxes
  are tied "(n)" frets, orange boxes are grace notes, blue lines are stems (labelled with beam
  count, `h` for half, `.` for dotted), red lines are bar lines, and bar numbers are drawn green
  when read from the image and red when inferred.
- **MIDI port**: type a name (partial match is fine, e.g. `svg`) or pick one. Pick a **Channel** (1–16).
- **Stream everything to MIDI** plays the whole song and follows along row by row.
  **Export MIDI file…** writes a format-0 `.mid`.
- **Tuning** is auto-detected from the string count (4 = bass EADG, 6 = guitar EADGBE).
  Use **Transpose** to shift the octave if your instrument expects it.

## Strummed guitar
Strum arrows are vertical arrows just left of a chord. Each arrow covers the strings it
strums. The reader finds them automatically on any tab staff, and the chord's notes are then
spread out in the arrow's direction:

- **Arrowhead at the top** (pointing toward the high strings): low strings sound first. This is a downstroke.
- **Arrowhead at the bottom**: high strings sound first. This is an upstroke, and it plays 10 velocity
  softer because upstrokes sound lighter.
- **Strum spread** (UI, default 12 ms per string) sets the gap between strings. Every note of the
  chord still ends together, so ties and rhythm are unaffected. Set it to 0 for block chords.
- `X` is a muted / dead strum. It plays as a very short, quiet click on each string.
- Chords without an arrow play all at once.

An arrow that spans the whole staff looks like a bar line. It's told apart by its arrowhead,
which pokes out past the staff on both sides of the shaft. Ties crossing a bar line never do.

## Drums (`tabreader/drums.py`)
Drum sheets are detected automatically: a 5-line staff whose glyphs are noteheads, not digits.
Each hit is identified by **notehead shape + staff position**. The position is counted in
half-spaces from the top line: top line = 0, spaces are odd, negative numbers are above the staff.

| Key | Looks like | Default (General MIDI) |
|---|---|---|
| `x@-2` | × on a ledger line above | 49 Crash Cymbal 1 |
| `x@-1` / `circle_slash@-1` / `circle_x@-1` | × / ⊘ / ⊗ above staff | 42 Closed / 46 Open Hi-Hat |
| `x@0` | × on top line | 51 Ride Cymbal 1 |
| `filled@3` / `x@3` | ● / × in space 2–3 | 38 Snare / 37 Side Stick |
| `triangle@3` | ▲ in space 2–3 | 54 Tambourine |
| `filled@1`, `@2`, `@4`, `@5`, `@6` | ● toms, high to low | 50, 48, 47, 45, 43 |
| `filled@7` | ● in space 4–5 | 36 Bass Drum 1 |
| `x@9` | × below the staff | 44 Pedal Hi-Hat |

- The **Drum map** panel in the UI lists every kit piece found in the image, with a crop of the
  actual notehead, its hit count and a MIDI note picker. ▶ auditions the sound. Changes apply
  immediately and are saved to `drum_map.json`, which only stores your overrides.
- `(×)` and `(●)` are ghost notes and play at velocity 52. A `>` accent plays at 124; normal hits play at 100.
- Drum sheets default to **MIDI channel 10**.
- Triplet brackets (`⌐3⌐`) scale the stems under them by 2/3.

## How the reading works (`tabreader/vision.py`)
All measurements are relative to the string spacing, so the zoom level doesn't matter.

| What | How |
|---|---|
| Rows | thin, evenly spaced horizontal lines (4–8) found with a horizontal morphological opening. Thicker bands are beams. Overlapping groups (tie arcs over chords) are rejected. |
| Bar lines | columns that are ink from the top string to the bottom one, with tall glyphs such as the time signature masked out. Double and thick bars are merged. |
| Bar numbers | small grey digits just above the top string, right of each bar line. Unreadable ones are inferred from sequence. Jumps in the numbering are kept and warned about. |
| Frets | pieces darker than the string lines that contain near-black pixels, which arcs and slides never do. They are grouped per string into numbers. Tall narrow pieces are parentheses (ties) and short digits are grace notes. |
| Rhythm | vertical stems under the staff. No stem = whole note, short stem = half, full stem = quarter. Each beam layer at the stem foot halves the value and a detached dot adds 50%. |
| Ties | a stem with no fret above it extends the previous note, and so does "(n)". |
| Tempo / time sig | digits after "=" above the first row, and the big stacked digits at the first row's start. |

Each bar's durations are checked against the time signature. If a bar doesn't add up, the
reader repairs it in this order:
1. A plain (unbeamed) stem is read as a half or whole note when that makes the bar exact.
2. Rests are inserted where the spacing between neighbouring notes leaves a gap.
3. As a last resort, durations are scaled to fit.

Repaired bars are listed in the row header ("inferred rests/lengths"). Scaled bars get a ⚠.

Digits are classified by nearest neighbour against `tabreader/glyph_templates.npz`, which was
harvested from the Creep tabs. To teach it a new font or zoom level:

```
python tools/harvest_glyphs.py sheet new_tab.png      # writes tools/clusters/clusters.png
python tools/harvest_glyphs.py label "0:3,1:5,2:x,..."  # cluster id:label, x = ignore
```

Regression check across everything in `tabs/`: `python tools/survey.py --overlays`
(overlays are written to `tools/clusters/`).

## Not handled yet
- Rest symbols aren't read directly. Their time is recovered from spacing (see repair step 2),
  which is why sheets with drawn rests show "inferred rests".
- Tremolo slashes (rolls) and flams play as single hits
- Repeats and alternate endings
- Bends and vibrato (bends play as the plain fret, and slides and hammer-ons as separate notes)
