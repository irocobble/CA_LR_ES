"""
qc.py - check a recording session before you train on it.

    python qc.py --dataset ../INMP_441_Dataset

Two jobs.

PER-FILE HEALTH. Clipping, silence, DC offset, how much energy sits below
100 Hz, and byte-misalignment corruption. That last one is the failure where
the link drops a byte, the 16-bit sample boundary flips, and every sample
afterwards becomes noise near the Nyquist frequency - while the WAV header
stays valid and the file still plays. It is detected here by zero-crossing
rate, because misaligned audio alternates sign almost every sample.

THE CONFOUND PROBE - the important one.

It trains a classifier on background only: the quietest frames of each file,
with the overall level divided out, so no speech is present at all. If that
can still tell your two classes apart, then something other than speech
differs between them - different room, different mic position, different
session, different gain - and a detector trained on the set will learn that
instead.

On the first dataset this probe reached 0.94 AUC. It should be near 0.5.
Run it after every recording session, before you train. It is the difference
between finding a problem in ten seconds and finding it in six weeks.
"""

import argparse
import glob
import os
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import stft
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import LeaveOneGroupOut, cross_val_predict
from sklearn.metrics import roc_auc_score

SR = 16000
ZCR_GARBAGE = 0.35


def file_report(path):
    rate, x = wavfile.read(path)
    x = x.astype(np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)

    xd = x - x.mean()
    zcr = float(np.mean(np.abs(np.diff(np.sign(xd))) > 0))

    f, _, Z = stft(x, rate, nperseg=512, noverlap=256)
    P = np.abs(Z) ** 2
    total = P.sum() + 1e-12
    lf = P[f < 100].sum() / total

    vb = (f >= 300) & (f < 3400)
    band = P[vb].sum(axis=0)
    floor = np.percentile(band, 10) + 1e-12
    snr = 10 * np.log10(band.mean() / floor)

    n_win = max(int(len(x) // rate), 1)
    per = len(band) / n_win
    quiet = sum(1 for i in range(n_win)
                if 10 * np.log10(band[int(i*per):int((i+1)*per)].mean() / floor) < 6)

    flags = []
    if zcr > ZCR_GARBAGE:
        flags.append("BYTE MISALIGNMENT - link dropped a byte, re-record")
    if np.mean(np.abs(x) >= 32700) > 0.01:
        flags.append("clipping")
    if len(x) / rate < 1.0:
        flags.append("shorter than one window")
    if lf > 0.5:
        flags.append(f"{100*lf:.0f}% of energy below 100 Hz")
    if abs(x.mean()) > 500:
        flags.append(f"DC offset {x.mean():.0f}")

    return {
        "dur": len(x) / rate, "rate": rate, "zcr": zcr,
        "peak": int(np.max(np.abs(x))) if len(x) else 0,
        "lf_pct": 100 * lf, "snr_db": snr,
        "quiet_windows": quiet, "n_windows": n_win, "flags": flags,
    }


def confound_probe(dataset, percentile=10):
    """Classify the two folders using ONLY their background noise shape."""
    rows, labels, groups = [], [], []
    for name, label in [("speech", 1), ("non_speech", 0)]:
        for p in sorted(glob.glob(os.path.join(dataset, name, "*.wav"))):
            rate, x = wavfile.read(p)
            x = x.astype(np.float64) / 32768.0
            if x.ndim > 1:
                x = x.mean(axis=1)
            f, _, Z = stft(x, rate, nperseg=512, noverlap=256)
            P = np.abs(Z) ** 2
            m = (f >= 100) & (f < 8000)          # skip the rumble band
            e = P[m].sum(axis=0)
            if len(e) < 10:
                continue
            quiet = P[m][:, e <= np.percentile(e, percentile)]
            if quiet.shape[1] < 3:
                continue
            s = np.log(quiet.mean(axis=1) + 1e-14)
            rows.append(s - s.mean())            # remove level, keep shape
            labels.append(label)
            groups.append(f"{name}/{os.path.basename(p)}")

    if len(rows) < 6 or len(set(labels)) < 2:
        return None

    X, y = np.array(rows), np.array(labels)
    pipe = make_pipeline(StandardScaler(),
                         LogisticRegression(max_iter=5000, class_weight="balanced"))
    prob = cross_val_predict(pipe, X, y, groups=groups, cv=LeaveOneGroupOut(),
                             method="predict_proba")[:, 1]
    return roc_auc_score(y, prob), len(y)


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--dataset", default=os.path.join(here, "..", "INMP_441_Dataset"))
    args = ap.parse_args()
    dataset = os.path.abspath(args.dataset)

    print(f"dataset: {dataset}\n")
    any_files = False
    durations = {"speech": [], "non_speech": []}

    for name in ("speech", "non_speech"):
        paths = sorted(glob.glob(os.path.join(dataset, name, "*.wav")))
        if not paths:
            print(f"{name}: empty")
            continue
        any_files = True
        print(f"{name}: {len(paths)} files")
        print(f"  {'file':<44}{'dur':>7}{'peak':>8}{'SNR':>7}{'LF%':>6}{'quiet':>7}")
        for p in paths:
            r = file_report(p)
            durations[name].append(r["dur"])
            print(f"  {os.path.basename(p)[:43]:<44}{r['dur']:>6.1f}s{r['peak']:>8}"
                  f"{r['snr_db']:>6.1f}{r['lf_pct']:>6.0f}"
                  f"{r['quiet_windows']:>4}/{r['n_windows']:<3}")
            for fl in r["flags"]:
                print(f"      [!] {fl}")
        print()

    if not any_files:
        sys.exit("Nothing to check yet.")

    ds, dn = durations["speech"], durations["non_speech"]
    if ds and dn and (min(ds) > max(dn) or min(dn) > max(ds)):
        print("[!] Clip durations do not overlap between the classes.")
        print("    Length alone identifies the class. Use one clip length for both.\n")

    print("=" * 70)
    print("CONFOUND PROBE - can the background alone tell the classes apart?")
    res = confound_probe(dataset)
    if res is None:
        print("  not enough files yet (need at least 3 per class)")
    else:
        auc, n = res
        print(f"  ROC-AUC = {auc:.3f} over {n} files   (0.500 is what you want)")
        if auc < 0.65:
            print("  PASS. The classes differ by speech, not by recording conditions.")
        elif auc < 0.80:
            print("  MARGINAL. Something is partly class-correlated. Check that gain,")
            print("  mic position and room were identical for both classes.")
        else:
            print("  FAIL. The background alone identifies the class, so a model")
            print("  can score well without hearing any speech. Any accuracy you")
            print("  measure on this set is not real. Re-record with the classes")
            print("  interleaved in one session, same mic, same gain, same position.")


if __name__ == "__main__":
    main()
