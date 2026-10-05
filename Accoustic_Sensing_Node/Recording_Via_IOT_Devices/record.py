"""
record.py - capture audio from one microphone on any of the three boards,
or from a 4-mic K210 array that cycles through mics sequentially.

    python record.py --list
    python record.py --device esp32 --port COM12 --label speech --minutes 2
    python record.py --device k210  --port COM5  --label non_speech --minutes 2

    # 4-mic sequential array firmware (k210_4mic_sequential.c):
    python record.py --device k210 --port COM5 --label calib \
        --minutes 4 --split-on-mic-switch

Writes 16-bit mono WAV into ../INMP_441_Dataset/<label>/ with a filename that
carries the metadata you will need later:

    spk02_room-lab_set1_d2m_speech_20260902_143012.wav

With --split-on-mic-switch, one WAV per mic is written instead of one
continuous file, named the same way with _mic0/_mic1/_mic2/_mic3 inserted -
audio recorded while no switch marker has been seen yet (there shouldn't be
any, since the firmware announces mic 0 before sending its first frame) is
filed under _micUNKNOWN so it's never silently discarded or misattributed.

Encode the metadata NOW. Re-grouping a dataset later is a rename; re-recording
it is an afternoon. The earlier dataset called everything speech_001.wav in
both folders and there was no way to split by speaker or room afterwards.

While recording it prints a live level meter and, more importantly, a running
count of dropped frames. If that number is climbing, the link is losing data
and the recording is not trustworthy - stop and fix it rather than finding out
weeks later.
"""

import argparse
import os
import re
import sys
import time
import wave
from collections import defaultdict
from datetime import datetime

import numpy as np

import devices

MAGIC = b"\x5A\xA5"
MIC_SWITCH_RE = re.compile(r"#switch mic=(\d+)")


