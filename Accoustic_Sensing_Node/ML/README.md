# ML

Three features. That is the whole input.

```
python qc.py                      check the dataset first
python train_basic.py             fit and cross-validate
python predict.py --wav x.wav     run it on a file
python predict.py --port COM12    run it live
```

## Files

- `features3.py` — the three features, written to be read. Each one has the
  idea in plain words, the steps, and a note on what the C version looks like.
- `qc.py` — per-file health plus the confound probe.
- `train_basic.py` — your own recordings only. Grouped cross-validation,
  recall@5%FPR, and a single-threshold baseline for comparison.
- `train_corpus.py` — your recordings plus `Public_Dataset`, every source
  budgeted by window count.
- `predict.py` — file and live inference, with smoothing and hangover.
- `filter_test.py` — compares front-end filter settings against your data
  and against synthetic confusers. Run it before changing the filtering.

## The high-pass is not optional

Everything is filtered at 100 Hz before any feature is computed. Without it,
`harmonicity` measures the microphone's low-frequency rumble instead of the
voice. Measured on the first dataset:

| | speech | non-speech | d-prime |
|---|---|---|---|
| no high-pass | 0.691 | 0.696 | **0.03** |
| 100 Hz high-pass | 0.660 | 0.377 | **2.43** |

The firmware removes DC only, at about 5 Hz, so this has to happen here.
Filtering on the device is irreversible; filtering here is a line you can
change.

## Read the d-prime table before the accuracy

`train_basic.py` prints how well each feature separates the classes on its
own. Above 1 is useful, above 2 is strong. If a feature sits near 0 it is
contributing nothing and something upstream is wrong — that table is what
caught the missing high-pass.

## Why not accuracy

Accuracy on an imbalanced, cost-asymmetric problem rewards guessing the
majority class. Report **recall at 5% false-alarm rate**: how many voices you
catch, at a stated cost in false alarms.

Also: PR-AUC is only comparable between runs with the same class balance,
because its chance baseline *is* the positive rate. Use ROC-AUC or
recall@FPR when comparing across datasets.

## Growing past three features

Add a fourth only when you can name the failure it fixes. The previous
version of this project reached 52 features, and an ablation showed the two
largest-weighted ones could be deleted with a cost of 0.001 — they were
collinear with features already present. More features mostly buys more ways
to be wrong.

## Budget by windows, never by files

`train_corpus.py` caps each source by **window count**, and this is the whole
reason it exists. Window yield per file differs by three orders of magnitude:

| source | typical file | windows per file |
|---|---|---|
| MUSAN speech | ~8 min | ~1000 |
| MUSAN music | ~2 min | ~450 |
| ESC-50 | 5 s | 9 |
| Speech Commands | 1 s | 1 |

Cap every source at 1200 *files* and MUSAN takes 99.4% of the positive
windows while Speech Commands contributes 0.6% — the speaker diversity that
was the entire reason for adding it never reaches the model. The per-source
percentage table printed at startup is there so this is visible immediately.

Two other things it handles that a naive loader would not: ESC-50 is 44.1 kHz
while everything else is 16 kHz, so it resamples; and Speech Commands clips
are exactly one window long, which makes `snr_db` degenerate, so short clips
are concatenated into longer pseudo-recordings first.

## The feature-subset table

Every run prints recall@5%FPR for all seven combinations of the three
features. If a two-feature row is within about 0.02 of all three, drop the
third — one fewer feature is one fewer thing to port to C and one fewer thing
to test for numerical parity.

## Should you add a low-pass?

Run `python filter_test.py` rather than guessing. The measured answer so far:

**A low-pass does not help, and can backfire.** Narrowband signals
autocorrelate well, so restricting the band makes *everything* look more
periodic. Measured on synthetic confusers, white noise harmonicity climbs
from 0.152 to 0.330 as the low-pass tightens from none to 1 kHz — the feature
becomes less able to reject the thing it exists to reject.

**Pre-emphasis is the change that helps.** `y[n] = x[n] - 0.97·x[n-1]`, one
multiply-add, flattens a 1/f spectrum. 1/f noise is concentrated at low
frequencies and therefore looks narrowband, which is why rumble scores as
"periodic" when it is nothing of the sort:

| confuser | HP100 only | HP100 + pre-emphasis |
|---|---|---|
| pink noise | 0.625 | **0.182** |
| wind rumble | 0.452 | **0.142** |
| speech | 0.812 | 0.787 |

Margin against the worst confuser goes from −0.038 to **+0.058**, the only
configuration tested that reaches positive.

**But the two tests disagree, so this is not settled.** On the first dataset,
pre-emphasis *lowered* harmonicity d-prime from 2.43 to 1.57 — because that
dataset's negatives are rumble-dominated room tone, and its confound is partly
rumble, so removing rumble removes some of the (spurious) separation. Believe
the synthetic test until your own negatives contain wind, machinery and 1/f
rumble, and re-run both once `qc.py`'s probe passes.

## The limit no filter fixes

A steady tonal source — a generator, the canonical rubble-site confuser —
scores **0.850** harmonicity against speech's 0.812, under every filter
setting tested. That is not a bug: a generator genuinely is periodic.

The cue that separates them is syllable-rate modulation. Speech fluctuates at
2–8 Hz because of syllables; a generator does not. Add that fourth feature
when you have recorded a generator and can show it is needed — not before.
