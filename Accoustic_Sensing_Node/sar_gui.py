"""
sar_gui.py - desktop front end for the detector.

    python sar_gui.py

Board, port, baud and model are all chosen in the window. Two modes:

  MONITOR   live detection. Score, the three feature values, input level, and
            the link health counters.
  RECORD    the same, but also writes the audio to INMP_441_Dataset so you can
            watch the meter while capturing a dataset.

The serial reading and feature extraction run in a worker thread and hand
results to the interface through a queue. Tk is not thread-safe, so the
worker never touches a widget - it only posts messages, and the interface
drains them on a timer.

WHY THE LINK COUNTERS ARE ON SCREEN

frames / dropped / resync are not decoration. A climbing dropped count means
the link is losing data, and audio captured that way is quietly corrupt while
still looking and sounding fine. Seeing it while you record is the whole point
of having put sequence numbers in the frame format.
"""

import os
import queue
import sys
import threading
import time
import wave
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "ML"))
sys.path.insert(0, os.path.join(HERE, "Recording_Via_IOT_Devices"))

try:
    import tkinter as tk
    from tkinter import ttk, filedialog
except ImportError:
    sys.exit("tkinter missing. On Windows it ships with Python; on Linux: "
             "sudo apt install python3-tk")

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG      = "#0e1116"
PANEL   = "#161a21"
PANEL2  = "#1d222b"
LINE    = "#2a313d"
TEXT    = "#e6e9ef"
MUTED   = "#8b95a5"
ACCENT  = "#4ade80"
WARN    = "#fbbf24"
ALERT   = "#f87171"
BLUE    = "#60a5fa"

FEATURE_COLORS = {
    "harmonicity": ACCENT,
    "voice_band_ratio": BLUE,
    "snr_db": WARN,
}
# Display ranges. snr_db is unbounded, so the bar saturates at 30 dB.
FEATURE_RANGE = {"harmonicity": (0, 1), "voice_band_ratio": (0, 1), "snr_db": (0, 30)}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self, cfg, out_q):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.q = out_q
        self.stop_flag = threading.Event()

    def post(self, kind, **kw):
        self.q.put((kind, kw))

    def run(self):
        try:
            import serial
            import joblib
            import devices
            from record import FrameReader
            import features3 as F
            from predict import Detector
        except Exception as e:
            self.post("error", msg=f"import failed: {e}")
            return

        cfg = self.cfg
        try:
            bundle = joblib.load(cfg["model"])
        except Exception as e:
            self.post("error", msg=f"could not load model: {e}")
            return
        if bundle.get("feature_names") != F.FEATURE_NAMES:
            self.post("error", msg=f"model expects {bundle.get('feature_names')}, "
                                   f"features3.py gives {F.FEATURE_NAMES}. Retrain.")
            return

        det = Detector(bundle)
        prof = devices.get(cfg["device"])
        reader = FrameReader(cfg["format"])

        ser = serial.Serial()
        ser.port = cfg["port"]
        ser.baudrate = cfg["baud"]
        ser.timeout = 0.3
        # Must be false BEFORE open. Asserting either resets the ESP32 and
        # drops the K210 into its ISP bootloader, where it sends nothing.
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except Exception as e:
            self.post("error", msg=f"could not open {cfg['port']}: {e}")
            return

        self.post("connected", threshold=bundle["threshold"])

        wav = None
        if cfg.get("record_path"):
            wav = wave.open(cfg["record_path"], "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(prof["sample_rate"])

        shown_text = 0
        n_samples = 0
        t0 = time.time()
        try:
            while not self.stop_flag.is_set():
                data = ser.read(4096)
                if not data:
                    continue
                for block in reader.feed(data):
                    if wav is not None:
                        wav.writeframes(block.astype("<i2").tobytes())
                    n_samples += len(block)
                    x = block.astype(np.float64) / 32768.0
                    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
                    self.post("level", db=20 * np.log10(rms + 1e-9))
                    for t, p, on, ready, feats in det.push(x):
                        self.post("score", t=t, p=p, on=on, ready=ready,
                                  feats=dict(zip(F.FEATURE_NAMES,
                                                 [float(v) for v in feats])))
                while shown_text < len(reader.text):
                    self.post("devline", line=reader.text[shown_text])
                    shown_text += 1
                self.post("stats", frames=reader.frames, dropped=reader.dropped,
                          bad=reader.bad_crc, resync=reader.resyncs,
                          secs=n_samples / prof["sample_rate"],
                          elapsed=time.time() - t0)
        except Exception as e:
            self.post("error", msg=str(e))
        finally:
            try:
                ser.close()
            except Exception:
                pass
            if wav is not None:
                wav.close()
                self.post("saved", path=cfg["record_path"], secs=n_samples / prof["sample_rate"])
            self.post("disconnected")


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
class Bar(tk.Canvas):
    """A labelled horizontal bar with a numeric readout."""

    def __init__(self, master, label, color, lo, hi, unit=""):
        super().__init__(master, height=44, bg=PANEL, highlightthickness=0)
        self.label, self.color, self.lo, self.hi, self.unit = label, color, lo, hi, unit
        self.value = lo
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, v):
        self.value = v
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = self.winfo_width() or 300
        self.create_text(2, 10, text=self.label, anchor="w", fill=MUTED,
                         font=("Segoe UI", 9))
        txt = f"{self.value:.2f}{self.unit}" if self.hi <= 1 else f"{self.value:.1f}{self.unit}"
        self.create_text(w - 2, 10, text=txt, anchor="e", fill=TEXT,
                         font=("Consolas", 11, "bold"))
        y0, y1 = 24, 36
        self.create_rectangle(0, y0, w, y1, fill=PANEL2, outline="")
        frac = (self.value - self.lo) / (self.hi - self.lo + 1e-9)
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            self.create_rectangle(0, y0, w * frac, y1, fill=self.color, outline="")


