use_bpm 73
step_duration = 0.25  # base 16th note (4/4)

define :play_my_pattern do |pattern, sample_name, amp: [1], on: true, swing: 0|
  step_index = 0
  pattern.each_char do |c|
    swing_offset = (step_index % 2 == 1) ? swing : 0

    sleep swing_offset

    if c == "X" and on
      sample sample_name, amp: amp.tick
    end

    sleep step_duration - swing_offset

    step_index += 1
  end
end

#------------- DRUM LOOPS ---------------

bass  =  "X___X___X___X___"  #"_X_X_X_X_X_X_X_X"
snare =  "__X___X___X___X_"  #"___X___X___X__XX"
cymbals =  "XX__XX__XX__X___"
cymbals2 = "X_X_X_X_X_X_X_X_"
crash =    "XX___X___X___X__"


live_loop :bass_loop do
  play_my_pattern bass, :bd_haus, amp: [1], swing: 0.00 #, on: false
end

live_loop :snare_loop do
  with_fx :echo ,phase: 0.16666, decay: 2 do
  play_my_pattern snare, :sn_dolf, amp: [0.5] ,swing: -0.15, on: false
    end
end

live_loop :crash_loop do
  play_my_pattern crash, :drum_cymbal_open, amp: [0.15], on: true
end

live_loop :cymbals_loop do
  play_my_pattern cymbals2,:drum_cymbal_closed, amp: [0.75],swing: 0.00,on: true
end

#------------- CHORDS LOOPS ---------------
##| chords = ring(
##|   [:a2, :minor, 4], [:d2, :minor, 4], [:f2, :major, 4], [:g2, :major, 4],
##| ##|   [:e2, :major, 4], [:f2, :major, 4], [:g2, :major, 4], [:e2, :major, 4],
##| ##|   [:g2, :major, 4], [:f2, :major, 4], [:g2, :major, 4], [:e2, :major, 4],
##| ##|   [:f2, :major, 2], [:g2, :major, 4], [:e2, :major, 2], [:f2, :major, 2],
##| )

chords = ring(
  [:f4, :major, 4], [:e4, :major, 4],

  [:c3, :major, 4], [:a3, :minor, 4], [:e3, :major, 4], [:f3, :major, 4],
  [:c3, :major, 4], [:a2, :minor, 4], [:e3, :major, 4], [:f3, :major, 4],

  [:f4, :major, 4], [:e4, :major, 4],
  [:f3, :major, 4], [:f3, :major, 4],[:e3, :major, 4], [:c3, :major, 4],

  [:g2, :major, 4], [:c3, :major, 4],[:g2, :major, 4], [:a3, :minor, 4],
  [:g3, :major, 4], [:g2, :major, 4],[:e2, :major, 4], [:g3, :major, 3],[:c3, :major, 1],

  [:f3, :major, 4], [:e3, :major, 4],
  [:f3, :major, 4], [:f3, :major, 4],[:e3, :major, 4], [:c3, :major, 4],

  [:c3, :major, 4], [:a3, :minor, 4], [:e3, :major, 4], [:g3, :major, 4],
  [:c3, :major, 4], [:a3, :minor, 4], [:e3, :major, 4], [:g3, :major, 3],[:c3, :major, 1],
)

OFFSET = -3
MY_MIDI_PORT = 'svg_midi_2'

live_loop :my_chordprogression_loop do
  note, chord_type, interval = chords.tick(:chord_tick)
  puts note, chord_type, interval
  my_notes = chord(note, chord_type, invert: [0,1,2].tick ) + OFFSET
  with_fx :tremolo, amp: 1.3, wave: 3, phase: 1 do
    use_synth :hollow
    use_synth_defaults sustain: 3.9
    play_chord my_notes , amp: 5
  end
  my_notes.each do |note|
    midi_note_on note, channel: 1, port: MY_MIDI_PORT
    end
  sleep interval
  my_notes.each do |note|
    midi_note_off note, channel: 1, port: MY_MIDI_PORT
    end
end


#------------- BASS ARPEGGIO LOOP ---------------
live_loop :my_bass_loop do
  note, chord_type, interval = chords.tick(:chord_tick)  # Same tick name!
  intr = interval/8

  riff_choice = 1
  if riff_choice == 0
    with_fx :tremolo, amp: 1.3, wave: 3, phase: 1 do
      use_synth :bass_foundation
      use_synth_defaults sustain: interval*0.1, decay: interval*0.8
      play chord(note, chord_type, invert: [0,1,2].tick )[1] + OFFSET , amp: 3
    end
    sleep interval
  elsif riff_choice == 1
    with_fx :tremolo, amp: 1.3, wave: 3, phase: 1 do
      use_synth :bass_foundation
      use_synth_defaults
      chord_playing = chord(note, chord_type, invert: [0,1,2].choose() , num_octaves: 2)
      N = chord_playing.length
      (interval/0.25).to_i.times do
        play chord_playing[rand(N)] + OFFSET , amp: 1 # [0, 0.35, 0.5, 0.7, 1].choose()
        sleep 0.25
      end
    end
  end
end




