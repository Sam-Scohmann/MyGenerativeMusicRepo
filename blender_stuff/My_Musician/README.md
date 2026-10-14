# My Musician (Blender add-on)

Instrument rigs played from MIDI. Pick the instrument at the top of
**3D View ▸ Sidebar (N) ▸ My Musician** (saved per scene):

- **Piano** – plays the `PIANO KEYS.blend` piano, keys *and* the two rigged
  hands, live from MIDI (e.g. Sonic Pi `midi_note_on`); records, re-animates,
  adds sound and exports MIDI. Also bundles the piano-building scripts.
- **Guitar** – string start/end empties, fret measurement and hooked string
  meshes (the former *Guitar String Animator* / `GuitarSetup.py`).

## Install
Blender ▸ Edit ▸ Preferences ▸ Add-ons ▸ ▾ ▸ **Install from Disk…** ▸ `My_Musician.zip`.
Remove *MIDI Piano Hands* and *Guitar String Animator* first: My Musician
replaces both (same operators and scene data, so existing .blend files keep
their settings).

On Windows, MIDI input uses the built-in Windows MIDI API: nothing to install.
Elsewhere it falls back to `mido`; if the panel says *mido not installed*,
press **Install MIDI Support**.

---

## Piano

### Play
- **MIDI Input** lists the inputs connected right now (↻ rescans). Pick the one
  Sonic Pi sends to; it's saved in the .blend and can be switched while live.
- **Start Live** / **Stop**. **Play Test Piece** runs a built-in sequence
  through the same path, handy without Sonic Pi.
- **Record Keyframes** bakes keys and hand controls from the current frame.
  While live, earlier takes are muted so they don't fight the live input.

Note mapping: property octave = `note // 12 - Octave Offset` (default 3: Sonic
Pi `:c2` → `C_0`, `:c4` → `C_2`). The 48-key piano covers `:c2`–`:b5`.

### Rest pose
- **Live:** Start never changes the hands. The pose they're in when you press
  Start is their rest pose: they play from it, drift back to it when idle,
  and Stop puts them back in it. A recording ends with a key of that pose.
- **Re-animate Hands** plans offline from the hands' **final keyframe** (the
  last key on any hand/finger control); with no keys, the current pose.
  **Hands ▸ Set Rest Pose** keys the current pose as that final keyframe (at
  the scene's end frame, or replacing a later final key). **Go to Rest Pose**
  puts the hands there.

### How the hands move
- **`hand_control`** points the hand: it moves sideways along the keyboard
  (and slides forward, horizontally, for black keys). It never changes height.
- **`wrist`** is the only thing that moves up and down: a damped average of
  how far the fingers are up or down (**Wrist Follow**), plus a slow random
  **Wrist Noise**. It dips at most **Max Wrist Dip** below its rest, and never
  below `hand_control`.
- **Fingers** snap to their keys through their IK controls, with an arc when
  they travel. Each target is lifted by that finger's **fingertip pad** (how
  far its skin reaches below the IK tip, measured on the hand mesh), so pads
  rest on the keys instead of sinking in.
- **Reach:** on Start / Re-animate each finger's IK is tested on the rig to
  find how far down it can really reach. Fingering avoids giving a finger a
  key below that; when no other finger can take it, the wrist dips up to
  **Extra Dip for Reach** more.
- **Wrist extras:** **Max Wrist Lift** caps how high it rises; **Jump Lift**
  arcs the wrist up while the hand travels further than **Jump Threshold**
  (white keys); **Wrist Inertia** adds lag and overshoot, never more than
  **Max Inertia Offset** from its target.
- **Idle Finger Sway:** idle fingers dangle sideways when the hand moves
  (a damped spring on each idle finger's IK control, driven by the hand's
  acceleration), within the finger bones' IK Z limits.
- **Toward the back of the piano:** *Black Key Reach-in* slides the hand in
  for black keys; separately, when a pressed key is beyond a finger's wrist
  *Limit Distance* (mostly thumb and pinkie), the hand slides back
  automatically until it fits. Higher *White/Black Touch* values put keys
  further back and increase that.
