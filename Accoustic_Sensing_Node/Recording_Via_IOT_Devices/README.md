# Recording_Via_IOT_Devices

```
python record.py --list
python record.py --device esp32 --port COM12 --label speech --minutes 2 \
                 --speaker 1 --room lab --set 1 --distance 2m
```

## Files

- `record.py` — the recorder. Handles all three boards.
- `devices.py` — baud rate, frame format and DTR/RTS policy per board.
  Adding a board means adding one entry here.
- `firmware/esp32_1mic/esp32_1mic.ino` — one INMP441, framed output.
- `firmware/k210_1mic/k210_1mic.c` — same, for the Kendryte SDK.

## Wire formats

ESP32 / STM32 (`v2`):
```
[0x5A][0xA5][seq16][count16][int16 payload × count][xor8]
```

K210 (`k210`):
```
[0x5A][0xA5][seq8][blk16][nch8][int16 payload × blk·nch][sum16]
```

The magic word alone is **not** a sync point: `0x5A 0xA5` occurs by chance
inside int16 audio in about 1.6% of blocks, and locking onto a false one at an
odd byte offset shifts every sample boundary afterwards. So four things are
checked before a frame is accepted — the shape fields are sane, the channel
count matches, the checksum matches, and the sequence advanced by exactly one.
A false magic fails at least one of them nearly always.

The sequence number is the point. Raw unframed PCM has no way to recover from
a dropped byte: lose one and the 16-bit sample boundary flips, so every
sample afterwards is noise at the Nyquist frequency — while the WAV header
stays valid and the file still plays. Three files in the first dataset failed
exactly this way and it took weeks to notice. With sequence numbers the
recorder **counts** what was lost and tells you while you are still standing
there.

Watch the `dropped=` counter during capture. If it climbs, stop: lower the
baud rate or shorten the USB cable before recording anything you intend to
keep.

## Two board-specific traps

**Both boards must have DTR and RTS deasserted before the port opens.**
pyserial asserts both by default. On the ESP32 that reboots the board
mid-capture; on the K210 it drops into the ISP bootloader, where it sends
nothing at all and the capture reads zero bytes forever. `devices.py` sets
both to `False`.

**The CH552/CH340 bridge on the K210 ignores the configured baud rate.**
Throughput is fixed and lower than requested regardless of what you ask for,
which makes the K210 a poor choice for streaming audio. Prefer the ESP32 for
dataset collection.

## Before trusting any audio

The firmware prints `#` diagnostic lines at boot: which I2S slot the mic is
actually in, the min/max sample values, and the measured sample rate.
`record.py` shows them. Healthy means min and max swing on both sides of
zero, not all-zero, not pinned at the rails. Slot and shift are worth
observing rather than assuming — on the earlier bring-up the datasheet alone
was not enough and both had to be settled by dumping raw words.

## K210: only one slot is transmitted

One I2S data line always carries two slots — 64 bit-clocks per frame, not
configurable. So `k210_1mic.c` still *captures* both and keeps the one the
microphone is in. That halves the payload:

| | frame | throughput | share of the 921600 link |
|---|---|---|---|
| both slots | 1032 B | 64.5 kB/s | 70% |
| one slot | 520 B | 32.5 kB/s | **35%** |

The CH552-class bridge has no flow control back to the K210, so headroom is
the only defence against drops. This is the largest reliability gain available
without changing hardware.

**Set `MIC_SLOT` from the tap test, not from the datasheet.** The diagnostic
window reads both slots and marks the one being kept with `*`. Tap the
housing: exactly one should jump. The wrong slot records the undriven line —
near-silence, valid header, plays fine, trains a useless model.

## The K210 high-pass was moved from 300 Hz to 20 Hz

The array firmware filtered at 300 Hz, which was correct there: across a
100 mm baseline a 1 m wavelength carries no usable timing information.

For single-microphone voice detection it breaks the main feature. Harmonicity
finds the pitch period, and 300 Hz sits *above* the fundamental of every adult
voice — 85–155 Hz male, 165–255 Hz female. Autocorrelation can still work off
the harmonics, but it weakens and becomes prone to octave errors.

So the firmware now removes DC and nothing else. The real 100 Hz high-pass
lives in `ML/features3.py`, where it is a line you can change rather than a
reflash.
