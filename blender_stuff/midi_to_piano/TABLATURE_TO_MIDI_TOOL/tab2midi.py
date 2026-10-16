"""Headless: tablature image -> .mid (and optionally a detection overlay).

    python tab2midi.py "creep radiohead/creep_radiohead_bassguitar.png"
    python tab2midi.py tab.png -o out.mid --overlay debug.png --transpose 12 --tempo 92
"""
import argparse
import os

import cv2
import numpy as np

from tabreader import midi_file, timing, vision


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--out", help="output .mid (default: next to the image)")
    ap.add_argument("--overlay", help="write a detection overlay PNG here")
    ap.add_argument("--tempo", type=float, help="override tempo (bpm)")
    ap.add_argument("--transpose", type=int, default=0)
    ap.add_argument("--channel", type=int, help="1-16 (default 1, or 10 for drum notation)")
    a = ap.parse_args()

    img = cv2.imdecode(np.fromfile(a.image, np.uint8), cv2.IMREAD_COLOR)
    page = vision.analyze(img)
    song = timing.build_song(page, transpose=a.transpose, tempo=a.tempo)
    out = a.out or os.path.splitext(a.image)[0] + ".mid"
    channel = a.channel or (10 if page.kind == "drums" else 1)
    midi_file.write(out, song, channel=channel - 1,
                    track_name=os.path.splitext(os.path.basename(out))[0])
    bad = [b.number for b in song.bars if not b.ok]
    if page.kind == "drums":
        from collections import Counter
        from tabreader import drums
        kit = Counter(n.drum for n in song.notes)
        for k, c in sorted(kit.items(), key=lambda kv: int(kv[0].split("@")[1])):
            note = drums.note_for(drums.load_map(), k)
            print(f"  {k:<18} {c:>4} hits -> {note} {drums.GM_DRUMS.get(note, '')}")
    print(f"{page.kind}: {len(page.staffs)} rows, {len(song.bars)} bars, {len(song.notes)} notes, "
          f"{song.time_sig[0]}/{song.time_sig[1]} @ {song.tempo:g} bpm -> {out}")
    if bad:
        print(f"rhythm did not add up in bars: {bad}")
    for w in song.warnings:
        print("  !", w)
    if a.overlay:
        cv2.imencode(".png", vision.draw_overlay(img, page))[1].tofile(a.overlay)


if __name__ == "__main__":
    main()
