"""
train_corpus.py - train the three-feature detector on your recordings PLUS
the public corpora, with every source budgeted by window count.

    python train_corpus.py                       auto-detect Public_Dataset
    python train_corpus.py --budget 6000         windows per source
    python train_corpus.py --device-only         skip the public data

WHY BUDGET BY WINDOWS AND NOT BY FILES

This is the trap that wasted a whole training run last time. Window yield per
file differs by three orders of magnitude:

    MUSAN speech      ~8 min per file    ~1000 windows each
    MUSAN music       ~2 min per file     ~450 windows each
    ESC-50             5 s per file          9 windows each
    Speech Commands    1 s per file          1 window each

Cap every source at 1200 FILES and MUSAN ends up with 99.4% of the positive
windows while Speech Commands contributes 0.6% - so the speaker diversity that
was the entire reason for adding it never reaches the model. Cap by WINDOWS
and each source contributes what you asked it to.

WHY SHORT CLIPS GET CONCATENATED

snr_db is measured against the noise floor of the recording it came from. If
the recording IS one window, the floor is estimated from the speech itself and
the number is meaningless - on zero-padded clips it saturates completely.
Clips shorter than --min-context-sec are joined into longer pseudo-recordings
first, with a little real background between them.

WHY YOUR OWN RECORDINGS GET A WEIGHT, NOT JUST A PLACE IN THE PILE

A few hundred device windows dropped into a hundred thousand corpus windows is
0.2% of the training mass, and the model simply ignores them. --device-weight
sets their share of the mass directly, independent of how many there are.
"""

import argparse
import glob
import os
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score, average_precision_score, confusion_matrix
import joblib

import features3 as F

SR = F.SAMPLE_RATE
AUDIO_EXT = ("*.wav", "*.WAV", "*.flac", "*.mp3")

# ESC-50 ships a "human, non-speech" group, and for a search-and-rescue
# detector several of those are not negatives at all - they are exactly the
# sounds a trapped person makes. Training on them as negatives teaches the
# model to ignore a cough or a moan.
#
#   POSITIVE for this application: crying_baby, coughing, laughing, snoring,
#       breathing, sneezing - human vocalisation, periodic, voice-band.
#   NEGATIVE and fine to keep: clapping, footsteps, brushing_teeth,
#       drinking_sipping - human, but not vocal.
#
# door_wood_knock is listed separately: knocking is arguably the highest-value
# POSITIVE in this application, since it is what rescue protocols tell victims
# to do and it travels through rubble far better than a voice.
ESC50_VOCAL = ("crying_baby", "coughing", "laughing", "snoring",
               "breathing", "sneezing")
