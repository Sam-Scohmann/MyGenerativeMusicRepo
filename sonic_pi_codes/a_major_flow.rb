# A Major Flow - an original piece for the Blender piano rig
# Flowing left-hand arpeggios under a lyrical right-hand melody.
# Plays through Sonic Pi's piano and sends every note to MIDI so the
# Blender add-on presses the keys and animates both hands.
# Range used: :fs2 .. :b5 (the rig covers :c2 .. :b5)

use_bpm 66

MIDI_PORT = "midi_2"
MIDI_CH = 1

use_synth :piano

# -----------------------------
# CHORDS: root (low octave) and quality
# -----------------------------
CHORDS = {
  A:   [:a2,  :major],
  Csm: [:cs3, :minor],
  D:   [:d3,  :major],
  E:   [:e2,  :major],
  Fsm: [:fs2, :minor],
  Bm:  [:b2,  :minor]
}

# root, fifth, octave, tenth, twelfth and back down, in eighth notes
ARP_MAJOR = [0, 7, 12, 16, 19, 16, 12, 7]
ARP_MINOR = [0, 7, 12, 15, 19, 15, 12, 7]

# -----------------------------
# ONE KEY: sound + MIDI (note off is sent automatically after sustain)
# -----------------------------
define :key do |n, dur, vel|
  unless n == :r
    vel = [[vel, 1].max, 127].min
    play n, amp: vel / 127.0, vel: vel / 127.0 * 0.6, release: dur + 0.6
    midi n, sustain: dur * 0.95, vel_f: vel / 127.0, port: MIDI_PORT, channel: MIDI_CH
  end
end

define :left_hand do |chords, vel|
  chords.each do |name|
    root, quality = CHORDS[name]
    pattern = quality == :major ? ARP_MAJOR : ARP_MINOR
    pattern.each_with_index do |step, i|
      accent = i == 0 ? 12 : 0
      key note(root) + step, 0.5, vel + accent + rrand_i(-4, 4)
      sleep 0.5
    end
  end
end

define :right_hand do |melody, vel|
  melody.each do |n, d|
    key n, d, vel + rrand_i(-6, 6)
    sleep d
  end
end

# both hands together; every chord lasts one 4-beat bar
define :section do |chords, melody, rh_vel, lh_vel|
  beats = melody.map { |_, d| d }.inject(:+)
  raise "melody has #{beats} beats, chords need #{chords.length * 4}" unless beats == chords.length * 4
  in_thread do
    left_hand chords, lh_vel
  end
  right_hand melody, rh_vel
end

# -----------------------------
# MATERIAL
# -----------------------------
THEME_CHORDS = [:A, :Csm, :D, :E, :Fsm, :Bm, :E, :A]
THEME = [
  [:e5, 1.5], [:cs5, 0.5], [:b4, 1],  [:a4, 1],     # A
  [:gs4, 1.5], [:a4, 0.5], [:b4, 1],  [:cs5, 1],    # C#m
  [:d5, 1.5], [:cs5, 0.5], [:b4, 1],  [:fs4, 1],    # D (b4: the left hand plays a4 here)
  [:e4, 1],   [:gs4, 1],   [:b4, 2],                # E
  [:cs5, 1.5], [:a4, 0.5], [:fs5, 1], [:e5, 1],     # F#m
  [:d5, 1.5], [:cs5, 0.5], [:b4, 1],  [:fs4, 1],    # Bm
  [:gs4, 1],  [:b4, 1],    [:e5, 1],  [:d5, 1],     # E
  [:cs5, 3],  [:r, 1]                               # A
]

MIDDLE_CHORDS = [:D, :E, :Csm, :Fsm, :Bm, :E, :A, :E]
MIDDLE = [
  [:fs5, 0.5], [:e5, 0.5], [:d5, 0.5], [:a4, 0.5], [:fs5, 1], [:e5, 1],    # D
  [:e5, 0.5], [:gs4, 0.5], [:b4, 0.5], [:e5, 0.5], [:d5, 1], [:b4, 1],     # E
  [:e5, 0.5], [:cs5, 0.5], [:gs4, 0.5], [:cs5, 0.5], [:e5, 1], [:gs5, 1],  # C#m
  [:a5, 1.5], [:gs5, 0.5], [:fs5, 1], [:cs5, 1],                           # F#m
  [:d5, 0.5], [:fs5, 0.5], [:b5, 1], [:a5, 0.5], [:fs5, 0.5], [:d5, 1],    # Bm
  [:e5, 0.5], [:gs5, 0.5], [:b5, 1], [:gs5, 0.5], [:e5, 0.5], [:b4, 1],    # E
  [:cs5, 1],  [:e5, 1],    [:a5, 2],                                       # A
  [:gs5, 0.5], [:e5, 0.5], [:d5, 0.5], [:b4, 0.5], [:gs4, 1], [:e4, 1]     # E (turn back)
]

ENDING_CHORDS = [:D, :E]
ENDING = [
  [:fs5, 1], [:e5, 1], [:d5, 1], [:cs5, 1],         # D
  [:b4, 2],  [:e5, 2]                               # E
]

# -----------------------------
# PERFORMANCE (plays once)
# -----------------------------
with_fx :reverb, room: 0.7, mix: 0.3 do
  left_hand [:A, :A], 60                     # intro: left hand alone
  section THEME_CHORDS, THEME, 80, 60
  section MIDDLE_CHORDS, MIDDLE, 92, 68
  section THEME_CHORDS, THEME, 76, 58
  section ENDING_CHORDS, ENDING, 70, 56

  # final chord, both hands
  [:a2, :e3, :a3].each { |n| key n, 4, 64 }
  [:a4, :cs5, :e5].each { |n| key n, 4, 72 }
  sleep 4
end
