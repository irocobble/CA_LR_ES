"""
filter_test.py - decide the front-end filtering with evidence, not opinion.

    python filter_test.py                    both tests
    python filter_test.py --synthetic-only   before you have clean recordings

WHY THIS EXISTS

"Should we add a low-pass?" cannot be answered from your dataset alone,
because your dataset does not contain the sounds a low-pass would hurt you
on. Room tone is an easy negative. A running generator, wind across the
microphone, and 1/f rumble are the ones that decide the question, and until
you record them the only honest test is a synthetic one.

So this runs two tests and prints both:

  A. YOUR RECORDINGS. d-prime of each feature per filter setting. Trustworthy
     only once qc.py's confound probe passes - before that, a filter that
     happens to preserve the confound will score well for the wrong reason.

  B. SYNTHETIC CONFUSERS. Voiced speech against white noise, pink noise,
     50 Hz hum, a tonal generator, wind rumble and impulsive clicks. This is
     the one that catches filtering that backfires.

READ IT AS: pick the setting with the largest MARGIN in test B among those
that also do well in test A. If the two tests disagree, believe B until your
own negatives include those sounds.
"""

import argparse
import glob
import os
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt, lfilter

import features3 as F

SR = F.SAMPLE_RATE


# ---------------------------------------------------------------------------
# Front-end variants under test
# ---------------------------------------------------------------------------
def make_frontend(hp=100, lp=None, preemph=0.0):
    """Return a function that applies one filter configuration.

    hp       high-pass cutoff, Hz. Removes microphone rumble.
    lp       low-pass cutoff, Hz, or None.
    preemph  first-difference coefficient. y[n] = x[n] - a*x[n-1].
             This flattens a 1/f spectrum, which matters because 1/f noise is
             concentrated at low frequencies and therefore looks narrowband -
             and anything narrowband autocorrelates well, so rumble scores as
             'periodic' when it is nothing of the sort.

    All three are biquads or simpler in C. Cost is not the question here;
    whether they help is.
    """
    sos_hp = butter(4, hp, "highpass", fs=SR, output="sos") if hp else None
    sos_lp = butter(4, lp, "lowpass", fs=SR, output="sos") if lp else None

    def fn(x):
        y = np.asarray(x, dtype=np.float64)
        if sos_hp is not None:
            y = sosfilt(sos_hp, y)
        if preemph:
            y = lfilter([1.0, -preemph], [1.0], y)
        if sos_lp is not None:
            y = sosfilt(sos_lp, y)
        return y
    return fn


CONFIGS = [
    ("HP100 (current)",        dict(hp=100)),
    ("HP100 + LP4000",         dict(hp=100, lp=4000)),
    ("HP100 + LP2000",         dict(hp=100, lp=2000)),
    ("HP100 + LP1000",         dict(hp=100, lp=1000)),
    ("HP100 + preemph",        dict(hp=100, preemph=0.97)),
    ("HP100 + preemph + LP2k", dict(hp=100, preemph=0.97, lp=2000)),
    ("HP100 + preemph + LP1k", dict(hp=100, preemph=0.97, lp=1000)),
    ("HP300 (old firmware)",   dict(hp=300)),
]


# ---------------------------------------------------------------------------
# Synthetic material
# ---------------------------------------------------------------------------
def make_voice(n, f0, rng):
    """Harmonic source, three formants, syllable-rate envelope.

    The envelope uses |sin| and not sin-squared: squaring doubles the rate and
    would put the modulation at 8.4 Hz instead of 4.2, which is outside the
    syllabic band and would make any modulation feature look useless.
    """
    t = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(f0 * (1 + 0.06 * np.sin(2 * np.pi * 3.1 * t))) / SR
    s = sum(np.sin(k * ph + rng.uniform(0, 6)) / k for k in range(1, 40))
    for fc, bw in ((700, 120), (1200, 150), (2600, 200)):
        s = s + 0.5 * sosfilt(butter(2, [max(fc - bw, 50), fc + bw],
                                     "bandpass", fs=SR, output="sos"), s)
    env = 0.25 + 0.75 * np.abs(np.sin(2 * np.pi * 4.2 * t))
    y = s * env
    return y / (np.max(np.abs(y)) + 1e-9)


def make_confusers(n, rng):
    t = np.arange(n) / SR
    out = {}
    out["white"] = rng.standard_normal(n)
    pk = lfilter([0.049, -0.095, 0.050], [1, -2.494, 2.017, -0.522],
                 rng.standard_normal(n))
    out["pink"] = pk / (np.std(pk) + 1e-9)
    out["hum50"] = sum(np.sin(2 * np.pi * 50 * k * t) / k for k in range(1, 8))
    out["generator"] = (sum(np.sin(2 * np.pi * 200 * k * t + rng.uniform(0, 6)) / k
                            for k in range(1, 6))
                        * (1 + 0.05 * rng.standard_normal(n)))
    wd = lfilter([1], [1, -0.995], rng.standard_normal(n))
    out["wind"] = wd / (np.std(wd) + 1e-9)
    imp = np.zeros(n)
    for k in rng.integers(0, n - 400, 12):
        imp[k:k + 400] += np.exp(-np.arange(400) / 25) * rng.choice([-1.0, 1.0])
    out["clicks"] = imp
    return {k: v / (np.max(np.abs(v)) + 1e-9) for k, v in out.items()}