class FrameReader:
    """Pulls framed int16 audio out of a byte stream.

    Resynchronisation is the whole point. If bytes go missing, we scan
    forward for the magic word and carry on, having counted what was lost.
    Compare that with unframed PCM, where the same event silently shifts the
    sample boundary and every subsequent sample is garbage.

    For the k210 format, each yielded frame is also tagged with whichever
    mic the firmware last announced via a "#switch mic=N" text line. That
    tagging happens INLINE during byte scanning (current_mic is updated the
    moment the marker text is harvested, before the buffer is scanned any
    further), not after feed() returns a whole batch - a single serial read
    can contain the tail of one mic's frames, the switch marker, and the
    start of the next mic's frames all in one chunk, and tagging after the
    fact would misattribute every frame in that chunk to the wrong mic.
    """

    def __init__(self, fmt):
        self.fmt = fmt
        self.buf = bytearray()
        self.dropped = 0        # frames lost, inferred from sequence gaps
        self.bad_crc = 0
        self.resyncs = 0
        self.frames = 0
        self.last_seq = None
        self.text = []          # '#' diagnostic lines from the firmware
        self.current_mic = None  # last mic announced via "#switch mic=N"

    # Sentinel meaning "this candidate was rejected, try again immediately".
    # Distinct from None, which means "need more bytes". Conflating the two
    # made one bad frame stop parsing until the next read arrived.
    RETRY = object()

    def feed(self, data):
        """Returns (samples_list, mic_ids_list), same length, same order.

        mic_ids entries are None for formats/frames with no mic-switch
        marker in play (single-mic firmware, non-k210 formats) - callers
        that don't care about mic identity can ignore the second list.
        """
        self.buf.extend(data)
        out = []
        mic_ids = []
        while True:
            got = self._one()
            if got is None:
                break
            if got is FrameReader.RETRY:
                continue
            out.append(got)
            mic_ids.append(self.current_mic)
        return out, mic_ids

    def _one(self):
        if self.fmt == "raw":
            n = len(self.buf) // 2 * 2
            if n == 0:
                return None
            s = np.frombuffer(bytes(self.buf[:n]), dtype="<i2")
            del self.buf[:n]
            self.frames += 1
            return s

        if self.fmt == "k210":
            return self._one_k210()

        head = 6 if self.fmt == "v2" else 4
        i = self.buf.find(MAGIC)
        if i < 0:
            # Keep the tail in case a magic word straddles the boundary.
            if len(self.buf) > 4096:
                self._harvest_text(self.buf[:-1])
                del self.buf[:-1]
            return None
        if i > 0:
            self._harvest_text(self.buf[:i])
            del self.buf[:i]
            self.resyncs += 1
        if len(self.buf) < head:
            return None

        if self.fmt == "v2":
            seq = self.buf[2] | (self.buf[3] << 8)
            count = self.buf[4] | (self.buf[5] << 8)
        else:
            seq = None
            count = self.buf[2] | (self.buf[3] << 8)

        if count == 0 or count > 8192:
            del self.buf[:2]
            self.resyncs += 1
            return FrameReader.RETRY

        total = head + count * 2 + (1 if self.fmt == "v2" else 0)
        if len(self.buf) < total:
            return None

        payload = bytes(self.buf[head:head + count * 2])

        if self.fmt == "v2":
            x = 0
            for b in self.buf[2:6]:
                x ^= b
            for b in payload:
                x ^= b
            if x != self.buf[total - 1]:
                self.bad_crc += 1
                del self.buf[:2]
                self.resyncs += 1
                return FrameReader.RETRY
            if self.last_seq is not None:
                gap = (seq - self.last_seq - 1) & 0xFFFF
                if gap:
                    self.dropped += gap
            self.last_seq = seq

        del self.buf[:total]
        self.frames += 1
        return np.frombuffer(payload, dtype="<i2")

    def _one_k210(self):
        """[0x5A][0xA5][seq8][blk16][nch8][int16 x blk*nch][sum16]

        Four checks before a candidate is accepted, because the magic word on
        its own is not a sync point: 0x5A 0xA5 turns up by chance inside int16
        audio in roughly 1.6% of blocks. Locking onto a false one at an odd
        byte offset would shift every sample boundary afterwards - the exact
        failure that produced three unusable files in the first dataset.
        """
        HEAD, TAIL = 6, 2
        i = self.buf.find(MAGIC)
        if i < 0:
            if len(self.buf) > 8192:
                self._harvest_text(self.buf[:-1])
                del self.buf[:-1]
            return None
        if i > 0:
            self._harvest_text(self.buf[:i])
            del self.buf[:i]
            self.resyncs += 1
        if len(self.buf) < HEAD:
            return None

        seq = self.buf[2]
        blk = self.buf[3] | (self.buf[4] << 8)
        nch = self.buf[5]

        # check 1 and 2: the shape fields have to be plausible
        if not (1 <= blk <= 8192) or not (1 <= nch <= 8):
            del self.buf[:2]
            self.resyncs += 1
            return FrameReader.RETRY

        total = HEAD + blk * nch * 2 + TAIL
        if len(self.buf) < total:
            return None

        payload = bytes(self.buf[HEAD:HEAD + blk * nch * 2])

        # check 3: byte-sum checksum, low byte first
        want = self.buf[total - 2] | (self.buf[total - 1] << 8)
        got = sum(payload) & 0xFFFF
        if got != want:
            self.bad_crc += 1
            del self.buf[:2]
            self.resyncs += 1
            return FrameReader.RETRY

        # check 4: the sequence number must advance by exactly one.
        # It is a single byte, so it wraps at 256 - compare modulo 256.
        if self.last_seq is not None:
            gap = (seq - self.last_seq - 1) & 0xFF
            if gap:
                self.dropped += gap
        self.last_seq = seq

        del self.buf[:total]
        self.frames += 1
        samples = np.frombuffer(payload, dtype="<i2")
        if nch > 1:
            # Firmware sending more than one channel: keep the first. The
            # single-mic firmware sends nch=1 so this never fires.
            samples = samples.reshape(-1, nch)[:, 0].copy()
        return samples

    def _harvest_text(self, chunk):
        try:
            s = bytes(chunk).decode("ascii", errors="ignore")
        except Exception:
            return
        for line in s.splitlines():
            line = line.strip()
            if line.startswith("#"):
                self.text.append(line)
                m = MIC_SWITCH_RE.search(line)
                if m:
                    self.current_mic = int(m.group(1))


COMMON_BAUDS = [115200, 230400, 460800, 921600, 1500000, 2000000]


def ask(prompt, options, default=0):
    """Numbered menu. Enter takes the default."""
    print(f"\n{prompt}")
    for i, o in enumerate(options):
        mark = " (default)" if i == default else ""
        print(f"  {i+1}. {o}{mark}")
    raw = input("choice: ").strip()
    if not raw:
        return default
    try:
        i = int(raw) - 1
        if 0 <= i < len(options):
            return i
    except ValueError:
        pass
    print("  not a listed option, using the default")
    return default