class Gauge(tk.Canvas):
    """Big score readout: an arc that fills with the probability, plus the
    threshold marked so you can see how close a decision was."""

    def __init__(self, master, size=190):
        super().__init__(master, width=size, height=size, bg=PANEL,
                         highlightthickness=0)
        self.size = size
        self.p = 0.0
        self.thr = 0.5
        self.on = False
        self.ready = False
        self.redraw()

    def set(self, p, on, ready, thr):
        self.p, self.on, self.ready, self.thr = p, on, ready, thr
        self.redraw()

    def redraw(self):
        self.delete("all")
        s = self.size
        pad = 16
        box = (pad, pad, s - pad, s - pad)
        self.create_arc(*box, start=225, extent=-270, style="arc", width=13,
                        outline=PANEL2)
        col = ACCENT if self.on else (MUTED if self.p < self.thr else WARN)
        if self.p > 0:
            self.create_arc(*box, start=225, extent=-270 * min(self.p, 1.0),
                            style="arc", width=13, outline=col)
        # threshold tick
        import math
        a = math.radians(225 - 270 * self.thr)
        cx = cy = s / 2
        r0, r1 = s / 2 - pad - 10, s / 2 - pad + 10
        self.create_line(cx + r0 * math.cos(a), cy - r0 * math.sin(a),
                         cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                         fill=ALERT, width=2)
        self.create_text(cx, cy - 12, text=f"{self.p:.2f}", fill=TEXT,
                         font=("Segoe UI", 34, "bold"))
        state = "DETECT" if self.on else "listening"
        if not self.ready:
            state = "warming up"
        self.create_text(cx, cy + 26, text=state,
                         fill=ACCENT if self.on else MUTED,
                         font=("Segoe UI", 12, "bold" if self.on else "normal"))
        self.create_text(cx, cy + 46, text=f"threshold {self.thr:.2f}",
                         fill=MUTED, font=("Segoe UI", 8))


