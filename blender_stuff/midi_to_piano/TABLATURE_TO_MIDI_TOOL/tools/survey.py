"""Regression survey: read every image under tabs/ and summarise.

    python tools/survey.py [--overlays]
"""
import glob
import os
import sys
from collections import Counter

import cv2
import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
from tabreader import timing, vision  # noqa: E402


def main():
    overlays = "--overlays" in sys.argv
    for f in sorted(glob.glob(os.path.join(ROOT, "tabs", "**", "*.png"), recursive=True)):
        img = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
        page = vision.analyze(img)
        song = timing.build_song(page)
        bad = [b.number for b in song.bars if not b.ok]
        print(f"{os.path.basename(f)}: {page.kind}, {len(page.staffs)} rows, {len(song.bars)} bars, "
              f"{len(song.notes)} notes, tempo {page.tempo}, {page.time_sig[0]}/{page.time_sig[1]}")
        print(f"   bars not adding up: {bad or 'none'}")
        if page.kind == "drums":
            c = Counter(h.key + (" ghost" if h.ghost else "")
                        for st in page.staffs for b in st.bars for h in b.hits)
            print("   heads:", ", ".join(f"{k}={v}" for k, v in c.most_common()))
            print("   accents:", sum(len(b.accents) for st in page.staffs for b in st.bars))
        for w in song.warnings[:12]:
            print("   !", w)
        if overlays:
            out = os.path.join(ROOT, "tools", "clusters", "overlay_" + os.path.basename(f))
            cv2.imencode(".png", vision.draw_overlay(img, page))[1].tofile(out)


if __name__ == "__main__":
    main()