def interactive(args):
    """Walk through the settings instead of remembering the flags.

    Recording sessions are long and repetitive, and a mistyped --label or a
    forgotten --speaker is only discovered when the training split turns out
    to be meaningless. This asks once and builds the filename correctly.
    """
    names = sorted(devices.DEVICES)
    i = ask("Which board?", [f"{n} - {devices.DEVICES[n]['label']}" for n in names])
    args.device = names[i]
    prof = devices.get(args.device)

    args.port = input(f"\nSerial port (e.g. COM12): ").strip() or args.port

    bauds = sorted(set(COMMON_BAUDS + [prof["baud"]]))
    default = bauds.index(prof["baud"])
    j = ask(f"Baud rate?  (firmware default for {args.device} is {prof['baud']})",
            [str(b) for b in bauds], default)
    args.baud = bauds[j]
    if args.baud != prof["baud"]:
        print(f"  note: this must match the BAUD #define in the firmware, "
              f"or you get nothing but noise")

    k = ask("Recording what?", ["speech", "non_speech", "other (type it)"])
    args.label = ["speech", "non_speech", None][k] or \
        (input("  label: ").strip() or "speech")

    args.speaker = int(input("\nSpeaker number [1]: ").strip() or 1)
    args.room = input("Room name [lab]: ").strip() or "lab"
    args.set = int(input("Set number [1]: ").strip() or 1)
    args.distance = input("Distance (e.g. 0m5, 2m, 5m) []: ").strip()
    args.minutes = float(input("Minutes [2]: ").strip() or 2)
    return args


def build_name(a, mic_suffix=None):
    parts = [f"spk{a.speaker:02d}", f"room-{a.room}", f"set{a.set}"]
    if a.distance:
        parts.append(f"d{a.distance}")
    parts.append(a.label)
    if mic_suffix is not None:
        parts.append(mic_suffix)
    parts.append(datetime.now().strftime("%Y%m%d_%H%M%S"))
    return "_".join(parts) + ".wav"


def meter(x):
    if len(x) == 0:
        return 0.0, "".ljust(28)
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    db = 20 * np.log10(rms / 32768.0 + 1e-9)
    filled = int(np.clip((db + 60) / 60 * 28, 0, 28))
    return db, ("#" * filled).ljust(28)


