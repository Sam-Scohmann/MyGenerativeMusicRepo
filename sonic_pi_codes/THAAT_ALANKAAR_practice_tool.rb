use_bpm 60
use_synth :blade
use_synth_defaults amp: 5
OFFSET = -12 #24
RELEASE_RATIO = 0.99 #0.9
ATTACK_RATIO = 0.2

def midi_to_sargam(midi_note, scale_notes, anomalies=nil)
  sargam = ["Sa","Re","Ga","Ma","Pa","Dha","Ni"]
  anomaly_map = {}
  if anomalies
    anomalies.each do |swara, adjustment|
      case swara
      when :re; anomaly_map[1]=adjustment
      when :ga; anomaly_map[2]=adjustment
      when :ma; anomaly_map[3]=adjustment
      when :pa; anomaly_map[4]=adjustment
      when :dha; anomaly_map[5]=adjustment
      when :ni; anomaly_map[6]=adjustment
      end
    end
  end
  scale_notes.each_with_index do |scale_note, idx|
    if midi_note == scale_note
      if idx >= 7
        octave=(midi_note-scale_notes[0])/12
        return octave>0 ? "#{sargam[0]}'" : sargam[0]
      else
        note_name = sargam[idx]
        note_name="#{note_name}*" if anomaly_map[idx]==1
        note_name="*#{note_name}" if anomaly_map[idx]==-1
        return note_name
      end
    end
  end
  base_sa=scale_notes[0]
  octave_diff=((midi_note-base_sa)/12).round
  note_in_base_octave=midi_note-(octave_diff*12)
  scale_notes.each_with_index do |scale_note, idx|
    if idx<7 && note_in_base_octave==scale_note
      note_name=sargam[idx]
      note_name="#{note_name}*" if anomaly_map[idx]==1
      note_name="*#{note_name}" if anomaly_map[idx]==-1
      return octave_diff==0 ? note_name : (octave_diff>0 ? "#{note_name}'" : "#{note_name}.")
    end
  end
  scale_notes.each_with_index do |scale_note, idx|
    if idx<7 && (note_in_base_octave-scale_note).abs<=1
      note_name=sargam[idx]
      note_name="*#{note_name}" if (note_in_base_octave-scale_note)==-1
      note_name="#{note_name}*" if (note_in_base_octave-scale_note)==1
      return octave_diff==0 ? note_name : (octave_diff>0 ? "#{note_name}'" : "#{note_name}.")
    end
  end
  return "?"
end

def print_sargam(notes, scale_notes, anomalies=nil)
  sargam_string = notes.map{|midi|midi_to_sargam(midi,scale_notes,anomalies)}.join(" ")
  puts "  Sargam: #{sargam_string}"
end

def init_thaat(thaat_name)
  base_midi = [60,62,64,65,67,69,71,72].map{|m| m + OFFSET}
  anomalies = {
    kalyaan:{ma:+1},
    kaphi:{ga:-1,ni:-1},
    bhairav:{re:-1,dha:-1},
    bhairavi:{re:-1,ga:-1,dha:-1,ni:-1},
    punvi:{re:-1,dha:-1,ma:+1}
  }
  notes = base_midi.dup
  thaat_anomalies = anomalies[thaat_name]
  if thaat_anomalies
    thaat_anomalies.each do |swara, adjustment|
      case swara
      when :re; notes[1]+=adjustment
      when :ga; notes[2]+=adjustment
      when :ma; notes[3]+=adjustment
      when :pa; notes[4]+=adjustment
      when :dha; notes[5]+=adjustment
      when :ni; notes[6]+=adjustment
      end
    end
  end
  return notes, thaat_anomalies
end

