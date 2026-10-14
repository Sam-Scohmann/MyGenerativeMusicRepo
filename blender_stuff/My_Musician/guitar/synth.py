"""A plucked steel-string guitar tone (vectorised additive model).

Each partial n of a string plucked at fraction b of its length has amplitude
~ |sin(pi n b)| / n^2 (brighter with a hard pick attack), decays faster the
higher it is, and the attack carries a short pick click. A palm-muted note is
the same pluck with a fast decay and dark spectrum.
"""
import math

import numpy as np

RATE = 48000
_cache = {}


def _pluck(midi, rate, brightness, muted, rng):
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    length = 0.35 if muted else float(np.clip(5.0 - (midi - 40) * 0.05, 2.0, 5.0))
    t = np.arange(int(length * rate), dtype=np.float32) / rate
    tau0 = 0.08 if muted else float(np.clip(2.6 * 2.0 ** (-(midi - 40) / 20.0), 0.6, 3.0))
    pluck_at = 0.13 + 0.05 * (1.0 - brightness)   # nearer the bridge = brighter
    inharm = 2e-5 * (1.0 + max(0, midi - 40) / 24.0)
    limit = min(rate * 0.45, 9000.0 if not muted else 2500.0)
    out = np.zeros_like(t)
    for n in range(1, 40):
        fn = n * f0 * math.sqrt(1.0 + inharm * n * n)
        if fn > limit:
            break
        amp = (abs(math.sin(math.pi * n * pluck_at)) + 0.02) / n ** (2.1 - 0.9 * brightness)
        tau = tau0 / (1.0 + 0.35 * (n - 1) * (1.4 if muted else 1.0))
        out += amp * np.exp(-t / tau) * np.sin(2.0 * np.pi * fn * t + rng.uniform(0, 2 * np.pi))
    out *= 1.0 - np.exp(-t / 0.0008)              # sub-millisecond attack
    click = int(0.006 * rate)                     # pick click
    noise = rng.standard_normal(click).astype(np.float32)
    out[:click] += noise * np.exp(-t[:click] / 0.0012) * (0.05 + 0.12 * brightness)
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak).astype(np.float32)


def note(midi, velocity, muted=False, rate=RATE):
    """Pluck for a MIDI note at velocity 0..1 (two cached brightness layers mixed)."""
    key = (midi, muted, rate)
    if key not in _cache:
        rng = np.random.default_rng(midi * 7 + (1 if muted else 0))
        _cache[key] = (_pluck(midi, rate, 0.2, muted, rng), _pluck(midi, rate, 1.0, muted, rng))
    soft, hard = _cache[key]
    return soft + (hard - soft) * float(np.clip(velocity, 0.0, 1.0))


def render(strikes, rate=RATE, volume=0.7, release=0.08, stereo=0.5):
    """strikes: [(time s, midi, velocity 0..1, end time s or None, string, muted)]
    -> (stereo float32 buffer, first time). A note ends at its end time (string
    damped by the fretting hand or the next pluck on the same string)."""
    if not strikes:
        return np.zeros((0, 2), dtype=np.float32), 0.0
    first = min(s[0] for s in strikes)
    tail = int(release * 5 * rate)
    total = max(int((s[0] - first) * rate) + len(note(s[1], s[2], s[5], rate)) for s in strikes) + rate // 10
    buf = np.zeros((total, 2), dtype=np.float32)
    strings = sorted({s[4] for s in strikes})
    for t, midi, vel, end, string, muted in strikes:
        sig = note(midi, vel, muted, rate)
        if end is not None:
            held = max(int((end - t) * rate), int(0.01 * rate))
            if held < len(sig):
                n = min(len(sig), held + tail)
                sig = sig[:n].copy()
                sig[held:] *= np.exp(-np.arange(n - held, dtype=np.float32) / (release * rate))
        gain = volume * (0.3 + 0.7 * vel)
        k = strings.index(string) / max(len(strings) - 1, 1)  # low strings left, high right
        pan = (k * 2.0 - 1.0) * stereo
        i = int((t - first) * rate)
        seg = buf[i:i + len(sig)]
        seg[:, 0] += sig[:len(seg)] * gain * math.cos((pan + 1) * math.pi / 4)
        seg[:, 1] += sig[:len(seg)] * gain * math.sin((pan + 1) * math.pi / 4)
    peak = float(np.max(np.abs(buf))) or 1.0
    if peak > 0.98:
        buf *= 0.98 / peak
    return buf, first
