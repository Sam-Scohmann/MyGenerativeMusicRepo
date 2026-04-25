

use_bpm 70

MIDI_NAME = "svg_midi_2"

use_midi_defaults port: MIDI_NAME, channel: 1
use_synth :piano




# Add this new live_loop for trigger control
##| live_loop :animation_trigger do
##|   # Send trigger note to Blender on channel 2
midi :c5, port: MIDI_NAME, channel: 2, vel: 100  # Trigger note
sleep 0.25
midi :c5, port: MIDI_NAME, channel: 2, vel: 0    # Note off
sleep 0.25  # Wait 2 seconds before song starts
#stop
##| end




live_loop :fur_elise_complete do

  # ========== SECTION A - Phrase 1 ==========
  notes_a1 = [:e5, :ds5, :e5, :ds5, :e5, :b4, :d5, :c5, :a4]
  dur_a1   = [0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.5]


  # ========== SECTION A - Phrase 2 ==========
  notes_a2 = [:c4, :e4, :a4, :b4]
  dur_a2   = [0.25, 0.25, 0.25, 0.5]

  # ========== SECTION A - Phrase 3 ==========
  notes_a3 = [:e4, :gs4, :b4, :c5]
  dur_a3   = [0.25, 0.25, 0.25, 0.5]


  # ========== SECTION A - Phrase 4 (repeat with correct ending) ==========
  notes_a4 = [:e5, :ds5, :e5, :ds5, :e5, :b4, :d5, :c5, :a4]
  dur_a4   = [0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.5]

  # ========== SECTION A - Phrase 5 ==========
  notes_a5 = [:c4, :e4, :a4, :b4]
  dur_a5   = [0.25, 0.25, 0.25, 0.5]

  # ========== SECTION A - Phrase 6 (ending fermata) ==========
  notes_a6 = [:e4, :c5, :b4, :a4]
  dur_a6   = [0.25, 0.25, 0.25, 1.0]



  # ========== SECTION A RETURNS (repeat main theme) ==========
  notes_a1.zip(dur_a1).each do |n, d|
    play n, release: 0.75, amp: [0.2,0.25,0.3,0.35].choose
    midi_note_on note(n)
    sleep d * 0.9
    midi_note_off note(n)
    sleep d * 0.1
  end

  notes_a2.zip(dur_a2).each do |n, d|
    play n, release: 0.75, amp: [0.2,0.25,0.3,0.35].choose
    midi_note_on note(n)
    sleep d * 0.9
    midi_note_off note(n)
    sleep d * 0.1
  end

  notes_a3.zip(dur_a3).each do |n, d|
    play n, release: 0.75, amp: [0.2,0.25,0.3,0.35].choose
    midi_note_on note(n)
    sleep d * 0.9
    midi_note_off note(n)
    sleep d * 0.1
  end

  notes_a4.zip(dur_a4).each do |n, d|
    play n, release: 0.75, amp: [0.2,0.25,0.3,0.35].choose
    midi_note_on note(n)
    sleep d * 0.9
    midi_note_off note(n)
    sleep d * 0.1
  end

  notes_a5.zip(dur_a5).each do |n, d|
    play n, release: 0.75, amp: [0.2,0.25,0.3,0.35].choose
    midi_note_on note(n)
    sleep d * 0.9
    midi_note_off note(n)
    sleep d * 0.1
  end

  notes_a6.zip(dur_a6).each do |n, d|
    play n, release: d * 0.8, amp: [0.2,0.25,0.3,0.35].choose
    midi_note_on note(n)
    sleep d * 0.9
    midi_note_off note(n)
    sleep d * 0.1
  end

  # Final chord
  play_chord [:a3, :c4, :e4, :a4], release: 2, amp: 0.7
  [:a3, :c4, :e4, :a4].each do |n|
    midi_note_on note(n)
  end
  sleep 2.8
  [:a3, :c4, :e4, :a4].each do |n|
    midi_note_off note(n)
  end
  sleep 0.2

  stop
end