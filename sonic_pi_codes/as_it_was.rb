use_bpm
86
step_duration = 0.25

define: play_my_pattern
do | pattern, sample_name, amp: 1, on: true, swing: 0 |
step_index = 0

pattern.each_char
do | c |
swing_offset = (step_index % 2 == 1) ? swing: 0

sleep
swing_offset

if c == "X" and on
    sample
    sample_name, amp: amp
end

sleep
step_duration - swing_offset

step_index += 1
end
end

# -------- LOOP GENERATOR --------

define: drum_loop
do | name, pattern, sample_name, amp: 1, on: true, swing: 0, sync_to: nil |
live_loop
name
do
# sync sync_to if sync_to
play_my_pattern
pattern, sample_name, amp: amp, on: on, swing: swing
end
end

bass = "X_X_X_X_X_X_X_X_"
snare = "__X___X___X__XX_"
cymbals = "X__XX__XX__X___X"
cymbals2 = "_X_X_X_X_X_X_XXX"
crash = "_______X___X_XX"

drum_loop: bass_loop, bass,: bd_haus, amp: 1.5, on: true
drum_loop: snare_loop, snare,: sn_dolf, on: true
drum_loop: crash_loop, crash,: elec_cymbal, swing: 0, amp: 0.15, on: true
drum_loop: cymbals_loop, cymbals2,: elec_flip, swing: 0, amp: 2, on: true

# ------------- CHORDS LOOPS ---------------

chord_progression = [
    [:A2,: major],
[: Fs2,:minor],
[: D2,:major],
[: E2,:major]
]

live_loop: my_chordprogression_loop
do
root, quality = chord_progression.tick

with_fx: tremolo, amp: 1.3, wave: 3, phase: 1, mix: 1
do
# with_fx :slicer, amp: 2, mix: 1 do #
with_fx: echo
do
use_synth: hollow
use_synth_defaults
sustain: 1.9
# play chord(root, quality, num_octaves: 2).tick, amp: 5
if 0 == 0
    play_chord
    chord(root, quality, num_octaves: 2), amp: 3
end
end
# end
end

sleep
2
end

# ------------- INTRO LOOPS ---------------

define: riff
do | is_playing = 1 |
if is_playing == 1
    use_synth: chiplead  #:rhodey 
    use_synth_defaults  # sustain: 0.8, decay: 0.3, attack: 0.1, release: 0.3, cutoff: 10
    # sleep 0.25
    # Bar 1
    play: A4, release: 0.1;
    sleep
    0.25
    play: B4, release: 0.1;
    sleep
    0.25
    play: Cs5, release: 0.1;
    sleep
    0.25

    4.
    times
    {play: B4, release: 0.1;
    sleep
    0.5}
    play: A4, release: 0.1;
    sleep
    0.25
    2.
    times
    {play: E5, release: 0.1;
    sleep
    0.25}
    play: Cs5, release: 0.1;
    sleep
    0.25
    sleep
    0.25
    # Bar 2 
    # sleep 0.25
    play: A4, release: 0.1;
    sleep
    0.25
    play: B4, release: 0.1;
    sleep
    0.25
    play: Cs5, release: 0.1;
    sleep
    0.25
    4.
    times
    {play: B4, release: 0.1;
    sleep
    0.5}
    play: A4, release: 0.1;
    sleep
    0.25
    play: B4, release: 0.1;
    sleep
    0.5
    play: A4, release: 0.1;
    sleep
    0.25
    sleep
    0.25
else
    sleep
    8
end
end

live_loop: playing_riff
do
with_fx: bitcrusher, amp: 0.75
do
with_fx: reverb, mix: 0.7, room: 1
do
riff
1
end
end
end

# ------------- BELLS LOOPS ---------------

# Define your pattern
my_pattern = {
    bar1: {notes: [:A4,: Gs4,:Fs4,: E4], interval: 2},
bar2: {notes: [:A4,: Gs4,:Fs4,: E4], interval: 2},
bar3: {notes: [:A4,: Gs4,:Fs4,: E4], interval: 2},
bar4: {notes: [:Fs4], interval: 8},
bar5: {notes: [:A4,: Gs4,:Fs4,: E4], interval: 2},
bar6: {notes: [:A4,: Gs4,:Fs4,: E4], interval: 2},
bar7: {notes: [:Fs4], interval: 8}
}

define: play_my_pattern2
do | my_pattern, synth_name = nil, sample_name = nil, on = true, reverb = false, tremolo = false, distortion = false |
if on == true
    sleep
    4
    # Apply effects using blocks
    fx_chain = ->(code)
    {
    if distortion
    with_fx(: distortion, distort: 0.5) do
    code.call
    end
    else
    code.call
    end
    }

    fx_chain = ->(code)
    {
    if tremolo
    with_fx(: tremolo, depth: 0.5) do
    code.call
    end
    else
    code.call
    end
    }

    fx_chain = ->(code)
    {
    if reverb
    with_fx(: reverb, mix: 0.9, room: 1) do
    code.call
    end
    else
    code.call
    end
    }

    ##| # Play sample if provided
    ##| if sample_name
    ##|   in_thread do
    ##|     sample sample_name
    ##|   end
    ##| end

    # Play the pattern
    my_pattern.each
    do | bar, data |
    if synth_name
        use_synth
        synth_name
        use_synth_defaults  # relase: 0.8, attack: 0.1
    end

    data[:notes].each
    do | note |
    play
    note, amp: 0.5
    # end
    sleep
    data[:interval]
end
end
else
sleep
8
end
end

# Call it
play_my_pattern2(my_pattern,: pretty_bell,:bd_haus, true, true, true, false)