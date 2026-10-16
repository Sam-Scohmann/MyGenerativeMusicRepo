"""Harvest digit glyphs from tab images and cluster them for labelling.

    python tools/harvest_glyphs.py sheet  img1.png img2.png ...   -> clusters.png + clusters.npz
    python tools/harvest_glyphs.py label  "0:3,1:5,2:(,..."       -> writes tabreader/glyph_templates.npz

`sheet` draws one representative per cluster with its id so you can read off
what each cluster is; `label` turns id:label pairs into the template file
(every member of a cluster becomes a template, which makes matching robust).
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tabreader import glyphs, vision  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "clusters")


class Recorder(glyphs.Classifier):
    def __init__(self):
        super().__init__()
        self.patches = []

    def classify(self, patch, bg):
        self.patches.append((patch.copy(), bg))
        return "0", 1.0


def sheet(paths):
    rec = Recorder()
    for p in paths:
        img = cv2.imread(p)
        vision.analyze(img, rec)
    vecs = np.array([glyphs.normalise(p, bg).ravel() for p, bg in rec.patches])
    # greedy clustering
    centres, members = [], []
    for i, v in enumerate(vecs):
        if centres:
            d = np.sqrt(((np.array(centres) - v) ** 2).sum(1))
            j = int(np.argmin(d))
            if d[j] < 2.2:
                members[j].append(i)
                continue
        centres.append(v)
        members.append([i])
    os.makedirs(OUT, exist_ok=True)
    cell = 60
    cols = 10
    rows = (len(centres) + cols - 1) // cols
    canvas = np.full((rows * cell, cols * cell, 3), 255, np.uint8)
    for k, mem in enumerate(members):
        p, bg = rec.patches[mem[0]]
        big = cv2.resize(p, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        big = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR) if big.ndim == 2 else big
        r, c = divmod(k, cols)
        y, x = r * cell + 14, c * cell + 4
        hh, ww = min(big.shape[0], cell - 16), min(big.shape[1], cell - 8)
        canvas[y:y + hh, x:x + ww] = big[:hh, :ww]
        cv2.putText(canvas, f"{k}:{len(mem)}", (c * cell + 2, r * cell + 11),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)
    cv2.imwrite(os.path.join(OUT, "clusters.png"), canvas)
    np.savez_compressed(os.path.join(OUT, "clusters.npz"), vecs=vecs,
                        members=np.array([m for mem in members for m in mem]),
                        lengths=np.array([len(m) for m in members]))
    print(f"{len(rec.patches)} glyphs -> {len(centres)} clusters")


def label(spec):
    d = np.load(os.path.join(OUT, "clusters.npz"))
    vecs, flat, lengths = d["vecs"], d["members"], d["lengths"]
    starts = np.concatenate([[0], np.cumsum(lengths)[:-1]])
    mapping = dict(item.split(":", 1) for item in spec.split(","))
    out_v, out_l = [], []
    for k, (a, n) in enumerate(zip(starts, lengths)):
        lab = mapping.get(str(k))
        if not lab or lab == "x":
            continue
        for i in flat[a:a + n]:
            out_v.append(vecs[i])
            out_l.append(lab)
    clf = glyphs.Classifier.load()
    clf.vectors = np.vstack([clf.vectors, np.array(out_v, np.float32)])
    clf.labels += out_l
    clf.save()
    print(f"saved {len(clf.labels)} templates: " +
          ", ".join(f"{l}={clf.labels.count(l)}" for l in sorted(set(clf.labels))))


if __name__ == "__main__":
    if sys.argv[1] == "sheet":
        sheet(sys.argv[2:])
    else:
        label(sys.argv[2])
