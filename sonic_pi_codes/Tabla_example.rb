# Welcome to Sonic Pi

use_bpm 120

# =========================
# HUMANIZATION HELPERS
# =========================
define :vel do |base, var=0.2|
  return base + rrand(-var, var)
end

define :timing do |t|
  sleep t + rrand(-0.01, 0.01)
end

# =========================
# BOL DEFINITIONS
# =========================
define :dha do |accent=1|
  sample :tabla_ghe1, amp: vel(1.2 * accent, 0.15)
  sample :tabla_na, amp: vel(1.0 * accent, 0.15)
end

define :dhin do |accent=1|
  sample :tabla_ghe1, amp: vel(1.0 * accent, 0.1)
  sample :tabla_tun1, amp: vel(0.9 * accent, 0.1)
end

define :tin do |accent=1|
  sample :tabla_na_o, amp: vel(0.8 * accent, 0.1)
end

define :ta do |accent=1|
  sample :tabla_te1, amp: vel(0.75 * accent, 0.1)
end

define :ge do |accent=1|
  sample :tabla_ghe2, amp: vel(0.8 * accent, 0.1)
end

define :ka do |accent=1|
  sample :tabla_ke1, amp: vel(0.7 * accent, 0.1)
end

# =========================
# MAIN PERFORMANCE
# =========================
live_loop :drut_teentaal_dynamic do

  # =========================
  # THEKA (accented structure)
  # =========================
  dha(1.5); timing 0.5   # SAM (strong)
  dhin(1.0); timing 0.5
  dhin(0.9); timing 0.5
  dha(1.2); timing 0.5

  dha(1.1); timing 0.5
  dhin(1.0); timing 0.5
  dhin(0.9); timing 0.5
  dha(1.2); timing 0.5

  # Khali section (lighter)
  tin(0.7); timing 0.5
  tin(0.6); timing 0.5
  tin(0.7); timing 0.5
  ta(0.8); timing 0.5

  ta(0.9); timing 0.5
  dhin(1.0); timing 0.5
  dhin(1.0); timing 0.5
  dha(1.3); timing 0.5

  # =========================
  # KAIDA (dynamic phrasing)
  # =========================
  2.times do
    dha(1.2); ge(0.8); dhin(1.0); ge(0.7)
    timing 0.25
    tin(0.7); ka(0.6); ta(0.8); ka(0.6)
    timing 0.25

    dhin(1.0); ge(0.7); dhin(0.9); ge(0.7)
    timing 0.25
    dha(1.2); ge(0.8); ta(0.9); ka(0.6)
    timing 0.25
  end

  # =========================
  # PALTA (contrast accents)
  # =========================
  2.times do
    tin(0.7); dhin(1.0); dha(1.2); ge(0.7)
    timing 0.25
    dhin(0.9); tin(0.7); dha(1.2); ge(0.7)
    timing 0.25

    dha(1.3); tin(0.8); dhin(1.0); ge(0.7)
    timing 0.25
    ge(0.6); dha(1.3); dhin(1.0); tin(0.7)
    timing 0.25
  end

  # =========================
  # RELA (ghost + accent flow)
  # =========================
  4.times do
    sample :tabla_te_ne, amp: vel(0.6, 0.1)
    sleep 0.125
    sample :tabla_te_ne, amp: vel(0.5, 0.1)
    sleep 0.125
    sample :tabla_ke1, amp: vel(0.4, 0.1)
    sleep 0.125
    sample :tabla_te_ne, amp: vel(0.7, 0.1)  # slight accent
    sleep 0.125
  end

  # =========================
  # TIHAI (clear resolution)
  # =========================
  3.times do
    dha(1.4); dhin(1.1); dha(1.4)
    timing 0.5
  end

  sleep 1
end