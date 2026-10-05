# Build and run, step by step

Two firmware projects share one SDK tree. Pick one with `-DPROJ=`.

| project | what it does | when |
|---|---|---|
| `Recorder` | probes the mics, streams ONE at a time to the PC | wiring checks, dataset collection |
| `Detector` | 4 mics simultaneously, VAD + DOA on device, sends 16-byte results | the actual system |

---

## 0. One-time setup

```
pip install -r requirements.txt
```

Toolchain at `C:\Code\Cmake\kendryte-toolchain\bin` (adjust paths below to match).
Adding that folder to PATH saves typing `-DTOOLCHAIN` every time.

---

## 1. Verify the microphones — `Recorder`

Skip this only if all four slots have already passed.

```
cd Recording_Via_IOT_Devices\firmware\K210
rmdir /s /q build
mkdir build
cd build
cmake .. -DPROJ=Recorder -G "Unix Makefiles" -DTOOLCHAIN=C:/Code/Cmake/kendryte-toolchain/bin
C:\Code\Cmake\kendryte-toolchain\bin\make.exe
kflash -p COM12 -b 1500000 Main.bin
```

Press the board's reset button after flashing — kflash leaves it in ISP mode,
where it sends nothing and a capture reads zero bytes forever.

```
python -m serial.tools.miniterm COM12 1500000
```

Read, in this order:

1. `#i2s rate=15986 OK` — if this is bad, nothing below it means anything.
2. `#shift` table — pick the **smallest** `sh` with `clip=0` on **every** slot,
   set `SAMPLE_SHIFT`, rebuild.
3. `#stat` lines — tap each mic. Each slot must respond to **its own** mic.

| verdict | meaning |
|---|---|
| both slots move together | reading one mic twice — L/R strapping fault |
| `FLOATING/RANDOM` | nothing driving that half-frame |
| `CLIPPING` | raise `SAMPLE_SHIFT` |
| `UNDRIVEN` | no clock, no power, or wrong pin |

Keys while it runs: `0`–`3` lock to one mic, `c` cycle, `p` stats, `?` help.

**Do not continue until all four slots respond independently.** A wrong slot
map gives a confident bearing rotated by an unknown amount, and nothing on
screen will hint at it.

---

## 2. Collect a dataset — still `Recorder`

```
cd ..\..\..\..
python capstone.py record --device k210 --port COM12 --label speech ^
  --minutes 2 --speaker 1 --room lab --set 1 --distance 2m
python capstone.py record --device k210 --port COM12 --label non_speech ^
  --minutes 2 --speaker 1 --room lab --set 1 --distance 2m
```

Alternate the two **without touching the mic, gain or position**. Watch the
`dropped=` counter: if it climbs, the recording is not trustworthy.

---

## 3. Check the dataset — the gate

```
python capstone.py qc
```

The confound probe at the bottom trains on background noise only, with
speech removed and level divided out. **It must come back under 0.65.**
Above 0.80 means your two classes differ by recording conditions rather
than by speech, and every number after that is fiction.

---

## 4. Train and export

```
python capstone.py train
python capstone.py train-corpus --budget 6000     (optional, needs Public_Dataset)
python ML\export_to_c_header.py --model ML\vad3_model.joblib ^
       --out Recording_Via_IOT_Devices\firmware\K210\src\Detector\vad3_model.h
```

`features3.py` is at `FEATURE_VERSION = 4`. If you change it, delete
`feature_cache`, retrain, and regenerate the header — otherwise the weights
describe features the device no longer computes.

---

## 5. Build the detector

Set the slot map in `src/Detector/main_detector.c` from what step 1 showed:

```c
#define MIC_EAST   0
#define MIC_WEST   1
#define MIC_NORTH  2
#define MIC_SOUTH  3
#define SAMPLE_SHIFT 13
```

East/West must be one opposing pair, North/South the other, and **both
baselines equal** — the bearing comes from a ratio of two delays, so the
absolute 40 mm cancels but a mismatch between the axes does not.

```
cd Recording_Via_IOT_Devices\firmware\K210
rmdir /s /q build
mkdir build
cd build
cmake .. -DPROJ=Detector -G "Unix Makefiles" -DTOOLCHAIN=C:/Code/Cmake/kendryte-toolchain/bin
C:\Code\Cmake\kendryte-toolchain\bin\make.exe
kflash -p COM12 -b 1500000 Main.bin
```

Delete `build` between switching projects. A stale `CMakeCache.txt` pins the
old `PROJ` and will happily rebuild and flash the wrong firmware.

---

## 6. Read the output

```
python -m serial.tools.miniterm COM12 1500000
```

16-byte packets, `5A A5 10 seq | angle×10 | conf×1000 | score×100 | level×10 |
speech | warm | xor | pad`.

`warm=0` for the first ~8 s while the noise floor fills — scores before that
are not trustworthy. `angle=-1` means no confident bearing.

---

## What to expect

| | |
|---|---|
| bearing accuracy | 2–3° at broadside, 5–12 dB SNR |
| worst case | 45° diagonals, ~6° |
| near endfire | degrades sharply — sensitivity is `(d/c)·sin θ` |
| CPU | under 2% of a 400 MHz core |

Beamforming is **not** included and is not worth building at this size: the
array spans 57 mm diagonally, which at 1 kHz is 0.17 of a wavelength. Delay-
and-sum would sum four nearly identical signals — 6 dB of noise averaging,
no directivity. Call it 4-channel averaging if you use it at all.