class Sparkline(tk.Canvas):
    """Recent score history, with the threshold as a horizontal line."""

    def __init__(self, master, n=220):
        super().__init__(master, height=90, bg=PANEL, highlightthickness=0)
        self.vals = []
        self.n = n
        self.thr = 0.5
        self.bind("<Configure>", lambda e: self.redraw())

    def push(self, v, thr):
        self.thr = thr
        self.vals.append(v)
        if len(self.vals) > self.n:
            self.vals = self.vals[-self.n:]
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = self.winfo_width() or 400
        h = self.winfo_height() or 90
        self.create_text(4, 9, text="score history", anchor="w", fill=MUTED,
                         font=("Segoe UI", 9))
        ty = h - 4 - (h - 22) * self.thr
        self.create_line(0, ty, w, ty, fill=ALERT, dash=(3, 3))
        if len(self.vals) < 2:
            return
        step = w / max(len(self.vals) - 1, 1)
        pts = []
        for i, v in enumerate(self.vals):
            pts += [i * step, h - 4 - (h - 22) * max(0.0, min(1.0, v))]
        self.create_line(*pts, fill=ACCENT, width=2, smooth=True)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("SAR Acoustic Detector")
        root.configure(bg=BG)
        root.geometry("980x660")
        root.minsize(900, 620)

        self.q = queue.Queue()
        self.worker = None
        self.threshold = 0.5

        try:
            import devices
            self.devices = devices
        except Exception as e:
            self.devices = None
            print("devices.py not importable:", e)

        self._build()
        self.refresh_ports()
        self.root.after(50, self.drain)

    # -- layout ------------------------------------------------------------
    def _panel(self, parent, **kw):
        return tk.Frame(parent, bg=PANEL, **kw)

    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=LINE)

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="SAR Acoustic Detector", bg=BG, fg=TEXT,
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        self.conn_lbl = tk.Label(head, text="disconnected", bg=BG, fg=MUTED,
                                 font=("Segoe UI", 10))
        self.conn_lbl.pack(side="right")

        # ---- connection bar ----
        bar = self._panel(self.root)
        bar.pack(fill="x", padx=14, pady=6)
        inner = tk.Frame(bar, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=12)

        # Each labelled control lives in its own frame. The frame is placed
        # with grid; the widget inside is placed with pack. Mixing the two
        # managers inside ONE container is what Tk refuses to do.
        def field(col, label, pad=(0, 14)):
            f = tk.Frame(inner, bg=PANEL)
            f.grid(row=0, column=col, sticky="w", padx=pad)
            tk.Label(f, text=label, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w")
            return f

        names = sorted(self.devices.DEVICES) if self.devices else ["esp32"]
        f = field(0, "BOARD")
        self.v_device = tk.StringVar(value=names[0])
        cb_dev = ttk.Combobox(f, textvariable=self.v_device, values=names,
                              width=10, state="readonly")
        cb_dev.pack(anchor="w")
        cb_dev.bind("<<ComboboxSelected>>", self.on_device)

        f = field(1, "PORT")
        self.v_port = tk.StringVar()
        self.cb_port = ttk.Combobox(f, textvariable=self.v_port, width=16)
        self.cb_port.pack(anchor="w")

        tk.Button(inner, text="scan", command=self.refresh_ports, bg=PANEL2,
                  fg=TEXT, relief="flat", font=("Segoe UI", 8),
                  activebackground=LINE, cursor="hand2"
                  ).grid(row=0, column=2, sticky="s", pady=(0, 3), padx=(0, 14))

        f = field(3, "BAUD")
        self.v_baud = tk.StringVar(value="921600")
        self.cb_baud = ttk.Combobox(
            f, textvariable=self.v_baud, width=10,
            values=["115200", "230400", "460800", "921600", "1500000", "2000000"])
        self.cb_baud.pack(anchor="w")

        f = field(4, "MODEL")
        self.v_model = tk.StringVar(
            value=os.path.join(HERE, "ML", "vad3_model.joblib"))
        row = tk.Frame(f, bg=PANEL)
        row.pack(anchor="w")
        self.model_lbl = tk.Label(row, text="vad3_model.joblib", bg=PANEL2,
                                  fg=TEXT, font=("Consolas", 9), padx=8, pady=3)
        self.model_lbl.pack(side="left")
        tk.Button(row, text="...", command=self.pick_model, bg=PANEL2, fg=TEXT,
                  relief="flat", font=("Segoe UI", 8), cursor="hand2",
                  activebackground=LINE).pack(side="left", padx=(4, 0))

        self.v_rec = tk.BooleanVar(value=False)
        tk.Checkbutton(inner, text="save to dataset", variable=self.v_rec,
                       bg=PANEL, fg=TEXT, selectcolor=PANEL2,
                       activebackground=PANEL, activeforeground=TEXT,
                       font=("Segoe UI", 9), highlightthickness=0, bd=0
                       ).grid(row=0, column=5, sticky="s", pady=(0, 3), padx=(0, 8))

        f = field(6, "CLASS")
        self.v_label = tk.StringVar(value="speech")
        self.cb_label = ttk.Combobox(f, textvariable=self.v_label, width=11,
                                     values=["speech", "non_speech"])
        self.cb_label.pack(anchor="w")

        self.btn = tk.Button(inner, text="Connect", command=self.toggle,
                             bg=ACCENT, fg="#06240f", relief="flat",
                             font=("Segoe UI", 10, "bold"), padx=22, pady=6,
                             cursor="hand2", activebackground="#86efac")
        self.btn.grid(row=0, column=7, sticky="e", padx=(6, 0))
        inner.columnconfigure(7, weight=1)

        # ---- main body ----
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=6)

        left = self._panel(body)
        left.pack(side="left", fill="y", padx=(0, 8))
        self.gauge = Gauge(left)
        self.gauge.pack(padx=22, pady=(18, 6))
        self.level = Bar(left, "INPUT LEVEL", BLUE, -60, 0, " dBFS")
        self.level.pack(fill="x", padx=16, pady=(0, 14))
        self.level.configure(width=210)

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        feats = self._panel(right)
        feats.pack(fill="x")
        tk.Label(feats, text="FEATURES", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(10, 0))
        self.bars = {}
        for name, color in FEATURE_COLORS.items():
            lo, hi = FEATURE_RANGE[name]
            b = Bar(feats, name, color, lo, hi, " dB" if name == "snr_db" else "")
            b.pack(fill="x", padx=14, pady=(2, 4))
            self.bars[name] = b
        tk.Frame(feats, bg=PANEL, height=8).pack()

        sp = self._panel(right)
        sp.pack(fill="x", pady=(8, 0))
        self.spark = Sparkline(sp)
        self.spark.pack(fill="x", padx=10, pady=8)

        link = self._panel(right)
        link.pack(fill="x", pady=(8, 0))
        self.stats = tk.Label(link, text="frames 0    dropped 0    bad crc 0    resync 0",
                              bg=PANEL, fg=MUTED, font=("Consolas", 9),
                              anchor="w", padx=14, pady=8)
        self.stats.pack(fill="x")

        logp = self._panel(right)
        logp.pack(fill="both", expand=True, pady=(8, 0))
        tk.Label(logp, text="DEVICE OUTPUT", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(8, 2))
        self.log = tk.Text(logp, bg=PANEL2, fg=TEXT, font=("Consolas", 9),
                           height=7, relief="flat", padx=10, pady=6, wrap="none")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log.insert("end", "Pick a board and port, then Connect.\n"
                               "The firmware prints its startup checks here - read them\n"
                               "before trusting any audio.\n")
        self.log.configure(state="disabled")

        self.on_device()

    # -- helpers -----------------------------------------------------------
    def say(self, line, color=None):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_device(self, *_):
        if not self.devices:
            return
        prof = self.devices.get(self.v_device.get())
        self.v_baud.set(str(prof["baud"]))

    def pick_model(self):
        p = filedialog.askopenfilename(
            initialdir=os.path.join(HERE, "ML"),
            filetypes=[("model", "*.joblib"), ("all", "*.*")])
        if p:
            self.v_model.set(p)
            self.model_lbl.configure(text=os.path.basename(p))

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        self.cb_port.configure(values=ports)
        if ports and not self.v_port.get():
            self.v_port.set(ports[0])
        self.say(f"# {len(ports)} serial port(s): {', '.join(ports) or 'none found'}")

    # -- run ---------------------------------------------------------------
    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_flag.set()
            self.btn.configure(text="Stopping...", state="disabled")
            return

        if not self.v_port.get():
            self.say("# no port selected")
            return
        model = self.v_model.get()
        if not os.path.exists(model):
            self.say(f"# no model at {model} - run 'capstone.py train' first")
            return

        prof = self.devices.get(self.v_device.get())
        cfg = {
            "device": self.v_device.get(),
            "port": self.v_port.get(),
            "baud": int(self.v_baud.get()),
            "format": prof["format"],
            "model": model,
        }
        if self.v_rec.get():
            folder = os.path.join(HERE, "INMP_441_Dataset", self.v_label.get())
            os.makedirs(folder, exist_ok=True)
            cfg["record_path"] = os.path.join(
                folder, f"gui_{self.v_label.get()}_"
                        f"{datetime.now():%Y%m%d_%H%M%S}.wav")

        self.spark.vals = []
        self.worker = Worker(cfg, self.q)
        self.worker.start()
        self.btn.configure(text="Disconnect", bg=ALERT, fg="#2b0b0b",
                           activebackground="#fca5a5")
        self.say(f"# connecting {cfg['port']} @ {cfg['baud']} "
                 f"({cfg['device']}, frame {cfg['format']})")

    def drain(self):
        try:
            while True:
                kind, kw = self.q.get_nowait()
                self.handle(kind, kw)
        except queue.Empty:
            pass
        self.root.after(40, self.drain)

    def handle(self, kind, kw):
        if kind == "score":
            self.gauge.set(kw["p"], kw["on"], kw["ready"], self.threshold)
            self.spark.push(kw["p"], self.threshold)
            for k, v in kw["feats"].items():
                if k in self.bars:
                    self.bars[k].set(v)
        elif kind == "level":
            self.level.set(kw["db"])
        elif kind == "stats":
            self.stats.configure(
                text=f"frames {kw['frames']:<8} dropped {kw['dropped']:<6} "
                     f"bad crc {kw['bad']:<6} resync {kw['resync']:<6} "
                     f"audio {kw['secs']:.1f}s in {kw['elapsed']:.0f}s",
                fg=ALERT if kw["dropped"] else MUTED)
        elif kind == "devline":
            self.say(kw["line"])
        elif kind == "connected":
            self.threshold = kw["threshold"]
            self.conn_lbl.configure(text="connected", fg=ACCENT)
            self.say(f"# connected, threshold {kw['threshold']:.3f}")
        elif kind == "saved":
            self.say(f"# wrote {os.path.basename(kw['path'])} ({kw['secs']:.1f}s)")
        elif kind == "error":
            self.say("# ERROR " + kw["msg"])
            self.conn_lbl.configure(text="error", fg=ALERT)
        elif kind == "disconnected":
            self.conn_lbl.configure(text="disconnected", fg=MUTED)
            self.btn.configure(text="Connect", bg=ACCENT, fg="#06240f",
                               state="normal", activebackground="#86efac")
            self.gauge.set(0.0, False, False, self.threshold)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
