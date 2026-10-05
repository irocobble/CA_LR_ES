# Acoustic Survivor Detection

Detecting trapped people by sound, on a K210 at the edge. Four INMP441
microphones in a 40 mm square: a three-feature voice detector decides
*whether* someone is there, and GCC-PHAT time-difference-of-arrival decides
*which direction*. No audio leaves the device — just a 16-byte result packet.

---

## Why three features and not fifty-two

An earlier version of this project used 52 features. An ablation showed the
two largest-weighted ones could be deleted at a cost of 0.001 — they were
collinear with features already present. More features mostly bought more
ways to be wrong, and every one is another thing to reimplement in C and
verify for numerical parity.

| feature | question it answers | why it earns its place |
|---|---|---|
| `harmonicity` | does the waveform repeat? | a voice is periodic; a door slam and a packet header are not |
| `voice_band_ratio` | is the energy at 300–3400 Hz? | rejects hum, rumble and wind |
| `snr_db` | is it above this room's background? | ignores what is too quiet to matter |

All three are ratios, so microphone gain cancels. All three are a short loop
in C. `ML/features3.py` explains each one line by line with a note on what
the C version looks like.

The model is logistic regression: three weights, one bias, one dot product.
`ML/export_to_c_header.py` folds the scaler into the weights and emits
`vad3_model.h`, so the device does no normalisation at all.

---

## What the array can and cannot do

40 mm spacing at 16 kHz gives a maximum delay of **1.87 samples** across one
axis. Integer-lag correlation would give four distinguishable values in
total, so sub-sample interpolation is the entire mechanism, not a
refinement.

Simulated GCC-PHAT accuracy, 64 ms windows, 300–3400 Hz:

| SNR | delay RMSE | bearing RMSE at broadside |
|---|---|---|
| 20 dB | 0.020 samples | 0.6° |
| 10 dB | 0.055 | 1.7° |
| 5 dB | 0.102 | 3.1° |
| 0 dB | 0.226 | 6.9° |

Measured across nine known bearings at 10 dB: **mean error 2.6°**, worst at
the 45° diagonals where both axes work at reduced sensitivity.

**Beamforming is deliberately not implemented.** The array spans 57 mm
diagonally; at 1 kHz that is 0.17 of a wavelength. Delay-and-sum would sum
four nearly identical signals — √4 = 6 dB of noise averaging and no
directivity. If you use it, call it 4-channel averaging and be honest.

Near endfire the array goes blind: sensitivity is `(d/c)·sin θ`, which
vanishes as θ → 0. The firmware reports a confidence value; use it.

---

## Layout

```
capstone.py                 one entry point for the host-side pipeline
sar_gui.py                  desktop app: live detection, per-mic recording
BUILD.md                    step-by-step build and flash

ML/                         features, training, QC, C-header export
INMP_441_Dataset/           your own recordings (speech / non_speech)
Public_Dataset/             MUSAN, ESC-50, Speech Commands (optional)

Recording_Via_IOT_Devices/
  record.py                 capture audio, any of three boards
  detector_monitor.py       read the Detector's result packets
  devices.py                per-board baud, framing, DTR/RTS policy
  firmware/K210/
    src/Recorder/           probe the mics, stream one at a time
    src/Detector/           4 mics at once, VAD + DOA on device
```

Two firmware projects share one SDK tree. `-DPROJ=Recorder` or
`-DPROJ=Detector`. Delete `build/` between them — a stale `CMakeCache.txt`
pins the old `PROJ` and will rebuild the wrong firmware without complaint.

---

## Quick start

```bash
pip install -r requirements.txt
python capstone.py status          # what exists, and what to do next
```

Full sequence in [BUILD.md](BUILD.md): probe → record → QC → train → export
→ flash → monitor.

---

## The measurement discipline

This is the part worth copying, more than the code.

**A confound probe gates every dataset.** `capstone.py qc` trains a
classifier on background noise only — quietest frames, level divided out, no
speech present at all. If it can still separate the two folders, they differ
by recording conditions rather than by speech, and any accuracy measured
afterwards is fiction. The first dataset scored **0.905**. It should be near
0.500.

**Splits are grouped by source file, never by window.** Windows cut from one
recording are near-duplicates; splitting them randomly grades the model on
data it has already seen.

**Recall at 5% false-alarm rate, not accuracy.** A missed victim and a false
alarm are not the same cost, so one accuracy figure cannot describe both.
PR-AUC is only comparable between runs of equal prevalence — its chance
baseline *is* the positive rate — so cross-run comparisons use ROC-AUC or
recall@FPR.

**Corpora are budgeted by window count, not file count.** One MUSAN speech
file yields ~1000 analysis windows; one Speech Commands clip yields 1. An
equal file cap left MUSAN with 99.4% of the positive mass and the speaker
diversity never reached the model.

---

## Things that cost real time, recorded so they don't again

**Unframed PCM cannot survive a dropped byte.** Lose one and the 16-bit
sample boundary flips: every sample afterwards is noise near Nyquist, while
the WAV header stays valid and the file still plays. Three files failed this
way and it went unnoticed for weeks. Every frame now carries a sequence
number and a checksum, so a loss is *counted* instead of silent.

**The magic word is not a sync point.** `0x5A 0xA5` occurs by chance inside
int16 audio in about 1.6% of blocks. Four things are checked before a frame
is accepted: shape fields sane, channel count matching, checksum good, and
sequence advancing by exactly one.

**`(int16_t)x` truncates, it does not clamp.** An overloaded sample wraps to
the opposite rail, so a sine becomes a sawtooth — and a sawtooth has
mean|x|/peak near 0.5, the same fingerprint as random noise. A wrapping
channel looks exactly like a floating pin while the clip counter reads zero.

**A floating pin reads as random bits**, not as a constant, so checks for
all-zero and all-ones both miss it. Its signature is mean|x| ≈ 16384 with
peak-to-peak pinned at full scale, unchanging no matter what you do to the
microphones.

**PHAT must be band-masked.** Whitening divides every bin by its own
magnitude, so bins outside the signal band — holding only numerical noise —
get amplified to unit weight alongside the real ones. Unmasked, a true delay
of 1.50 samples was estimated as 0.14. Masked: 1.49.

**`i2s_set_sample_rate` returns the bit clock**, 64× the sample rate.
Comparing it against 16000 reports a mismatch on a perfectly healthy chain.

**16000 is not a power of two.** `voice_band_ratio` originally took one
16000-point FFT, which no K210 implementation reproduces. The framed
approximation differed by 0.065 — against a model weight of 4.03, that is
0.26 of logit, roughly 11% of the decision margin, lost silently on device.
Both sides now use 512-point frames and agree to 9.3e-6.

---

## Status

| | |
|---|---|
| 4-mic capture, per-slot diagnostics | working on hardware |
| 3-feature VAD, host and device | parity verified to 1e-6 (harmonicity), 0.013 dB (SNR) |
| DOA | verified in simulation, **not yet on hardware** |
| Dataset confound | 0.812 on the latest recordings — still above the 0.65 target |
| Beamforming | not implemented, and not worth it at this aperture |

The open item is the dataset. Until the confound probe passes, device-side
accuracy numbers are not measurements.
