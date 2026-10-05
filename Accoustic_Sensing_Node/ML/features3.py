"""
features3.py - three features. That is the whole model's input.

Each one answers a question you could ask out loud, and each one is a short
loop in C. If the detector misfires you can print all three and see which one
lied. With fifty-two you cannot.

    1. harmonicity      Does the waveform repeat?
    2. voice_band_ratio Is the energy where a voice lives?
    3. snr_db           Is it louder than this room's background?

WHY THESE THREE

A packet header, a door slam and a dropped spanner are all loud, and all
broadband, so an energy-in-band detector fires on every one of them. That is
the oldest failure in voice detection and no amount of extra training data
fixes it, because it is not a data problem - the detector genuinely cannot
tell those apart from a shout.

What separates a voice is periodicity. Vocal folds vibrate at 60-400 Hz, so
the waveform repeats. A click repeats at nothing. That is what harmonicity
measures, and on the earlier dataset it separated the classes about three
times better than any other single feature.

The other two stop the obvious mistakes: a 50 Hz hum is beautifully periodic
but is not in the voice band, and a quiet periodic hiss is not worth waking
anyone for.
"""

import numpy as np
from scipy.signal import butter, sosfilt

FEATURE_VERSION = 4
SAMPLE_RATE = 16000

# ---------------------------------------------------------------------------
# HIGH-PASS FIRST. This is not optional and it is easy to forget.
#
# The INMP441 recordings put 74-97% of their total energy below 100 Hz -
# slow DC wander and infrasonic drift, roughly 30 dB above the speech band.
# Feed that straight into the autocorrelation and the harmonicity feature
# measures the rumble instead of the voice: on the first dataset it scored
# 0.691 for speech and 0.696 for non-speech, a d-prime of 0.03. Useless.
#
# After a 100 Hz high-pass the same feature separates the classes cleanly,
# because the rumble is gone and what is left to repeat is the voice.
#
# The firmware deliberately removes only DC (about 5 Hz), so this filter has
# to happen here. Filtering on the device is irreversible; filtering here is
# a line you can change.
#
# IN C: a second-order biquad, five multiply-adds per sample.
# ---------------------------------------------------------------------------
HPF_CUTOFF = 100.0
_HPF_SOS = butter(4, HPF_CUTOFF, btype="highpass", fs=SAMPLE_RATE, output="sos")


def highpass(x):
    """Causal 100 Hz high-pass. sosfilt, not sosfiltfilt, so the PC does
    exactly what a streaming device is able to do."""
    return sosfilt(_HPF_SOS, np.asarray(x, dtype=np.float64))
WINDOW_SEC = 1.0
HOP_SEC = 0.5

# Human fundamental frequency. Adult male sits near 85-155 Hz, adult female
# near 165-255 Hz, children higher. 60-400 covers all of it with margin.
F0_MIN = 60.0
F0_MAX = 400.0

# Telephone band. Enough of a voice to recognise it, and it deliberately
# excludes the sub-300 Hz region where the microphone's own rumble lives.
VOICE_LO = 300.0
VOICE_HI = 3400.0

FEATURE_NAMES = ["harmonicity", "voice_band_ratio", "snr_db"]


# ---------------------------------------------------------------------------
# 1. HARMONICITY
# ---------------------------------------------------------------------------
def harmonicity(x, sr=SAMPLE_RATE, frame=512):
    """How strongly does this window repeat itself?  Returns 0.0 to 1.0.

    THE IDEA, in one sentence: slide the signal over a delayed copy of
    itself, and if some delay makes them line up, that delay is the pitch
    period and the signal is periodic.

    Step by step, for one 512-sample frame:

      1. Subtract the mean. Otherwise a DC offset correlates with itself at
         every delay and everything looks periodic.
      2. energy = sum(x*x). This is the correlation at zero delay, and it is
         the largest value possible - so dividing by it puts the answer on a
         0..1 scale regardless of how loud the sound is. That division is
         what makes this feature immune to microphone gain.
      3. For each delay from 40 to 266 samples, multiply the signal by itself
         shifted by that delay and sum. 16000/400 = 40 samples is the shortest
         period we care about; 16000/60 = 266 is the longest.
      4. Take the best one. Near 1.0 means strongly periodic. Near 0 means
         noise or a transient.

    IN C: two nested loops and one divide. No FFT needed at this size, no
    library, no floating point required if you scale carefully. About 25
    lines.

    Here it is vectorised over frames because Python loops are slow, but the
    arithmetic is exactly the loop described above.
    """
    n_frames = len(x) // frame
    if n_frames == 0:
        return 0.0

    segs = x[:n_frames * frame].reshape(n_frames, frame).astype(np.float64)
    segs = segs - segs.mean(axis=1, keepdims=True)          # step 1
    energy = (segs ** 2).sum(axis=1)                        # step 2

    lag_min = int(sr / F0_MAX)
    lag_max = min(int(sr / F0_MIN), frame - 1)
    if lag_max <= lag_min:
        return 0.0

    # Steps 3 and 4. Done with an FFT because it gives every delay at once
    # and is identical to the loop; on the K210 write the loop instead.
    nfft = 1 << int(np.ceil(np.log2(2 * frame)))
    spec = np.fft.rfft(segs, n=nfft, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, n=nfft, axis=1)[:, :frame]

    peak = ac[:, lag_min:lag_max].max(axis=1)
    scores = np.where(energy > 1e-9, peak / np.maximum(energy, 1e-9), 0.0)

    # Max over frames, not mean: a window that is half speech and half pause
    # should still count as speech.
    return float(np.clip(scores.max(), 0.0, 1.0))


