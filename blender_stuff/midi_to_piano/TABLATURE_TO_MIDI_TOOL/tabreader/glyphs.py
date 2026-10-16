"""Tiny nearest-neighbour digit classifier.

Glyphs are normalised to a fixed grid of "inkiness" (0 = background, 1 = darkest
pixel) so the same templates serve dark fret numbers and light grey bar numbers.
Templates live in glyph_templates.npz next to this file and were harvested from
real tab screenshots (see tools/harvest_glyphs.py).
"""
import os

import cv2
import numpy as np

GRID_W, GRID_H = 10, 14
TEMPLATE_FILE = os.path.join(os.path.dirname(__file__), "glyph_templates.npz")


def normalise(patch, bg):
    p = patch.astype(np.float32)
    lo = float(p.min())
    span = max(1.0, bg - lo)
    ink = np.clip((bg - p) / span, 0, 1)
    ys, xs = np.where(ink > 0.25)
    if len(xs):
        ink = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = ink.shape
    # keep aspect: narrow glyphs (1) stay narrow, centred in the grid
    scale = GRID_H / h
    nw = max(1, min(GRID_W, int(round(w * scale))))
    small = cv2.resize(ink, (nw, GRID_H), interpolation=cv2.INTER_AREA)
    out = np.zeros((GRID_H, GRID_W), np.float32)
    off = (GRID_W - nw) // 2
    out[:, off:off + nw] = small
    return out


class Classifier:
    def __init__(self, vectors=None, labels=None):
        self.vectors = np.zeros((0, GRID_W * GRID_H), np.float32) if vectors is None else vectors
        self.labels = [] if labels is None else list(labels)

    @classmethod
    def load(cls, path=TEMPLATE_FILE):
        if not os.path.exists(path):
            return cls()
        d = np.load(path, allow_pickle=False)
        return cls(d["vectors"].astype(np.float32), [str(x) for x in d["labels"]])

    def save(self, path=TEMPLATE_FILE):
        np.savez_compressed(path, vectors=self.vectors, labels=np.array(self.labels))

    def add(self, patch, bg, label):
        v = normalise(patch, bg).ravel()[None]
        self.vectors = np.vstack([self.vectors, v])
        self.labels.append(label)

    def classify(self, patch, bg):
        """-> (label, confidence 0..1)."""
        if not len(self.labels):
            return "?", 0.0
        v = normalise(patch, bg).ravel()
        d = np.sqrt(((self.vectors - v) ** 2).sum(1))
        order = np.argsort(d)
        best = self.labels[order[0]]
        d0 = d[order[0]]
        other = next((d[i] for i in order if self.labels[i] != best), None)
        if other is None:
            return best, 1.0
        # margin between the best match and the best match of any other label
        conf = float(np.clip((other - d0) / max(other, 1e-6) * 2.0, 0, 1))
        return best, conf


_default = None


def default_classifier():
    global _default
    if _default is None:
        _default = Classifier.load()
    return _default
