"""
capstone.py - one entry point for the whole pipeline.

    python capstone.py devices                       what boards are known
    python capstone.py record --port COM12 --label speech
    python capstone.py qc                            check before training
    python capstone.py gui                           desktop app: live detection
    python capstone.py filters                       compare filter settings
    python capstone.py train                         fit on your recordings only
    python capstone.py train-corpus                  fit on yours + public data
    python capstone.py predict --wav some.wav        run it
    python capstone.py status                        where things stand

Each command forwards to the real script in its own folder, so you can also
run those directly. Nothing is hidden here - this only saves typing paths.

THE ORDER MATTERS

    record  ->  qc  ->  train  ->  predict

qc is not optional. It runs the confound probe, which tells you whether your
two classes differ by speech or merely by recording conditions. Training on a
set that fails that probe produces a number that looks excellent and means
nothing.
"""

import argparse
import glob
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(HERE, "Recording_Via_IOT_Devices")
ML = os.path.join(HERE, "ML")
DATA = os.path.join(HERE, "INMP_441_Dataset")


def run(script_dir, script, argv):
    """Run a sibling script as if it had been called directly."""
    sys.path.insert(0, script_dir)
    old = sys.argv
    sys.argv = [script] + list(argv)
    try:
        runpy.run_path(os.path.join(script_dir, script), run_name="__main__")
    except SystemExit as e:
        if e.code not in (None, 0):
            raise
    finally:
        sys.argv = old


def status():
    print(f"project: {HERE}\n")
    total = 0
    for name in ("speech", "non_speech"):
        folder = os.path.join(DATA, name)
        n = len(glob.glob(os.path.join(folder, "*.wav")))
        total += n
        print(f"  {name:<12}{n:>4} recordings")

    extra = [d for d in sorted(glob.glob(os.path.join(DATA, "*")))
             if os.path.isdir(d) and os.path.basename(d) not in ("speech", "non_speech")]
    for d in extra:
        n = len(glob.glob(os.path.join(d, "*.wav")))
        total += n
        print(f"  {os.path.basename(d):<12}{n:>4} recordings")

    pub = os.path.join(HERE, "Public_Dataset")
    for name in ("musan", "ESC-50_sorted", "google"):
        d = os.path.join(pub, name)
        if os.path.isdir(d):
            n = len(glob.glob(os.path.join(d, "**", "*.wav"), recursive=True))
            print(f"  {name:<12}{n:>4} public files")

    for label, fn in (("model (own)", "vad3_model.joblib"),
                      ("model (+corpus)", "vad3_corpus.joblib")):
        p = os.path.join(ML, fn)
        print(f"\n  {label:<17}{'trained' if os.path.exists(p) else 'not trained yet'}"
              if label.startswith("model (own)") else
              f"  {label:<17}{'trained' if os.path.exists(p) else 'not trained yet'}")
    model = os.path.join(ML, "vad3_model.joblib")

    print("\nnext step:")
    if total == 0:
        print("  record some audio:")
        print("    python capstone.py record --port COM12 --label speech --minutes 2")
    elif total < 8:
        print(f"  only {total} recordings. Get both classes from the same session,")
        print("  interleaved in ~2 minute blocks, before training means anything.")
    elif not os.path.exists(model):
        print("  python capstone.py qc        then, if it passes, train")
    else:
        print("  python capstone.py predict --port COM12")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    for name, helptext in [
        ("devices", "list supported boards"),
        ("record", "capture audio from a board"),
        ("qc", "check a dataset, including the confound probe"),
        ("gui", "open the desktop app"),
        ("filters", "compare front-end filter settings"),
        ("train", "fit on your own recordings only"),
        ("train-corpus", "fit on your recordings plus Public_Dataset"),
        ("predict", "run the model on a wav or a live port"),
        ("status", "show what exists and what to do next"),
    ]:
        sub.add_parser(name, help=helptext, add_help=False)

    args, rest = ap.parse_known_args()

    if args.cmd == "devices":
        run(REC, "record.py", ["--list"])
    elif args.cmd == "record":
        run(REC, "record.py", rest)
    elif args.cmd == "qc":
        run(ML, "qc.py", rest)
    elif args.cmd == "gui":
        run(HERE, "sar_gui.py", rest)
    elif args.cmd == "filters":
        run(ML, "filter_test.py", rest)
    elif args.cmd == "train":
        run(ML, "train_basic.py", rest)
    elif args.cmd == "train-corpus":
        run(ML, "train_corpus.py", rest)
    elif args.cmd == "predict":
        run(ML, "predict.py", rest)
    elif args.cmd == "status":
        status()
    else:
        ap.print_help()
        print()
        status()


if __name__ == "__main__":
    main()