# ---------------------------------------------------------------------------
# 2. VOICE BAND RATIO
# ---------------------------------------------------------------------------
FFT_N = 512


def voice_band_ratio(x, sr=SAMPLE_RATE):
    """Fraction of total power sitting between 300 and 3400 Hz. 0.0 to 1.0.

    A ratio, not an absolute number, so microphone gain cancels out.

    This is the feature that rejects mains hum (all its energy is at 50 Hz
    and harmonics), rumble, and wind. On the earlier recordings 74 to 97
    percent of all energy was below 100 Hz, which is why this ratio was
    doing real work there.

    FRAMED, not one long transform. The obvious implementation takes a
    single FFT over the whole 16000-sample window - but 16000 is not a power
    of two, so no reasonable K210 implementation reproduces it. The framed
    version below differs from the single-transform one by 0.065, which
    against a model weight of 4.03 is 0.26 of logit, about 11% of the
    decision margin, lost silently on device.

    Measured agreement with sar_features.c after this change: 9.3e-6.

    IN C: one 512-point FFT per frame, then two running sums over bins. The
    bin boundaries are constants you compute once.
    """
    x = np.asarray(x, dtype=np.float64)
    n_frames = len(x) // FFT_N
    if n_frames == 0:
        return 0.0

    lo = (int(VOICE_LO) * FFT_N) // int(sr)
    hi = (int(VOICE_HI) * FFT_N) // int(sr)
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FFT_N) / (FFT_N - 1))

    total = 0.0
    band = 0.0
    for f in range(n_frames):
        spec = np.abs(np.fft.rfft(x[f * FFT_N:(f + 1) * FFT_N] * w)) ** 2
        total += spec.sum()
        band += spec[lo:hi].sum()
    return float(band / (total + 1e-20))


# ---------------------------------------------------------------------------
# 3. SNR
# ---------------------------------------------------------------------------
def snr_db(x, noise_floor):
    """How far above the room's background is this window, in dB.

    noise_floor is the mean square of the quiet parts of the SAME recording
    (see NoiseFloor below). Because this is a ratio of two powers from the
    same microphone, gain cancels here too - a quiet room and a loud room
    give the same answer for the same voice.

    Do not use absolute level instead. The earlier pipeline peak-normalised
    every window, which scaled silence up to full volume and threw away the
    single most useful cue there is.

    IN C: one sum of squares, one divide, one log. If you want to avoid
    log entirely, compare the ratio against a squared threshold.
    """
    p = float(np.mean(np.asarray(x, dtype=np.float64) ** 2))
    return float(10.0 * np.log10(p / (noise_floor + 1e-12) + 1e-12))


class NoiseFloor:
    """Estimates the background level of a recording.

    Offline (a whole file): take the 20th percentile of the per-frame energy.
    Twenty percent of a normal recording is gaps between words, so that
    percentile lands on the background rather than on the speech.

    Online (a live stream): the same percentile over the last few seconds.
    You cannot use the whole recording because you do not have the future.

    IN C: a small ring buffer of frame energies and a percentile, or simply
    track a slowly-decaying minimum.
    """

    def __init__(self, history_sec=8.0, frame=512, sr=SAMPLE_RATE):
        self.frame = frame
        self.maxlen = max(int(history_sec * sr / frame), 8)
        self.energies = []

    def update(self, x):
        n = len(x) // self.frame
        if n:
            segs = np.asarray(x[:n * self.frame], dtype=np.float64).reshape(n, self.frame)
            self.energies.extend((segs ** 2).mean(axis=1).tolist())
            if len(self.energies) > self.maxlen:
                self.energies = self.energies[-self.maxlen:]
        return self

    @property
    def value(self):
        if not self.energies:
            return 1e-12
        return float(np.percentile(self.energies, 20)) + 1e-12

    @property
    def ready(self):
        """False until there is enough history to trust the estimate. The
        first few seconds after power-on are not reliable."""
        return len(self.energies) >= self.maxlen // 3


# ---------------------------------------------------------------------------
# Putting them together
# ---------------------------------------------------------------------------
def extract(window, noise_floor_value, already_highpassed=True):
    """One 1-second window -> array of 3 numbers, in FEATURE_NAMES order.

    The window must already be high-passed. features_for_signal and the live
    Detector both do that for the whole stream, which is correct: filtering
    per window would restart the filter state 
    at every boundary."""
    x = np.asarray(window, dtype=np.float64)
    if not already_highpassed:
        x = highpass(x)
    return np.array([
        harmonicity(x),
        voice_band_ratio(x),
        snr_db(x, noise_floor_value),
    ], dtype=np.float32)


def windows(x, window_sec=WINDOW_SEC, hop_sec=HOP_SEC, sr=SAMPLE_RATE):
    win = int(window_sec * sr)
    hop = int(hop_sec * sr)
    for s in range(0, max(len(x) - win + 1, 0), hop):
        yield x[s:s + win]


def features_for_signal(x, window_sec=WINDOW_SEC, hop_sec=HOP_SEC, sr=SAMPLE_RATE):
    """Whole recording -> one feature vector per window.

    The noise floor is measured over the entire file, which is the right
    thing offline. At inference time use NoiseFloor incrementally instead,
    so that training and running see comparable estimates.
    """
    x = highpass(x)                      # once, over the whole recording
    nf = NoiseFloor().update(x).value
    return [extract(w, nf) for w in windows(x, window_sec, hop_sec, sr)]