- **Seeking keys, no stretching:** a finger reaches a key by turning sideways
  at its knuckle, up to **Seek Angle** (thumb and pinkie swing forward over
  the keys this way), within its IK chain's reach and the rig's wrist Limit
  Distance. It can't turn into a pressed neighbour (**Min Finger Gap** between
  their middle segments); idle neighbours turn aside to make room. Beyond
  that the hand moves along the keyboard, or slides towards the back of the
  piano, at most **Max Reach-in**; fingers needing more fall short. Held
  notes that can't come along have their fingers lift early (keys keep sounding).
- **Which finger plays:** the finger nearest the key, not the same finger as
  the previous different key, and - when the hand has to move - landing on
  the lead finger: live, the index (right hand) or pinkie (left hand);
  Re-animate, the middle finger, then the next finger over on the side the
  melody moves.
- **Min Hand Distance** (default 5 white keys between the two `hand_control`
  centres): not a split - either hand can play anywhere - but an idle hand
  moves aside (also when returning home), splitting chords and fingering
  avoid bringing two busy hands closer, and Re-animate reports any frames
  where both hands' notes force them closer.
- **Hand Lift (world Z):** `hand_control` rises along world Z from several
  sources added together, capped by **Max Hand Lift**: an arc on every
  sideways move (**Move Lift** per key travelled, pressed fingers stay put),
  **Hand Jump Height** on moves longer than **Hand Jump Threshold** (only while
  that hand holds no key), **Lift per Reach-in** while sliding towards the back
  of the piano, **Play Lift** following how busy the fingers are, and a slow
  **Hand Noise**. Wherever a pressing finger couldn't reach its key from the
  raised hand, the lift is cut back.

### Re-animate hands from a take
**Recording & Sound ▸ Re-animate Hands from Take** replans the hands with the
whole performance known: which hand plays each note, fingering with lookahead
(spread, hand moves that would cut held notes short, fast jumps, re-using a
finger too soon, crossings, thumbs on black keys), hands arriving before the
notes, and fingers hovering, striking on the note-on and lifting on release.
It replaces the take's hand keyframes over the take (keys and sound are
untouched; Ctrl+Z undoes it). Timing options are in the redo panel. The report
says how many chord notes were wider than a hand (left unfingered) and how
many held notes' fingers lifted early.

### Sound, MIDI export, presets
- **Build Piano Sound** (automatic after recording): synthesises the take into
  `//piano_audio/<scene>_piano.wav` as a *Piano Take* strip in the Video
  Sequencer, so it plays with the timeline and renders with the video.
- **Export MIDI File…**: the recorded take (frame-accurate) or the live session
  (exact timing, kept until Blender closes) as a `.mid`.
- **Hands ▸ Import / Export** saves all hand settings as JSON; ↺ resets them.
  Older preset files' *reach* / *stay_reach* are skipped (replaced by Finger
  Spread).

### How the hands are found
Bone names `hand_control_{l,r}`, `wrist_{l,r}` and
`thumb/index/mid/ring_f/pinkie_control_{l,r}`; the hand lower on the keyboard
is the bass hand, whatever its suffix. Fingertip targets come from each key's
`<key>Guide` curve (Touch sliders = how far along it).

### Build tools (Build Piano panel)
1. **Prepare Base Octave** – 12 keys selected: align pivots, lock, name C…B, rotation limits.
2. **Duplicate Octaves** – copy those 12 keys N times into *Piano Keys*.
3. **Build Key Drivers** – keys + main object (active): `C_0…` props driving each key.
   **Check / Repair Drivers** checks every key reads the property for its
   position on the keyboard and fixes wrong mappings and broken drivers.
4. **Glowing Keys** – drive Mix node factors from `note_on`.
5. **Build Finger Guides** – active curve drawn over the keys → per-key guide curves.

---

## Guitar

### Live guitar (MIDI Guitar Live panel)
- **Hands:** in `HandsArmature`, the `hand_control` nearer the bridge strums
  with the pick (any mesh parented to one of its bones, e.g. *PluckTrum* on
  `thumb_control_l`); the other hand frets: `hand_control_r` follows the neck
  to the current position and its finger IK controls press the fret points.
  Both hands keep their pose relative to the neck, so a guitar animated at its
  root (`GUITAR_ROOT`) carries them along.
