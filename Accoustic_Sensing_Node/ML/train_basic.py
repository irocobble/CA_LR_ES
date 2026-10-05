"""
train_basic.py - train the three-feature detector.

    python train_basic.py
    python train_basic.py --dataset ../INMP_441_Dataset --window-sec 1.0

WHAT THIS DOES DIFFERENTLY FROM A TUTORIAL

1. Splits by FILE, never by window.
   Windows cut from one recording are near-duplicates - same voice, same
   room, same gain. Split them randomly and copies of the same second land
   on both sides, so the model is graded on data it has already seen. Every
   number here comes from GroupKFold with the source file as the group.

2. Reports recall at a fixed false-alarm rate, not accuracy.
   A missed victim and a false alarm are not the same cost, so a single
   accuracy figure is meaningless. "How many do we catch, if we accept one
   false alarm in twenty" is the question that matters.

3. Prints a rule-based version too.
   With three features you may not need machine learning at all. The script
   fits plain thresholds as well and shows you how they compare. If the gap
   is small, take the thresholds - they are three if-statements in C.
"""

import argparse
import glob
import os
import sys

import numpy as np
from scipy.io import wavfile
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.metrics import roc_auc_score, average_precision_score, confusion_matrix
import joblib

import features3 as F


def load_wav(path, sr=F.SAMPLE_RATE):
    rate, x = wavfile.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if rate != sr:
        raise ValueError(f"{path}: {rate} Hz, expected {sr}")
    return x.astype(np.float64) / 32768.0


def build(dataset, window_sec, hop_sec):
    X, y, groups, files = [], [], [], []
    for label_name, label in [("speech", 1), ("non_speech", 0)]:
        folder = os.path.join(dataset, label_name)
        paths = sorted(glob.glob(os.path.join(folder, "*.wav")))
        if not paths:
            print(f"  [warn] nothing in {folder}")
        for p in paths:
            try:
                x = load_wav(p)
            except Exception as e:
                print(f"  [skip] {p}: {e}")
                continue
            feats = F.features_for_signal(x, window_sec, hop_sec)
            for f in feats:
                X.append(f)
                y.append(label)
                groups.append(os.path.basename(p))
            files.append((label_name, os.path.basename(p), len(feats)))
        print(f"  {label_name:<11}{len(paths):>4} files")
    return (np.array(X, dtype=np.float32), np.array(y),
            np.array(groups), files)


def recall_at_fpr(y_true, scores, target=0.05):
    """Highest recall obtainable while keeping false alarms at or under
    `target`. Reads directly as: catch this fraction, at this cost."""
    neg = np.sort(scores[y_true == 0])[::-1]
    if len(neg) == 0:
        return float("nan"), float("nan")
    k = max(min(int(np.ceil(target * len(neg))), len(neg)) - 1, 0)
    thr = neg[k]
    return float(np.mean(scores[y_true == 1] >= thr)), float(thr)