# ---------------------------------------------------------------------------
def d_prime(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if len(a) == 0 or len(b) == 0:
        return float("nan")
    return abs(a.mean() - b.mean()) / np.sqrt(0.5 * (a.var() + b.var()) + 1e-12)


def test_real(dataset):
    files = {}
    for cls in ("speech", "non_speech"):
        files[cls] = sorted(glob.glob(os.path.join(dataset, cls, "*.wav")))
    if not files["speech"] or not files["non_speech"]:
        return None

    audio = {c: [] for c in files}
    for c, paths in files.items():
        for p in paths:
            rate, x = wavfile.read(p)
            if x.ndim > 1:
                x = x.mean(axis=1)
            if rate != SR:
                continue
            audio[c].append(x.astype(np.float64) / 32768.0)

    print("A. YOUR RECORDINGS - d-prime per feature (higher separates better)\n")
    print(f"  {'front end':<26}{'harmonicity':>13}{'band ratio':>13}{'snr_db':>10}")
    print("  " + "-" * 62)
    for name, kw in CONFIGS:
        fe = make_frontend(**kw)
        vals = {c: {k: [] for k in F.FEATURE_NAMES} for c in audio}
        for c, sigs in audio.items():
            for x in sigs:
                y = fe(x)
                nf = F.NoiseFloor().update(y).value
                for w in F.windows(y):
                    v = F.extract(w, nf)
                    for i, k in enumerate(F.FEATURE_NAMES):
                        vals[c][k].append(v[i])
        row = f"  {name:<26}"
        for k in F.FEATURE_NAMES:
            row += f"{d_prime(vals['speech'][k], vals['non_speech'][k]):>13.2f}"
        print(row)
    return True


def test_synthetic():
    rng = np.random.default_rng(3)
    n = SR * 3
    voices = [make_voice(n, f0, rng) for f0 in (105, 130, 165, 200, 240)]
    conf = make_confusers(n, rng)

    print("\n\nB. SYNTHETIC CONFUSERS - mean harmonicity")
    print("   speech should stay HIGH, every confuser should stay LOW\n")
    keys = list(conf)
    hdr = f"  {'front end':<26}{'SPEECH':>8}" + "".join(f"{k:>10}" for k in keys) + f"{'margin':>9}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    best = None
    for name, kw in CONFIGS:
        fe = make_frontend(**kw)
        sv = [F.harmonicity(w) for v in voices for w in F.windows(fe(v))]
        row = f"  {name:<26}{np.mean(sv):>8.3f}"
        worst = 0.0
        for k in keys:
            m = float(np.mean([F.harmonicity(w) for w in F.windows(fe(conf[k]))]))
            worst = max(worst, m)
            row += f"{m:>10.3f}"
        margin = float(np.mean(sv)) - worst
        print(row + f"{margin:>9.3f}")
        if best is None or margin > best[1]:
            best = (name, margin)

    print("\n  margin = mean speech harmonicity minus the WORST confuser.")
    print(f"  best here: {best[0]} (margin {best[1]:+.3f})")
    print("\n  A NEGATIVE margin means some confuser scores at least as periodic")
    print("  as speech. A steady tonal source - a generator - will do this to")
    print("  any harmonicity feature, because a generator genuinely IS periodic.")
    print("  No filter fixes that. The cue that does is syllable-rate modulation:")
    print("  speech fluctuates at 2-8 Hz, a generator does not. Add that feature")
    print("  only when you have recorded a generator and can prove you need it.")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(here, "..", "INMP_441_Dataset"))
    ap.add_argument("--synthetic-only", action="store_true")
    args = ap.parse_args()

    if not args.synthetic_only:
        if test_real(os.path.abspath(args.dataset)) is None:
            print("A. YOUR RECORDINGS - none found, skipping\n")

    test_synthetic()

    print("\n\nHOW TO READ THIS")
    print("  Pick the setting with the best margin in B among those that also do")
    print("  well in A. If the two disagree, believe B - your own negatives do not")
    print("  yet contain wind, generators or 1/f rumble, so A cannot see the")
    print("  failure modes that B is testing for.")
    print("  Re-run this after qc.py's confound probe passes. Until it does, a")
    print("  filter that happens to preserve the confound scores well in A for")
    print("  entirely the wrong reason.")


if __name__ == "__main__":
    main()