- **Fretting arm:** `elbow_controller` of the fretting side travels with the
  hand along the neck (**Elbow Follow**). The fretting `hand_control` drops
  (**Hand Drop**, world down) and comes out from the guitar's front (**Hand
  Forward**) in proportion to how low - physically, in world height - the
  fretted strings are, smoothed by **Low String Lag**; pressing fingertips
  stay on their frets.
- **MIDI Input**, Channel, and **Channel = String** (MIDI channel 1-6 picks the
  string; otherwise the add-on chooses).
- **Modes:** *Auto* (chords strummed, single notes picked), *Melody* (every note
  picked, alternate picking), *Chord* (notes starting within the Chord Window
  are one strum; quick repeats alternate down/up), *Pattern* (held notes are
  fretted as a chord and strummed by the **Strum Pattern** at the **Tempo**:
  `D` down, `U` up (top strings), `X` muted down, `-` the hand swings past
  the strings).
- **Fingering:** the fretting hand sits in a position (index over fret p, one
  finger per fret, one-fret stretches). Each note group is voiced on distinct
  strings, preferring to stay in position, shifting by the shortest move,
  open strings low on the neck, compact chords, few string jumps; several
  notes on the index finger's fret make a barre. For a barre the index finger's IK
  target sits **Barre Overhang** (1 string spacing) past the edge string away
  from the palm, so the finger lies straight across the fret instead of
  curling. Positions where two notes would need the same finger are avoided,
  so barre shapes (E-form, A-form) come out with one finger per note.
  Chords need no names: whatever notes arrive together (within the Chord
  Window) are voiced and strummed as one. When no hand shape holds them all,
  **Octave Far Notes** plays the fewest notes at their nearest octave (above
  or below), keeping the top melody note where it is if possible; only if
  that fails is the largest part that fits played, keeping the top (melody)
  and bottom (bass) notes first. The panel counts moved and left-out notes. **Fold Into Range**
  moves notes above or below the guitar by octaves instead of dropping them.
- **Strings:** a fretted string is pushed towards the fretboard at its fret
  (`FRET_f_STRING_s_CTRL`); a plucked string rings and decays on its
  `VIBRATE_*` controllers, damped on release.
- **Play Guitar Sound Live** plays the synthesised guitar in Blender (handy in
  Pattern mode). **Play Test Piece** strums a I-V-vi-IV progression and picks
  a C major scale without Sonic Pi.

### Recording, Re-animate, sound, MIDI
- **Record** keeps the note stream and every pluck (string, fret, direction,
  time) with the scene, and keys the hands and the strings that move. A take
  ends with a key of the hands' rest pose.
- **Re-animate from Take** re-fingers the whole take with lookahead (positions
  and voicings planned over the whole take), then replays the recorded plucks
  and strums on the frame clock: the fretting hand is in place before each
  note and the pick approaches before each hit. It replaces the take's hand
  and string keys.
- **Build Guitar Sound** (automatic after recording): plucked-string synthesis
  of the take into `//guitar_audio/<file>_<scene>_guitar.wav` as a *Guitar
  Take* strip in the Video Sequencer.
- **Export MIDI File...**: the plucks as heard, optionally one channel per string.

### String setup (Guitar String Setup panel)
1. Set the number of strings and tunings (e.g. `E2,A2,D3,G3,B3,E4`).
2. **Create Start/End Empties** and place them (they may be parented to the guitar).
3. **Start Recording**, then click the start and end of each fret on the
   guitar in the 3D view (points snap to the surface under the mouse, else to
   the 3D cursor's depth; right-click cancels a point; Esc finishes).
4. **Create Strings**: one edge-chain mesh per string, every vertex hooked to
   a controller empty (`FRET_n_STRING_s_CTRL` with `note`, `fret_number`,
   `string_number`; `VIBRATE_n_STRING_s_CTRL` for the section past the last fret).
- **Update Fret Positions** keeps each controller at the same proportion along
  its string after the start/end empties move.
- **Update Note Names** re-labels controllers after a tuning change (the live
  guitar reads its tuning from these names).
- Measurements are saved with the .blend; **Save / Load** also writes them,
  with the tunings, to a JSON file.
