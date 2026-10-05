"""
api_server.py - reads the K210 detector over serial, serves JSON over HTTP.

    python api_server.py --port COM12
    python api_server.py --replay capture.bin       (no hardware needed)

Then open dashboard.html, or call the API yourself:

    GET /api/detections   recent events, newest first
    GET /api/state        current bearing, score, level, link health
    GET /api/health       is the device talking

ONE BEARING PER UPDATE, MANY CIRCLES OVER TIME

A 4-microphone array at 40 mm spacing has a single main lobe covering the
whole plane - at 1 kHz the aperture is 0.16 of a wavelength. Two sounds
happening at the same instant do not appear as two peaks; they merge into
one blob at the energy-weighted average of their bearings.

So the device reports ONE bearing per update. The several circles you see on
screen are detections accumulated over the last few seconds, each fading as
it ages. That is an honest picture of a scene building up over time, and it
is what the hardware can actually support. Claiming simultaneous multi-
source separation from this array would not survive a question about it.

Each detection carries: bearing, amplitude (drives circle size), a
human/non-human class from the VAD, and a confidence.
"""

import argparse
import json
import struct
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

DOA_MAGIC = b"\x5A\xA6\x10"     # deliberately NOT the audio magic
AUDIO_MAGIC = b"\x5A\xA5"
PKT = 20     # grew from 16 when lag_x/lag_y were added

state = {
    "connected": False,
    "diagnosis": "waiting for data",
    "threshold": 2.39,        # VAD_LOGIT_THRESHOLD; adjustable at runtime
    "last_packet_time": 0.0,
    "angle": None,
    "confidence": 0.0,
    "score": 0.0,
    "level_db": -99.0,
    "speech": False,
    "warm": False,
    "packets": 0,
    "dropped": 0,
    "audio_frames": 0,
    "device_lines": [],
}
detections = deque(maxlen=200)
lock = threading.Lock()


class StreamDecoder:
    """Pulls DOA packets out of a stream that also carries audio frames.

    The two use different magic words on purpose. Sharing one and switching
    on a type byte would collide whenever an audio sequence number happened
    to equal that type value - rare enough to survive testing and appear
    once, unreproducibly, during a demo.
    """

    def __init__(self):
        self.buf = bytearray()
        self.last_seq = None
        self.dropped = 0
        self.audio_frames = 0
        self.wav = None
        self.text = []

    def feed(self, data):
        self.buf.extend(data)
        out = []
        while True:
            i = self.buf.find(DOA_MAGIC)
            if i < 0:
                if len(self.buf) > 8192:
                    self._scan_audio(self.buf[:-2])
                    self._text(self.buf[:-2])
                    del self.buf[:-2]
                break
            if i > 0:
                self._scan_audio(self.buf[:i])
                self._text(self.buf[:i])
                del self.buf[:i]
            if len(self.buf) < PKT:
                break

            p = bytes(self.buf[:PKT])
            x = 0
            for b in p[2:18]:
                x ^= b
            if x != p[18]:
                del self.buf[:2]        # false magic inside audio; step past
                continue

            seq = p[3]
            if self.last_seq is not None:
                gap = (seq - self.last_seq - 1) & 0xFF
                if gap:
                    self.dropped += gap
            self.last_seq = seq

            a10, c1k, s100, l10 = struct.unpack("<hhhh", p[4:12])
            lx, ly = struct.unpack("<hh", p[14:18])
            out.append({
                "seq": seq,
                "lag_x": lx / 100.0,
                "lag_y": ly / 100.0,
                "angle": None if a10 <= -10 else a10 / 10.0,
                "confidence": c1k / 1000.0,
                "score": s100 / 100.0,
                "level_db": l10 / 10.0,
                "speech": bool(p[12]),
                "warm": bool(p[13]),
            })
            del self.buf[:PKT]
        return out

    def _scan_audio(self, chunk):
        """Count audio frames, and write them to a WAV if one is open.

        The Detector streams one microphone alongside the bearings, so the
        audio is already arriving - there is no reason to make you run a
        second tool on the same port to hear it. Validating the checksum
        before writing means a corrupt frame is dropped rather than
        appearing as a full-scale click."""
        b = bytes(chunk)
        i = 0
        while True:
            i = b.find(AUDIO_MAGIC, i)
            if i < 0 or i + 6 > len(b):
                break
            blk = b[i + 3] | (b[i + 4] << 8)
            nch = b[i + 5]
            end = i + 6 + blk * nch * 2 + 2
            if not (1 <= blk <= 8192 and 1 <= nch <= 8) or end > len(b):
                i += 2
                continue
            payload = b[i + 6:i + 6 + blk * nch * 2]
            want = b[end - 2] | (b[end - 1] << 8)
            if (sum(payload) & 0xFFFF) == want:
                self.audio_frames += 1
                if self.wav is not None:
                    self.wav.writeframes(payload)
                i = end
            else:
                i += 2

    def _text(self, chunk):
        try:
            s = bytes(chunk).decode("ascii", errors="ignore")
        except Exception:
            return
        for line in s.splitlines():
            line = line.strip()
            if not line.startswith("#") or len(line) < 2:
                continue
            # Audio is binary, so '#' (0x23) turns up in it constantly and the
            # bytes after it are arbitrary. Require the whole line to be
            # printable ASCII before believing it is a device message -
            # otherwise the log fills with noise and hides the real lines.
            body = line[1:]
            if all(32 <= ord(ch) < 127 for ch in body) and len(body) < 120:
                self.text.append(line)


