"""
detector_monitor.py - read and display the Detector firmware's output.

    python detector_monitor.py --port COM12
    python detector_monitor.py --file capture.bin        (offline replay)
    python detector_monitor.py --port COM12 --csv log.csv

The Detector build does not send audio. It sends a 16-byte result packet,
so record.py cannot read it - record.py only knows the audio frame formats.
This is the tool for that stream.

PACKET LAYOUT (matches send_report in main_detector.c)

    0-1   magic 0x5A 0xA5
    2     type 0x10
    3     seq, wraps at 256
    4-5   angle x10,      int16 LE, -10 means no confident bearing
    6-7   confidence x1000, int16 LE
    8-9   vad score x100, int16 LE   (the SMOOTHED logit, not a probability)
    10-11 level dBFS x10, int16 LE
    12    speech flag
    13    warm flag - 0 while the noise floor is still filling
    14    xor of bytes 2..13
    15    pad, keeps the length even

The magic word is shared with the audio format but the type byte differs,
so a host reading either stream can tell them apart without guessing from
the length. Sequence numbers make a dropped packet countable rather than
invisible.
"""

import argparse
import struct
import sys
import time

MAGIC = b"\x5A\xA5\x10"
PKT = 16


class Decoder:
    def __init__(self):
        self.buf = bytearray()
        self.last_seq = None
        self.dropped = 0
        self.bad_crc = 0
        self.count = 0
        self.text = []

    def feed(self, data):
        self.buf.extend(data)
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                # keep a short tail in case a magic straddles the boundary
                if len(self.buf) > 4096:
                    self._text(self.buf[:-2])
                    del self.buf[:-2]
                break
            if i > 0:
                self._text(self.buf[:i])
                del self.buf[:i]
            if len(self.buf) < PKT:
                break
            p = bytes(self.buf[:PKT])

            x = 0
            for b in p[2:14]:
                x ^= b
            if x != p[14]:
                # Not a real packet: the magic turned up inside data. Step
                # past it and keep looking rather than consuming 16 bytes
                # that might contain a genuine header.
                self.bad_crc += 1
                del self.buf[:2]
                continue

            seq = p[3]
            if self.last_seq is not None:
                gap = (seq - self.last_seq - 1) & 0xFF
                if gap:
                    self.dropped += gap
            self.last_seq = seq

            a10, c1k, s100, l10 = struct.unpack("<hhhh", p[4:12])
            out.append({
                "seq": seq,
                "angle": None if a10 <= -10 else a10 / 10.0,
                "conf": c1k / 1000.0,
                "score": s100 / 100.0,
                "level": l10 / 10.0,
                "speech": bool(p[12]),
                "warm": bool(p[13]),
            })
            self.count += 1
            del self.buf[:PKT]
        return out

    def _text(self, chunk):
        try:
            s = bytes(chunk).decode("ascii", errors="ignore")
        except Exception:
            return
        for line in s.splitlines():
            line = line.strip()
            if line.startswith("#"):
                self.text.append(line)


def compass(angle):
    """Eight-point label. Easier to sanity-check against a real room than
    a number - if you clap due east and it says W, the slot map is wrong."""
    if angle is None:
        return "  --"
    names = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
    return f"{names[int((angle + 22.5) % 360 // 45)]:>4}"


def bar(v, lo, hi, width=20):
    f = max(0.0, min(1.0, (v - lo) / (hi - lo + 1e-9)))
    return "#" * int(f * width)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=1500000)
    ap.add_argument("--file", help="replay a raw capture instead of a port")
    ap.add_argument("--csv")
    ap.add_argument("--quiet", action="store_true",
                    help="only print packets where speech is detected")
    args = ap.parse_args()

    dec = Decoder()
    csv = open(args.csv, "w") if args.csv else None
    if csv:
        csv.write("t,seq,angle,conf,score,level,speech,warm\n")

    def show(r, t):
        if args.quiet and not r["speech"]:
            return
        tag = "DETECT" if r["speech"] else "      "
        cold = "" if r["warm"] else " cold"
        ang = "  --  " if r["angle"] is None else f"{r['angle']:6.1f}"
        print(f"{t:7.1f}s {tag}{cold:5} angle={ang}{compass(r['angle'])} "
              f"conf={r['conf']:.2f} score={r['score']:+6.2f} "
              f"{r['level']:6.1f}dB [{bar(r['score'], -10, 10):<20}]")
        if csv:
            csv.write(f"{t:.2f},{r['seq']},{'' if r['angle'] is None else r['angle']},"
                      f"{r['conf']},{r['score']},{r['level']},"
                      f"{int(r['speech'])},{int(r['warm'])}\n")
            csv.flush()

    shown = 0
    t0 = time.time()

    if args.file:
        for r in dec.feed(open(args.file, "rb").read()):
            show(r, 0.0)
    else:
        if not args.port:
            ap.error("give --port or --file")
        try:
            import serial
        except ImportError:
            sys.exit("pyserial missing.  pip install pyserial")
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = args.port, args.baud, 0.5
        # Both off BEFORE open, or the K210 drops into its ISP bootloader
        # and sends nothing at all.
        ser.dtr = ser.rts = False
        ser.open()
        print(f"listening on {args.port} @ {args.baud}. Ctrl-C to stop.\n")
        try:
            while True:
                data = ser.read(512)
                if data:
                    for r in dec.feed(data):
                        show(r, time.time() - t0)
                while shown < len(dec.text):
                    print("  " + dec.text[shown])
                    shown += 1
        except KeyboardInterrupt:
            pass
        finally:
            ser.close()

    print(f"\n{dec.count} packets, {dec.dropped} dropped, "
          f"{dec.bad_crc} rejected as false magic")
    if csv:
        csv.close()
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