def write_wav(path, audio, sr):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show known devices and exit")
    ap.add_argument("--device", default="esp32", help="esp32 | k210 | stm32")
    ap.add_argument("--port", help="COM12 on Windows, /dev/ttyUSB0 on Linux")
    ap.add_argument("--baud", type=int, help="override the profile's baud rate")
    ap.add_argument("--format", choices=["v1", "v2", "raw", "k210"], help="override framing")
    ap.add_argument("--label", default="speech", help="speech | non_speech | <your class>")
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument("--speaker", type=int, default=1)
    ap.add_argument("--room", default="unknown")
    ap.add_argument("--set", type=int, default=1)
    ap.add_argument("--distance", default="", help="e.g. 0m5, 2m, 5m")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("-i", "--interactive", action="store_true",
                    help="prompt for every setting instead of passing flags")
    ap.add_argument("--split-on-mic-switch", action="store_true",
                    help="write one WAV per mic, split on '#switch mic=N' "
                         "markers, instead of one continuous file. Only "
                         "meaningful with the 4-mic sequential k210 firmware.")
    args = ap.parse_args()

    if args.interactive:
        args = interactive(args)

    if args.list:
        print(devices.describe())
        print("\nPer-device notes:")
        for k, v in sorted(devices.DEVICES.items()):
            print(f"  {k}: {v['notes']}")
        return

    if not args.port:
        ap.error("--port is required (try --list first)")

    try:
        import serial
    except ImportError:
        sys.exit("pyserial missing.  pip install pyserial")

    prof = devices.get(args.device)
    baud = args.baud or prof["baud"]
    fmt = args.format or prof["format"]
    sr = prof["sample_rate"]

    if args.split_on_mic_switch and fmt != "k210":
        print(f"  note: --split-on-mic-switch expects the k210 frame format "
              f"(got '{fmt}') - the switch marker is only meaningful "
              f"alongside that framing. Proceeding, but you will likely "
              f"just get one 'micUNKNOWN' file.")

    outdir = args.outdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "INMP_441_Dataset", args.label)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)

    print(f"device : {prof['label']}")
    print(f"link   : {args.port} @ {baud} baud, frame format {fmt}")
    print(f"output : {outdir}  "
          f"({'one WAV per mic' if args.split_on_mic_switch else 'single WAV'})")
    if fmt == "raw":
        print("WARNING: unframed mode. A single dropped byte corrupts everything\n"
              "         after it, and nothing here can detect that. Use v2 firmware.")

    ser = serial.Serial()
    ser.port = args.port
    ser.baudrate = baud
    ser.timeout = 1
    # Both of these must be off BEFORE the port opens. Asserting them resets
    # the ESP32 and drops the K210 into its ISP bootloader, where it sends
    # nothing at all and the capture reads zero bytes forever.
    ser.dtr = prof.get("dtr", False)
    ser.rts = prof.get("rts", False)
    try:
        ser.open()
    except serial.SerialException as e:
        sys.exit(f"could not open {args.port}: {e}")

    reader = FrameReader(fmt)
    chunks = []                       # used when NOT splitting
    mic_chunks = defaultdict(list)    # used when splitting; key=mic id or "UNKNOWN"
    n_samples = 0
    want = int(args.minutes * 60 * sr)
    t0 = time.time()
    last_print = 0.0
    shown_text = 0

    print("\nrecording - Ctrl-C to stop early\n")
    try:
        while n_samples < want:
            data = ser.read(4096)
            if not data:
                continue
            blocks, mic_ids = reader.feed(data)
            for block, mic_id in zip(blocks, mic_ids):
                n_samples += len(block)
                if args.split_on_mic_switch:
                    key = mic_id if mic_id is not None else "UNKNOWN"
                    mic_chunks[key].append(block)
                else:
                    chunks.append(block)

            while shown_text < len(reader.text):
                print("  " + reader.text[shown_text])
                shown_text += 1

            now = time.time()
            if now - last_print > 0.25:
                last_print = now
                if args.split_on_mic_switch:
                    last_block = None
                    if mic_chunks:
                        last_key = max(mic_chunks, key=lambda k: len(mic_chunks[k]))
                        if mic_chunks[last_key]:
                            last_block = mic_chunks[last_key][-1]
                    db, bar = meter(last_block if last_block is not None else np.zeros(0))
                    mic_label = f"mic={reader.current_mic}" if reader.current_mic is not None else "mic=?"
                else:
                    db, bar = meter(chunks[-1] if chunks else np.zeros(0))
                    mic_label = ""
                secs = n_samples / sr
                print(f"\r  {secs:6.1f}s / {args.minutes*60:.0f}s  [{bar}] "
                      f"{db:6.1f} dBFS  {mic_label}  frames={reader.frames}  "
                      f"dropped={reader.dropped}  badcrc={reader.bad_crc}  "
                      f"resync={reader.resyncs}   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\n  stopped early")
    finally:
        ser.close()

    if not chunks and not mic_chunks:
        sys.exit("\nno audio received. Check the port, the baud rate, and that "
                 "the firmware is actually running (its '#' lines should appear "
                 "above).")

    print()

    if args.split_on_mic_switch:
        for key in sorted(mic_chunks, key=lambda k: (isinstance(k, str), k)):
            audio = np.concatenate(mic_chunks[key])
            suffix = f"mic{key}" if isinstance(key, int) else f"mic{key}"
            path = os.path.join(outdir, build_name(args, mic_suffix=suffix))
            write_wav(path, audio, sr)
            peak = int(np.max(np.abs(audio))) if len(audio) else 0
            print(f"wrote {path}")
            print(f"  {len(audio)/sr:.1f} s  peak={peak} "
                  f"({20*np.log10(peak/32768+1e-9):.1f} dBFS)")
            if key == "UNKNOWN":
                print("  [!] this segment was recorded before any '#switch mic=' "
                      "marker was seen - check it isn't just boot-time noise "
                      "before including it in a dataset.")
    else:
        audio = np.concatenate(chunks)
        path = os.path.join(outdir, build_name(args))
        write_wav(path, audio, sr)
        peak = int(np.max(np.abs(audio))) if len(audio) else 0
        print(f"wrote {path}")
        print(f"  {len(audio)/sr:.1f} s  peak={peak} ({20*np.log10(peak/32768+1e-9):.1f} dBFS)")

    elapsed = time.time() - t0
    measured = n_samples / elapsed if elapsed > 0 else 0

    print(f"\nframes={reader.frames}  dropped={reader.dropped}  "
          f"bad checksum={reader.bad_crc}  resyncs={reader.resyncs}")
    print(f"throughput {measured:.0f} samples/s against a nominal {sr}")

    if reader.dropped:
        print("\n  [!] frames were dropped. Lower the baud rate or shorten the "
              "\n      USB cable before recording anything you intend to keep.")


if __name__ == "__main__":
    main()