def amplitude_from_db(db):
    """Map dBFS to 0..1 for circle size. -60 dB is silence, -6 is loud."""
    return max(0.0, min(1.0, (db + 60.0) / 54.0))


def record(det):
    """A detection is worth plotting only if it has a bearing AND the
    device has warmed up. Before warm-up the noise floor is still filling
    and the score means nothing - plotting those would fill the screen with
    confident-looking nonsense in the first few seconds."""
    with lock:
        state.update({
            "connected": True,
            "last_packet_time": time.time(),
            "angle": det["angle"],
            "confidence": det["confidence"],
            "score": det["score"],
            "level_db": det["level_db"],
            "lag_x": det.get("lag_x", 0.0),
            "lag_y": det.get("lag_y", 0.0),
            "speech": det["score"] >= state["threshold"],
            "device_speech": det["speech"],
            "warm": det["warm"],
        })
        state["packets"] += 1
        if det["angle"] is not None and det["warm"]:
            detections.appendleft({
                "t": time.time(),
                "angle": det["angle"],
                "amplitude": amplitude_from_db(det["level_db"]),
                "level_db": det["level_db"],
                "confidence": det["confidence"],
                "score": det["score"],
                # Classify HOST-side from the raw score, not from the
                # device's speech flag. The threshold baked into
                # vad3_model.h came from a 16-file dataset that still fails
                # the confound probe, so it is not a number to trust as
                # final - and reflashing to try another value is a terrible
                # way to tune anything.
                "klass": "human" if det["score"] >= state["threshold"] else "non_human",
            })


def diagnose(dec, st):
    """Name the actual problem rather than leaving "no device" on screen.

    The failure that keeps happening is running the Recorder firmware and
    pointing this at it. Audio frames arrive, DOA packets never do, and the
    only symptom is a zero counter - which looks identical to a dead link.
    Audio arriving with no DOA packets is unambiguous, so say so.
    """
    if dec.audio_frames > 0 and st["packets"] == 0:
        return ("RECORDER firmware is running - it streams audio, not bearings. "
                "Rebuild with -DPROJ=Detector and reflash.")
    if st["packets"] == 0 and dec.audio_frames == 0:
        return ("bytes arriving but nothing recognised - check the baud rate "
                "matches BAUD_RATE in the firmware")
    if st["packets"] > 0 and not st["warm"]:
        return "device warming up - the noise floor needs about 8 s"
    return "ok"


def serial_worker(port, baud, capture_path=None, wav_path=None):
    import serial
    import wave
    dec = StreamDecoder()
    cap = open(capture_path, "wb") if capture_path else None
    if wav_path:
        w = wave.open(wav_path, "wb")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        dec.wav = w
        print(f"writing audio to {wav_path}")
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, 0.3
    # Both off BEFORE open, or the K210 drops into ISP and sends nothing.
    ser.dtr = ser.rts = False
    ser.open()
    print(f"serial: {port} @ {baud}")
    try:
        while True:
            data = ser.read(2048)
            if cap and data:
                cap.write(data)
                cap.flush()
            if not data:
                with lock:
                    if time.time() - state["last_packet_time"] > 3.0:
                        state["connected"] = False
                continue
            for d in dec.feed(data):
                record(d)
            with lock:
                state["dropped"] = dec.dropped
                state["audio_frames"] = dec.audio_frames
                state["device_lines"] = dec.text[-10:]
                state["diagnosis"] = diagnose(dec, state)
    finally:
        ser.close()
        if cap:
            cap.close()
        if dec.wav:
            dec.wav.close()
            print(f"wrote {wav_path}")


