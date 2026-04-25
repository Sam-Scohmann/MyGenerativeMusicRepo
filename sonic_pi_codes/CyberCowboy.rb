use_bpm 58

set :bassta_active, 1
set :bells_active, 1
set :sliced_active, 1
set :rev_snare_active, 1
set :bass_0_active , 1

set :lead_68_active, 1
set :lead_88_active, 1


live_loop :bassta do
  #use_bpm 29
  #sample :bass_hit_c, amp: 2
  #sleep 1
  sample :loop_mika, rate: current_bpm/(60*2), amp: 2 if get(:bassta_active) == 1
  sleep 8
end

live_loop :bells_0 do
  #sleep
  #use_bpm 58
  use_synth :dull_bell
  play :e4 if get(:bells_active) == 1
  sleep 2
end

live_loop :bass_0 do
  #sleep
  #use_bpm 58
  with_fx :reverb, room: 1,mix:0.2 do
    with_fx :distortion, mix: 0.2, amp: 0.5 do
      use_synth :pluck
      if get(:bass_0_active) == 1
        play (scale , :e2,:minor_pentatonic).tick, cutoff: 10, pan: 0.13
      end
      sleep 0.5
    end
  end
end

live_loop :sliced_movements_0 do
  sync "/live_loop/bass_0"
  3.times do
    #use_bpm 58
    if get(:sliced_active) == 1
      use_synth :rodeo
      use_synth_defaults note: :e2 ,attack: 0.01, decay: 0.1, sustain: 0.1, amp: 0.3, pan: -1
      ns_0 = [:e2, :d3, :b3, :a3, :b3, :a3, :g3, :d2,  :e2]
      time_0 = [0.5, 0.5, 0.5, 0.5, 0.25,0.5, 0.5, 0.25 ,0.25]
      with_fx :slicer do
        play_pattern_timed ns_0, time_0
      end
    end
    sleep 0.25
  end
end

live_loop :rev_snare_0 do
  #use_bpm 58
  sync "/live_loop/bassta"
  sample :perc_bell2, rate: -1 if get(:rev_snare_active) == 1
  sleep 8
end


# ======================================
# ELECTRIC GUITAR LEADS – SIMPLIFIED & FIXED
# ======================================

# 6/8 LEAD (4 bars) – syncs to :bass_0
live_loop :guitar_lead_68 do
  sync :bass_0
  if get(:lead_68_active) == 1
    with_fx :distortion, mix: 0.5, gain: 1, distort: 0.3 do
    ##|   with_fx :phaser, phase: 0.25, depth: 0.7, mix: 0.4 do
      ##| with_fx :echo, decay: 2, mix: 0.3, phase: 0.25 do
    ##|       with_fx :reverb, room: 0.8, mix: 0.3 do
      use_synth :supersaw
      use_synth_defaults attack: 0.01, decay: 0.1, sustain: 0.2, release: 0.1, amp: 0.2
      notes_68 = [:e3, :g3, :a3, :b3, :a3, :g3, :e3, :g3, :a3, :c4, :b3, :a3, :g3, :a3, :b3, :d4, :b3, :g3, :e4, :d4, :b3, :a3, :g3, :e3]
      play_pattern_timed notes_68, [0.5] * 24
        ##|     end
      ##|   end
      ##| end
    end
  else
    sleep 12
  end
end

# 8/8 LEAD (1 bar) – simple sleep, no sync dependency
live_loop :guitar_lead_88 do
  if get(:lead_88_active) == 1
    with_fx :distortion, mix: 0.7, gain: 1, distort: 0.5 do
      ##| with_fx :phaser, phase: 0.25, depth: 0.7, mix: 0.4 do
        ##| with_fx :echo, decay: 2, mix: 0.3, phase: 0.25 do
    ##|       with_fx :reverb, room: 0.8, mix: 0.3 do
      use_synth :tb303
      use_synth_defaults attack: 0.01, decay: 0.1, sustain: 0.2, release: 0.05, amp: 0.12
      play_pattern_timed [:e3, :rest, :g3, :a3, :b3, :a3, :g3, :e3], [0.25] * 8 *2
      ##|     end
        ##| end
      end
    end
    sleep 4
  ##| else
    sleep 4
  ##| end
end