def fit_rules(X, y):
    """Best single threshold per feature, chosen for balanced accuracy.

    Not a serious classifier - a sanity check. If one feature alone is
    already close to the trained model, the model is not earning its place.
    """
    out = []
    for i, name in enumerate(F.FEATURE_NAMES):
        v = X[:, i]
        best = (0.0, None, 1)
        for t in np.percentile(v, np.arange(1, 100)):
            for sign in (1, -1):
                pred = (v * sign >= t * sign).astype(int)
                tpr = np.mean(pred[y == 1] == 1) if (y == 1).any() else 0
                tnr = np.mean(pred[y == 0] == 0) if (y == 0).any() else 0
                acc = 0.5 * (tpr + tnr)
                if acc > best[0]:
                    best = (acc, t, sign)
        out.append((name, best[0], best[1], best[2]))
    return out


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--dataset", default=os.path.join(here, "..", "INMP_441_Dataset"))
    ap.add_argument("--window-sec", type=float, default=1.0)
    ap.add_argument("--hop-sec", type=float, default=0.5)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(here, "vad3_model.joblib"))
    args = ap.parse_args()

    print("loading dataset")
    X, y, groups, files = build(os.path.abspath(args.dataset),
                                args.window_sec, args.hop_sec)
    if len(X) == 0:
        sys.exit("\nNo audio found. Record some first:\n"
                 "  cd ../Recording_Via_IOT_Devices\n"
                 "  python record.py --device esp32 --port COM12 --label speech")

    n_groups = len(np.unique(groups))
    print(f"\n{len(X)} windows from {n_groups} files "
          f"({int(y.sum())} speech / {int((1-y).sum())} non-speech)")

    if n_groups < 4 or len(np.unique(y)) < 2:
        sys.exit("\nNeed at least 2 files per class to split honestly. "
                 "Record more before training.")

    print("\nfeature separation (d-prime: >1 useful, >2 strong)")
    print(f"  {'feature':<20}{'speech':>10}{'non-speech':>13}{'d-prime':>10}")
    for i, name in enumerate(F.FEATURE_NAMES):
        a, b = X[y == 1, i], X[y == 0, i]
        d = abs(a.mean() - b.mean()) / np.sqrt(0.5 * (a.var() + b.var()) + 1e-12)
        print(f"  {name:<20}{a.mean():>10.3f}{b.mean():>13.3f}{d:>10.2f}")

    folds = min(args.folds, n_groups)
    cv = GroupKFold(n_splits=folds)
    scaler = StandardScaler()
    clf = LogisticRegression(max_iter=5000, class_weight="balanced")
    from sklearn.pipeline import make_pipeline
    pipe = make_pipeline(scaler, clf)

    prob = cross_val_predict(pipe, X, y, groups=groups, cv=cv,
                             method="predict_proba")[:, 1]

    auc = roc_auc_score(y, prob)
    ap_score = average_precision_score(y, prob)
    r5, thr = recall_at_fpr(y, prob, 0.05)
    cm = confusion_matrix(y, (prob >= thr).astype(int))

    print(f"\ngrouped {folds}-fold cross-validation (split by file)")
    print(f"  ROC-AUC          {auc:.3f}")
    print(f"  PR-AUC           {ap_score:.3f}   (chance = {y.mean():.3f})")
    print(f"  recall @ 5% FPR  {r5:.3f}   threshold {thr:.3f}")
    print(f"  confusion        TN={cm[0,0]} FP={cm[0,1]} FN={cm[1,0]} TP={cm[1,1]}")

    print("\nsingle-threshold rules, for comparison")
    print(f"  {'feature':<20}{'bal. accuracy':>15}{'rule':>28}")
    for name, acc, t, sign in fit_rules(X, y):
        op = ">=" if sign > 0 else "<="
        print(f"  {name:<20}{acc:>15.3f}{f'{name} {op} {t:.3f}':>28}")

    pipe.fit(X, y)
    joblib.dump({
        "pipeline": pipe,
        "feature_names": F.FEATURE_NAMES,
        "threshold": thr,
        "window_sec": args.window_sec,
        "hop_sec": args.hop_sec,
        "sample_rate": F.SAMPLE_RATE,
        "smooth_k": 3,
    }, args.out)
    print(f"\nsaved {args.out}")

    coefs = pipe.named_steps["logisticregression"].coef_[0]
    print("\nwhat the model learned (standardised weights)")
    for name, c in zip(F.FEATURE_NAMES, coefs):
        arrow = "higher -> speech" if c > 0 else "lower  -> speech"
        print(f"  {name:<20}{c:+.3f}   {arrow}")

    if n_groups < 20:
        print(f"\n  [!] only {n_groups} files. These numbers carry a very wide "
              f"\n      error bar. Treat them as a pipeline check, not a result.")


if __name__ == "__main__":
    main()