def demo_worker():
    """Synthesise plausible detections. No hardware, no capture file.

    This exists because rehearsing a demo should not depend on the bench
    being wired up. It produces the same packets the device would, so the
    dashboard code path is identical - if it looks right here it will look
    right on hardware.
    """
    import math
    import random
    scene = [
        # bearing, drift/sec, class, base level dB, how often it speaks
        (45.0,   0.6,  True,  -14.0, 0.55),   # a person, moving slowly
        (200.0, -0.3,  True,  -20.0, 0.35),   # a second person, quieter
        (300.0,  0.0,  False, -26.0, 0.30),   # machinery, fixed bearing
    ]
    t0 = time.time()
    seq = 0
    with lock:
        state["device_lines"] = ["#DEMO MODE - synthetic data, no device attached"]
    while True:
        now = time.time() - t0
        warm = now > 4.0          # mimic the real noise-floor warm-up
        for base, drift, human, lvl, prob in scene:
            if random.random() > prob:
                continue
            ang = (base + drift * now + random.gauss(0, 2.5)) % 360.0
            level = lvl + random.gauss(0, 3.0)
            record({
                "seq": seq & 0xFF,
                "angle": ang if warm else None,
                "confidence": random.uniform(0.45, 0.85),
                "score": random.uniform(3.0, 9.0) if human else random.uniform(-6.0, 1.0),
                "level_db": level,
                "speech": human,
                "warm": warm,
            })
            seq += 1
        time.sleep(0.35)


def replay_worker(path, rate):
    """Replay a captured stream at wall-clock speed, so the dashboard can be
    demonstrated and debugged with no hardware attached."""
    dec = StreamDecoder()
    data = open(path, "rb").read()
    print(f"replaying {len(data)} bytes from {path}")
    step = 2048
    while True:
        for i in range(0, len(data), step):
            for d in dec.feed(data[i:i + step]):
                record(d)
            with lock:
                state["dropped"] = dec.dropped
                state["audio_frames"] = dec.audio_frames
                state["device_lines"] = dec.text[-10:]
            time.sleep(step / float(rate))
        with lock:
            detections.clear()


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")  # local page
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/detections":
            now = time.time()
            with lock:
                items = [dict(d, age=now - d["t"]) for d in detections]
            self._send({"detections": items, "count": len(items)})
        elif path == "/api/state":
            with lock:
                self._send(dict(state))
        elif path.startswith("/api/threshold"):
            q = self.path.split("?", 1)
            if len(q) == 2 and q[1].startswith("v="):
                try:
                    with lock:
                        state["threshold"] = float(q[1][2:])
                except ValueError:
                    pass
            with lock:
                self._send({"threshold": state["threshold"]})
        elif path == "/api/health":
            with lock:
                ok = state["connected"] and time.time() - state["last_packet_time"] < 3
                self._send({"ok": ok, "packets": state["packets"],
                            "dropped": state["dropped"]})
        elif path in ("/", "/dashboard.html"):
            try:
                body = open("dashboard.html", "rb").read()
            except OSError:
                self._send({"error": "dashboard.html not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send({"error": "not found"}, 404)

    def log_message(self, *a):
        pass          # keep the console clear for device output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=1500000)
    ap.add_argument("--replay", help="a captured raw stream (see --capture)")
    ap.add_argument("--capture", metavar="FILE",
                    help="dump the raw serial stream to FILE while running, "
                         "so you can replay this session later")
    ap.add_argument("--wav", metavar="FILE",
                    help="save the streamed microphone audio to a WAV, so you "
                         "can listen to what the detector is actually hearing")
    ap.add_argument("--demo", action="store_true",
                    help="synthetic data, no hardware and no capture file")
    ap.add_argument("--replay-rate", type=int, default=33000,
                    help="bytes/sec, roughly the real link rate")
    ap.add_argument("--http-port", type=int, default=8000)
    args = ap.parse_args()

    if args.demo:
        t = threading.Thread(target=demo_worker, daemon=True)
    elif args.replay:
        t = threading.Thread(target=replay_worker,
                             args=(args.replay, args.replay_rate), daemon=True)
    elif args.port:
        t = threading.Thread(target=serial_worker,
                             args=(args.port, args.baud, args.capture, args.wav),
                             daemon=True)
    else:
        ap.error("give --port, --replay or --demo")
    t.start()

    srv = HTTPServer(("0.0.0.0", args.http_port), Handler)
    print(f"http://localhost:{args.http_port}/   (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