def get_extended_scale(notes, pattern_type, octaves=2, anomalies=nil)
  extended = []
  if octaves.nil?
    net_movement = pattern_type.sum
    extra = net_movement.abs
    notes.each{|note| extended<<note}
    (1..extra).each do |i|
      idx = i % (notes.length-1)
      idx = 1 if idx==0
      extended<<notes[idx]+12*((i+notes.length-2)/(notes.length-1))
    end
    (1..extra).each do |i|
      idx = (notes.length-1-i)%(notes.length-1)
      idx = notes.length-2 if idx==0
      extended.unshift(notes[idx]-12*((i+notes.length-2)/(notes.length-1)))
    end
    print_sargam(extended, notes, anomalies)
  else
    (0...octaves).each do |octave|
      notes.each{|note| extended<<note+(octave*12)}
    end
  end
  extended
end

def dynamic_note_play(note, duration)
  attack_val = duration * ATTACK_RATIO
  release_val = duration * RELEASE_RATIO
  #with_synth :blade do
  play note, attack: attack_val, release: release_val, amp: 2
  #end
end

def play_alankaar(thaat_name, pattern, dur=0.25, octaves=nil)
  notes, anomalies = init_thaat(thaat_name)
  ext_notes = get_extended_scale(notes, pattern, octaves, anomalies)
  puts "Playing #{thaat_name} - Alankaar #{pattern}"
  max_start = ext_notes.length - pattern.sum.abs - 1
  puts "\n↑ Ascending:"
  (0..max_start).each do |start|
    p = [start]
    valid = true
    pattern.each do |step|
      nxt = p.last+step
      if nxt<0 || nxt>=ext_notes.length
        valid=false
        break
      end
      p<<nxt
      sample :tabla_dhec
    end
    if valid && p.length==pattern.length+1
      p_notes = p.map{|i| ext_notes[i]}
      print_sargam(p_notes, notes, anomalies)
      p.each do |i|
        sample choose([:tabla_na, :tabla_ke1, :tabla_na_o]) if (i+1)%p.length!=1
        dynamic_note_play(ext_notes[i], dur)
        sleep dur
      end
    end
  end
  puts "\n↓ Descending:"
  (ext_notes.length-1).downto(pattern.sum.abs).each do |start|
    p = [start]
    valid = true
    pattern.each do |step|
      nxt = p.last-step
      if nxt<0 || nxt>=ext_notes.length
        valid=false
        break
      end
      p<<nxt
      sample :tabla_dhec
    end
    if valid && p.length==pattern.length+1
      p_notes = p.map{|i| ext_notes[i]}
      print_sargam(p_notes, notes, anomalies)
      p.each do |i|
        sample choose([:tabla_na, :tabla_ke1, :tabla_na_o]) if i%pattern.length+1!=0
        dynamic_note_play(ext_notes[i], dur)
        sleep dur
      end
    end
  end
end

def practice(thaat, mode, pattern=nil, tempo=120, cycles=2, octaves=2)
  dur = 60.0/tempo
  cycles.times do |c|
    puts "=== Cycle #{c+1}/#{cycles} ==="
    if mode==:straight
      notes, anomalies = init_thaat(thaat)
      puts "\n↑ Arohana:"
      print_sargam(notes, notes, anomalies)
      notes.each{|n| dynamic_note_play(n, dur); sleep dur}
      puts "\n↓ Avarohana:"
      print_sargam(notes.reverse, notes, anomalies)
      notes.reverse.each{|n| dynamic_note_play(n, dur); sleep dur}
    elsif mode==:alankaar
      return puts "Error: pattern required" if pattern.nil?
      play_alankaar(thaat, pattern, dur, octaves)
    end
    sleep 0.5
  end
end

THAAT = :bhairav
with_fx :reverb, room: 0.7, mix: 0.65 do
  #practice(THAAT, :straight, nil, 110, 1, 0)
  #practice(THAAT, :straight, nil, 220, 2, 0)
  #practice(THAAT, :alankaar, [-2,1,2], 110, 1, 1)
  #practice(THAAT, :alankaar, [-1,1,1,-1], 220, 1, 1)
  practice(THAAT, :alankaar, [1,1], 220, 1, 1)
  practice(THAAT, :alankaar, [1,-1,1,1], 220*1.5, 2, 1)
end