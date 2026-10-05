"""
predict.py - run the trained three-feature detector.

    python predict.py --wav ../INMP_441_Dataset/speech/some.wav
    python predict.py --device esp32 --port COM12          (live)

The live path uses NoiseFloor incrementally rather than over the whole
recording, because on a stream you do not have the future. That is the one
place where training and running genuinely differ, so it is worth
understanding: offline the background is measured over everything; live it is
measured over the last eight seconds. Until that history fills up the score
is marked (cold) and should not be trusted - about three seconds after
power-on.

Decisions are smoothed before being reported. A single window is a poor
witness; two out of three consecutive windows is a much better one, and it
costs one line.
"""

import argparse
import collections
import os
import sys

import numpy as np
from scipy.io import wavfile
import joblib

import features3 as F


def load_model(path):
    b = joblib.load(path)
    if b.get("feature_names") != F.FEATURE_NAMES:
        sys.exit(f"model expects {b.get('feature_names')}, "
                 f"features3.py provides {F.FEATURE_NAMES}. Retrain.")
    return b


class Detector:
    def __init__(self, bundle, n_of=2, m_of=3, hangover=3):
        self.pipe = bundle["pipeline"]
        self.thr = bundle["threshold"]
        self.win = int(bundle["window_sec"] * F.SAMPLE_RATE)
        self.hop = int(bundle["hop_sec"] * F.SAMPLE_RATE)
        self.nf = F.NoiseFloor()
        self.buf = np.zeros(0)
        self.votes = collections.deque(maxlen=m_of)
        self.n_of, self.hangover, self.hold = n_of, hangover, 0
        self.t = 0.0

    def push(self, samples):
        self.buf = np.concatenate([self.buf, F.highpass(samples)])
        while len(self.buf) >= self.win:
            w = self.buf[:self.win]
            self.nf.update(w)                       # history first, then score
            f = F.extract(w, self.nf.value).reshape(1, -1)
            p = float(self.pipe.predict_proba(f)[0, 1])
            self.votes.append(p >= self.thr)
            if sum(self.votes) >= self.n_of:
                self.hold = self.hangover
            elif self.hold > 0:
                self.hold -= 1
            yield self.t, p, self.hold > 0, self.nf.ready, f[0]
            self.buf = self.buf[self.hop:]
            self.t += self.hop / F.SAMPLE_RATE


def run_wav(det, path):
    rate, x = wavfile.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if rate != F.SAMPLE_RATE:
        sys.exit(f"{path} is {rate} Hz, expected {F.SAMPLE_RATE}")
    x = x.astype(np.float64) / 32768.0

    print(f"{'time':>7}{'harm':>8}{'vbr':>8}{'snr':>8}{'score':>8}  state")
    events, active = [], False
    for t, p, on, ready, feats in det.push(x):
        state = "DETECT" if on else ""
        if not ready:
            state += " (cold)"
        print(f"{t:7.2f}{feats[0]:8.3f}{feats[1]:8.3f}{feats[2]:8.1f}"
              f"{p:8.3f}  {state}")
        if on and not active:
            events.append([t, None])
        if not on and active and events:
            events[-1][1] = t
        active = on
    if active and events:
        events[-1][1] = det.t

    print(f"\n{len(events)} detection event(s)")
    for a, b in events:
        print(f"  {a:6.2f}s - {(b if b else det.t):6.2f}s")


def run_live(det, port, device):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "Recording_Via_IOT_Devices"))
    import devices
    from record import FrameReader
    try:
        import serial
    except ImportError:
        sys.exit("pyserial missing.  pip install pyserial")

    prof = devices.get(device)
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, prof["baud"], 1
    ser.dtr = ser.rts = False
    ser.open()
    reader = FrameReader(prof["format"])
    print(f"listening on {port} @ {prof['baud']}. Ctrl-C to stop.\n")
    try:
        while True:
            data = ser.read(2048)
            if not data:
                continue
            for block in reader.feed(data):
                for t, p, on, ready, feats in det.push(block.astype(np.float64) / 32768.0):
                    bar = "#" * int(np.clip(p * 30, 0, 30))
                    tag = "DETECT" if on else "      "
                    cold = "" if ready else " cold"
                    print(f"\r{t:7.1f}s  harm={feats[0]:.2f} vbr={feats[1]:.2f} "
                          f"snr={feats[2]:5.1f}  [{bar:<30}] {p:.2f} {tag}{cold}   ",
                          end="", flush=True)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        ser.close()


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--model", default=os.path.join(here, "vad3_model.joblib"))
    ap.add_argument("--wav")
    ap.add_argument("--port")
    ap.add_argument("--device", default="esp32")
    ap.add_argument("--threshold", type=float)
    args = ap.parse_args()

    if not os.path.exists(args.model):
        sys.exit(f"no model at {args.model}. Run train_basic.py first.")
    bundle = load_model(args.model)
    if args.threshold is not None:
        bundle["threshold"] = args.threshold
    det = Detector(bundle)
    print(f"model: {F.FEATURE_NAMES}, threshold={bundle['threshold']:.3f}\n")

    if args.wav:
        run_wav(det, args.wav)
    elif args.port:
        run_live(det, args.port, args.device)
    else:
        ap.error("give --wav or --port")


if __name__ == "__main__":
    main()
