use_bpm 94
use_synth :organ_tonewheel

# =========================
# HUMANIZATION
# =========================
define :vel do |base|
  base + rrand(-0.05, 0.05)
end

# =========================
# HARMONIUM NOTE
# =========================
define :harm_note do |n, dur, amp=0.8|
  with_fx :vibrato, rate: 5, depth: 0.15 do
    play n, sustain: dur, release: 0.3, amp: vel(amp)
  end
end

# =========================
# CHORD PLAYER
# =========================
define :play_chord_swell do |root, type, dur|
  notes = chord(root, type)

  notes.each do |n|
    harm_note n, dur, 0.7
  end
end

# =========================
# GRACE NOTE (KAN SWAR)
# =========================
define :grace do |from, to|
  play from, release: 0.1, amp: 0.4
  sleep 0.05
  play to, release: 0.3, amp: 0.6
end

# =========================
# LIGHT MELODY FILL
# =========================
define :melodic_fill do
  notes = scale(:g4, :major_pentatonic).shuffle.take(3)

  notes.each do |n|
    play n, release: 0.3, amp: 0.5
    sleep 0.25
  end
end

# =========================
# DRONE (IMPORTANT!)
# =========================
live_loop :drone do
  use_synth :organ_tonewheel
  play :g3, sustain: 6, amp: 0.4
  play :d4, sustain: 6, amp: 0.3
  sleep 6
end

# =========================
# MAIN HARMONIUM
# =========================
live_loop :harmonium do

  # G
  play_chord_swell :g3, :major, 3
  sleep 3

  # C
  play_chord_swell :c4, :major, 3
  sleep 3

  # Am
  play_chord_swell :a3, :minor, 3
  sleep 3

  # G
  play_chord_swell :g3, :major, 3
  sleep 3

  # =========================
  # EXPRESSIVE TOUCH
  # =========================
  if one_in(2)
    melodic_fill
  end

  if one_in(3)
    grace :fs4, :g4
  end

end




# TABLA TABLA TABLA

TABLA_TUNE = 3   # try values from 1 to 5



# =========================
# HUMANIZATION
# =========================
define :vel do |base, var=0.12|
  base + rrand(-var, var)
end

define :t do |dur|
  sleep dur + rrand(-0.01, 0.01)
end

# =========================
# MAIN BOLS
# =========================
define :dha do |a=1|
  sample :tabla_ghe1, amp: vel(1.1 * a) # bass, no pitch change
  sample :tabla_na, amp: vel(0.9 * a), rpitch: TABLA_TUNE
end

define :dhi do |a=1|
  sample :tabla_ghe1, amp: vel(0.85 * a)
  sample :tabla_tun1, amp: vel(0.75 * a), rpitch: TABLA_TUNE
end

define :na do |a=1|
  sample :tabla_na, amp: vel(0.75 * a), rpitch: TABLA_TUNE
end

define :ti do |a=1|
  sample :tabla_tas1, amp: vel(0.65 * a), rpitch: TABLA_TUNE
end

# =========================
# GHOST (SUBDIVISION)
# =========================
define :ghost_na do
  sample :tabla_na_o, amp: vel(0.35, 0.08), rpitch: TABLA_TUNE
end

define :ghost_te do
  sample :tabla_te_ne, amp: vel(0.3, 0.08), rpitch: TABLA_TUNE
end

define :ghost_ke do
  sample :tabla_ke1, amp: vel(0.3, 0.08)
end


# =========================
# FILL LIBRARY
# (ALL FILLS = 1 BEAT TOTAL)
# =========================

define :fill_laggi do
  2.times do
    sample :tabla_na_o, amp: vel(0.4)
    sleep 0.25
    sample :tabla_tas1, amp: vel(0.35)
    sleep 0.25
  end
end

define :fill_rela_light do
  4.times do
    sample :tabla_te_ne, amp: vel(0.3)
    sleep 0.25
  end
end

define :fill_phrase_end do
  sample :tabla_na_o, amp: vel(0.5)
  sleep 0.25
  sample :tabla_ke1, amp: vel(0.45)
  sleep 0.25
  sample :tabla_te1, amp: vel(0.5)
  sleep 0.5
end

define :fill_mini_tihai do
  3.times do
    sample :tabla_na_o, amp: vel(0.45)
    sleep 0.166
    sample :tabla_ke1, amp: vel(0.2)
    sleep 0.166
  end
end

define :fill_bass_touch do
  sample :tabla_ghe2, amp: vel(0.7)
  sleep 0.5
  sample :tabla_na, amp: vel(0.3)
  sleep 0.5
end

# =========================
# FILL SELECTOR
# =========================
define :play_fill do
  r = rrand(0, 1)
  print r
  if r < 0.1 #0.25
    fill_laggi
  elsif r < 0.3 #0.5
    fill_rela_light
  elsif r < 0.5 #0.7
    fill_phrase_end
  elsif r < 0.7 #0.9
    fill_mini_tihai
  else
    fill_bass_touch
  end
end

# =========================
# MAIN LOOP
# =========================
live_loop :dadra_real do

  sub = 0.5  # half-beat subdivision

  # === DADRA THEKA WITH SUBDIVISION ===

  # Dha
  dha(1.2); t sub
  ghost_na; t sub

  # Dhi
  dhi(1.0); t sub
  ghost_ke; t sub

  # Na
  na(0.9); t sub
  ghost_te; t sub

  # --- Khali ---
  na(0.7); t sub
  ghost_na; t sub

  ti(0.7); t sub
  ghost_ke; t sub

  na(0.8); t sub
  ghost_te; t sub

  # =========================
  # OCCASIONAL FILL
  # =========================
  if one_in(2)
    play_fill
  end

end