ESC50_KNOCK = ("door_wood_knock",)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_audio(path):
    """Read any of the corpora to mono float at 16 kHz.

    ESC-50 ships at 44.1 kHz while MUSAN and Speech Commands are 16 kHz, so
    resampling is not optional - and a silently skipped source is worse than
    a loud failure, which is why this raises rather than returning None.
    """
    rate, x = wavfile.read(path)
    x = np.asarray(x)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype(np.float64)
    if np.issubdtype(np.asarray(wavfile.read(path)[1]).dtype, np.integer):
        x = x / 32768.0
    if rate != SR:
        g = np.gcd(int(rate), SR)
        x = resample_poly(x, SR // g, rate // g)
    return x


def wav_seconds(path):
    """Duration from the header where possible, so work can be planned before
    anything is decoded."""
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


def find_audio(folder, recursive=True):
    out = []
    for ext in AUDIO_EXT:
        pat = os.path.join(folder, "**", ext) if recursive else os.path.join(folder, ext)
        out += glob.glob(pat, recursive=recursive)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Source definition
# ---------------------------------------------------------------------------
class Source:
    def __init__(self, name, folder, label, budget, is_device=False):
        self.name = name
        self.folder = folder
        self.label = label
        self.budget = budget
        self.is_device = is_device


def discover(root, public, device_budget, corpus_budget, device_only):
    """Build the source list from what is actually on disk."""
    src = []
    data = os.path.join(root, "INMP_441_Dataset")
    for cls, lab in (("speech", 1), ("non_speech", 0)):
        folder = os.path.join(data, cls)
        if find_audio(folder, recursive=False):
            src.append(Source(f"device/{cls}", folder, lab, device_budget, True))

    if device_only or not public or not os.path.isdir(public):
        return src

    # MUSAN keeps its categories in subfolders; the label depends on which.
    musan = None
    for cand in ("musan", "MUSAN"):
        if os.path.isdir(os.path.join(public, cand)):
            musan = os.path.join(public, cand)
    if musan:
        for sub, lab in (("speech", 1), ("music", 0), ("noise", 0)):
            folder = os.path.join(musan, sub)
            if os.path.isdir(folder):
                src.append(Source(f"musan/{sub}", folder, lab, corpus_budget))

    for cand, lab in (("ESC-50_sorted", 0), ("esc50", 0), ("ESC-50", 0)):
        folder = os.path.join(public, cand)
        if os.path.isdir(folder):
            src.append(Source("esc50", folder, lab, corpus_budget))
            break

    for cand in ("google", "speech_commands"):
        folder = os.path.join(public, cand)
        if os.path.isdir(folder):
            src.append(Source("speech_commands", folder, 1, corpus_budget))
            break

    return src


# ---------------------------------------------------------------------------
# Work planning
# ---------------------------------------------------------------------------
def plan(paths, min_context_sec, hop_sec, budget, seed):
    """Group files into jobs, then keep a random subset within the budget.

    Shuffling before trimming matters: taking the first N files alphabetically
    would, in ESC-50, give you whole categories and none of the others.
    """
    jobs, batch, acc, durs = [], [], 0.0, []
    for p in paths:
        d = wav_seconds(p)
        if d is None or d >= min_context_sec:
            jobs.append([p])
            durs.append(d or min_context_sec)
        else:
            batch.append(p)
            acc += d + 0.3
            if acc >= min_context_sec:
                jobs.append(batch)
                durs.append(acc)
                batch, acc = [], 0.0
    if batch:
        jobs.append(batch)
        durs.append(acc)

    if not budget:
        return jobs

    order = np.random.default_rng(seed).permutation(len(jobs))
    kept, spent = [], 0.0
    for i in order:
        kept.append(jobs[i])
        spent += durs[i] / hop_sec
        if spent >= budget:
            break
    return kept


def features_for_job(job, background, window_sec, hop_sec, max_context_sec):
    """One job -> list of feature vectors.

    Short clips are joined with a splice of real recorded background between
    them, so the noise floor has something genuine to measure. Long files are
    cut into chunks, which keeps memory bounded and makes the training-time
    noise floor resemble the rolling estimate used live.
    """
    parts = []
    for p in job:
        try:
            parts.append(load_audio(p))
        except Exception:
            continue
        if len(job) > 1 and background is not None and len(background):
            gap = int(0.3 * SR)
            s = np.random.randint(0, max(len(background) - gap, 1))
            parts.append(background[s:s + gap])
    if not parts:
        return []

    sig = np.concatenate(parts)
    chunk = int(max_context_sec * SR)
    out = []
    for s in range(0, max(len(sig), 1), chunk):
        piece = sig[s:s + chunk]
        if len(piece) < int(window_sec * SR):
            break
        out.extend(F.features_for_signal(piece, window_sec, hop_sec))
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def recall_at_fpr(y, scores, target=0.05):
    neg = np.sort(scores[y == 0])[::-1]
    if len(neg) == 0:
        return float("nan"), float("nan")
    k = max(min(int(np.ceil(target * len(neg))), len(neg)) - 1, 0)
    thr = neg[k]
    return float(np.mean(scores[y == 1] >= thr)), float(thr)


def evaluate(X, y, groups, w, folds):
    cv = GroupKFold(n_splits=min(folds, len(np.unique(groups))))
    oof = np.full(len(y), np.nan)
    for tr, te in cv.split(X, y, groups=groups):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(max_iter=5000)
        clf.fit(sc.transform(X[tr]), y[tr], sample_weight=w[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
    return oof


def worst_negatives(y, s, groups, n=12):
    """The negative groups scoring most speech-like.

    recall@5%FPR is set entirely by the top 5% of negatives, so when it
    collapses while ROC-AUC stays healthy, a small tail of mislabelled or
    genuinely speech-like negatives is responsible. Printing them by name
    turns an unexplained number into a fixable list.
    """
    rows = {}
    for g in np.unique(groups[y == 0]):
        m = (groups == g) & (y == 0)
        rows[g] = float(np.mean(s[m]))
    top = sorted(rows.items(), key=lambda kv: -kv[1])[:n]
    pos_mean = float(np.mean(s[y == 1])) if (y == 1).any() else float("nan")
    print(f"\n  negatives scoring most speech-like "
          f"(mean speech score for comparison: {pos_mean:.3f})")
    for g, v in top:
        print(f"    {v:>6.3f}  {g}")


def report(tag, y, s):
    if len(np.unique(y)) < 2:
        print(f"  {tag}: only one class present, nothing to measure")
        return
    r5, thr = recall_at_fpr(y, s)
    cm = confusion_matrix(y, (s >= thr).astype(int))
    print(f"  {tag:<10} ROC-AUC={roc_auc_score(y, s):.3f}  "
          f"PR-AUC={average_precision_score(y, s):.3f} (chance {y.mean():.3f})  "
          f"recall@5%FPR={r5:.3f}")
    print(f"             TN={cm[0,0]:<7} FP={cm[0,1]:<7} FN={cm[1,0]:<7} TP={cm[1,1]}")


# ---------------------------------------------------------------------------
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=root)
    ap.add_argument("--public", default=os.path.join(root, "Public_Dataset"))
    ap.add_argument("--budget", type=int, default=6000,
                    help="windows per public source")
    ap.add_argument("--device-budget", type=int, default=0,
                    help="windows per device folder, 0 = use everything")
    ap.add_argument("--device-weight", type=float, default=0.30,
                    help="share of training mass given to your own recordings")
    ap.add_argument("--device-only", action="store_true")
    ap.add_argument("--window-sec", type=float, default=1.0)
    ap.add_argument("--hop-sec", type=float, default=0.5)
    ap.add_argument("--min-context-sec", type=float, default=4.0)
    ap.add_argument("--max-context-sec", type=float, default=30.0)
    ap.add_argument("--esc50-vocal", choices=["negative", "exclude", "positive"],
                    default="exclude",
                    help="what to do with ESC-50's human vocal categories "
                         "(crying_baby, coughing, laughing, snoring, breathing, "
                         "sneezing). 'negative' is the raw ESC-50 label and is "
                         "wrong for this application; 'exclude' is the safe "
                         "default; 'positive' treats them as target sounds")
    ap.add_argument("--features", default=None,
                    help="comma-separated subset, e.g. harmonicity,voice_band_ratio")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(here, "vad3_corpus.joblib"))
    args = ap.parse_args()

    sources = discover(args.root, args.public, args.device_budget,
                       args.budget, args.device_only)
    if not sources:
        sys.exit("No audio found. Record something, or check --public.")

    # Real recorded background, used as the splice between short clips so the
    # noise floor is estimated against this microphone rather than silence.
    background = None
    ns = os.path.join(args.root, "INMP_441_Dataset", "non_speech")
    ns_files = find_audio(ns, recursive=False)
    if ns_files:
        try:
            background = np.concatenate([load_audio(p) for p in ns_files[:5]])
        except Exception:
            background = None

    esc50_vocal_paths = []
    print("building features\n")
    print(f"  {'source':<22}{'lbl':>4}{'files':>8}{'jobs':>7}{'windows':>10}")
    X, y, groups, is_dev = [], [], [], []
    summary = []
    for i, s in enumerate(sources):
        paths = find_audio(s.folder, recursive=not s.is_device)
        if s.name == "esc50" and args.esc50_vocal != "negative":
            vocal = [p for p in paths
                     if any(k in p.replace("\\", "/").lower() for k in ESC50_VOCAL)]
            if vocal:
                paths = [p for p in paths if p not in set(vocal)]
                what = ("moved to the SPEECH class" if args.esc50_vocal == "positive"
                        else "dropped")
                print(f"  {'esc50 human-vocal':<22}{'':>4}{len(vocal):>8}   ({what})")
                if args.esc50_vocal == "positive":
                    sources.append(Source("esc50_vocal", s.folder, 1, s.budget))
                    esc50_vocal_paths.extend(vocal)
        if s.name == "esc50_vocal":
            paths = esc50_vocal_paths
        if not paths:
            print(f"  {s.name:<22}{s.label:>4}{'0':>8}   (empty, skipped)")
            continue
        jobs = plan(paths, args.min_context_sec, args.hop_sec,
                    s.budget, args.seed + i)
        n0 = len(X)
        for job in jobs:
            for f in features_for_job(job, background, args.window_sec,
                                      args.hop_sec, args.max_context_sec):
                X.append(f)
                y.append(s.label)
                groups.append(f"{s.name}::{os.path.relpath(job[0], s.folder)}")
                is_dev.append(s.is_device)
        n = len(X) - n0
        summary.append((s.name, s.label, len(paths), len(jobs), n))
        print(f"  {s.name:<22}{s.label:>4}{len(paths):>8}{len(jobs):>7}{n:>10,}")

    X = np.array(X, dtype=np.float32)
    y = np.array(y)
    groups = np.array(groups)
    is_dev = np.array(is_dev)

    if len(X) == 0 or len(np.unique(y)) < 2:
        sys.exit("\nNeed both classes. Record some non_speech, or add public data.")

    feat_names = list(F.FEATURE_NAMES)
    if args.features:
        want = [w.strip() for w in args.features.split(",") if w.strip()]
        bad = [w for w in want if w not in feat_names]
        if bad:
            sys.exit(f"unknown feature(s): {bad}. Available: {feat_names}")
        keep = [feat_names.index(w) for w in want]
        X = X[:, keep]
        feat_names = want
        print(f"\nusing {len(feat_names)} feature(s): {', '.join(feat_names)}")

    for lab in (0, 1):
        tot = int((y == lab).sum())
        print(f"  {'TOTAL label=' + str(lab):<22}{'':>4}{'':>8}{'':>7}{tot:>10,}")
        for name, l, _, _, n in summary:
            if l == lab and tot:
                print(f"      {name:<28}{100*n/tot:>6.1f}% of the class")

    # ---- weights: device share first, then class balance inside each domain
    w = np.zeros(len(y), dtype=np.float64)
    have_dev, have_cor = is_dev.any(), (~is_dev).any()
    share = ({True: args.device_weight, False: 1 - args.device_weight}
             if (have_dev and have_cor)
             else {True: 1.0 if have_dev else 0.0, False: 1.0 if have_cor else 0.0})
    for d in (True, False):
        for c in (0, 1):
            m = (is_dev == d) & (y == c)
            if m.sum():
                w[m] = share[d] * 0.5 / m.sum()
    w *= len(w) / w.sum()

    print(f"\n{len(y):,} windows, {len(np.unique(groups))} groups")
    print(f"  device share of weight {w[is_dev].sum()/w.sum():>7.2%}  "
          f"(target {share[True]:.0%})")
    print(f"  speech share of weight {w[y==1].sum()/w.sum():>7.2%}  (target 50%)")

    print("\nfeature separation (d-prime)")
    print(f"  {'feature':<20}{'speech':>10}{'non-speech':>13}{'d-prime':>10}")
    for i, n in enumerate(feat_names):
        a, b = X[y == 1, i], X[y == 0, i]
        d = abs(a.mean() - b.mean()) / np.sqrt(0.5 * (a.var() + b.var()) + 1e-12)
        print(f"  {n:<20}{a.mean():>10.3f}{b.mean():>13.3f}{d:>10.2f}")

    print(f"\ngrouped cross-validation, split by file")
    oof = evaluate(X, y, groups, w, args.folds)
    report("overall", y, oof)
    if have_dev and have_cor:
        report("device", y[is_dev], oof[is_dev])
        report("corpus", y[~is_dev], oof[~is_dev])
        worst_negatives(y[~is_dev], oof[~is_dev], groups[~is_dev])

    # ---- is the third feature earning its place?
    print("\nfeature subsets - keep the smallest one that holds up")
    print(f"  {'features':<40}{'recall@5%FPR':>14}")
    best_full = recall_at_fpr(y, oof)[0]
    import itertools
    idx = list(range(len(feat_names)))
    subsets = [c for r in range(1, len(idx) + 1) for c in itertools.combinations(idx, r)]
    for sub in subsets:
        s = evaluate(X[:, list(sub)], y, groups, w, args.folds)
        r = recall_at_fpr(y, s)[0]
        names = " + ".join(feat_names[i] for i in sub)
        mark = "  <- all" if len(sub) == len(idx) else ""
        print(f"  {names:<40}{r:>14.3f}{mark}")
    print(f"\n  If a two-feature row is within ~0.02 of all three, drop the third:")
    print(f"  one fewer feature is one fewer thing to port and test in C.")

    # ---- final fit
    thr = recall_at_fpr(y, oof)[1]
    pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000))
    pipe.fit(X, y, logisticregression__sample_weight=w)
    joblib.dump({
        "pipeline": pipe, "feature_names": feat_names, "threshold": thr,
        "window_sec": args.window_sec, "hop_sec": args.hop_sec,
        "sample_rate": SR, "smooth_k": 3,
    }, args.out)
    print(f"\nsaved {args.out}")

    coef = pipe.named_steps["logisticregression"].coef_[0]
    print("\nweights (standardised)")
    for n, c in zip(feat_names, coef):
        print(f"  {n:<20}{c:+.3f}   {'higher' if c > 0 else 'lower '} -> speech")

    n_dev_groups = len(np.unique(groups[is_dev])) if have_dev else 0
    if have_dev and n_dev_groups < 12:
        print(f"\n  [!] only {n_dev_groups} of your own recordings. The device row above")
        print(f"      has a very wide error bar. Run qc.py's confound probe before")
        print(f"      trusting it at all.")


if __name__ == "__main__":
    main()
