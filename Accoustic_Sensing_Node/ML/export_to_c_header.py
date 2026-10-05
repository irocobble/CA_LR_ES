"""
Exports vad_model.joblib into a C header for K210 inference.

Key trick: a StandardScaler followed by a linear model is STILL a linear
model. Instead of shipping mean[]/scale[] arrays and doing
    x_scaled = (x - mean) / scale
    score = w . x_scaled + b
on the K210, this folds the scaler directly into the weights:
    w_eff[i] = w[i] / scale[i]
    b_eff    = b - sum(w[i] * mean[i] / scale[i])
so on-device inference is exactly ONE dot product against the RAW feature
vector - no scaling step, no separate mean/scale arrays to keep in sync.

Second trick: LogisticRegression's actual decision is
    sigmoid(w . x_scaled + b) >= threshold_prob
sigmoid() is monotonic, so this is equivalent to comparing the raw score
(the logit, before sigmoid) against a fixed logit threshold:
    w . x_scaled + b >= log(threshold_prob / (1 - threshold_prob))
This avoids computing exp() on the K210 entirely - just the dot product
and one comparison.

Usage:
    python export_to_c_header.py --model vad_model.joblib --out vad_model.h
"""
import argparse
import math

import joblib
import numpy as np


def prob_to_logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)  # keep log() finite at the edges
    return math.log(p / (1.0 - p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="vad_model.joblib")
    ap.add_argument("--out", default="vad_model.h")
    args = ap.parse_args()

    bundle = joblib.load(args.model)

    # Two bundle formats have shown up in this project - handle both so this
    # script doesn't break again the next time the training script changes:
    #   (a) separate "model" + "scaler" keys
    #   (b) a single sklearn Pipeline under "pipeline", steps named by
    #       sklearn's default lowercase-classname convention
    if "pipeline" in bundle:
        pipeline = bundle["pipeline"]
        scaler = pipeline.named_steps["standardscaler"]
        model = pipeline.named_steps["logisticregression"]
    else:
        model = bundle["model"]
        scaler = bundle["scaler"]

    threshold = bundle["threshold"]
    feature_names = bundle["feature_names"]
    dropped = bundle.get("dropped_feature_prefixes", [])
    window_sec = bundle["window_sec"]
    hop_sec = bundle["hop_sec"]
    sample_rate = bundle["sample_rate"]
    smooth_k = bundle.get("smooth_k", 3)
    feature_version = bundle.get("feature_version", "unspecified")

    w = model.coef_.ravel()          # shape (n_features,)
    b = float(model.intercept_[0])
    mean = scaler.mean_
    scale = scaler.scale_

    n = len(feature_names)
    assert len(w) == n == len(mean) == len(scale), (
        f"Length mismatch: weights={len(w)}, feature_names={n}, "
        f"scaler.mean={len(mean)}, scaler.scale={len(scale)}. "
        f"This usually means features.py changed without retraining, "
        f"or --drop-features wasn't accounted for somewhere downstream."
    )

    # --- fold the scaler into the weights ---
    w_eff = w / scale
    b_eff = b - float(np.sum(w * mean / scale))

    # --- convert probability threshold to a logit threshold ---
    logit_threshold = prob_to_logit(threshold)

    version_define = (f"#define VAD_FEATURE_VERSION {feature_version}\n"
                       if isinstance(feature_version, int)
                       else f"/* feature_version not recorded in this bundle - "
                            f"verify manually that the feature-extraction code "
                            f"matches VAD_FEATURE_NAMES below */\n")

    with open(args.out, "w") as f:
        f.write(f"""/* Auto-generated from {args.model} - DO NOT EDIT BY HAND.
 * Regenerate with: python export_to_c_header.py --model {args.model} --out {args.out}
 *
 * Scaler is folded into these weights already - do NOT apply any
 * mean/scale normalization on-device, just take the raw feature vector
 * from features.c (in the SAME ORDER as VAD_FEATURE_NAMES below) and
 * compute one dot product.
 *
 * feature_version={feature_version} - if features.c is ever changed,
 * this model must be retrained and this header regenerated, or the
 * feature vector layout will silently no longer match these weights.
 */
#ifndef VAD_MODEL_H
#define VAD_MODEL_H

{version_define}#define VAD_N_FEATURES {n}
#define VAD_WINDOW_SEC {window_sec}f
#define VAD_HOP_SEC {hop_sec}f
#define VAD_SAMPLE_RATE {sample_rate}
#define VAD_SMOOTH_K {smooth_k}

""")
        if dropped:
            f.write(f"/* Features dropped at training time (--drop-features): "
                     f"{', '.join(dropped)} */\n\n")

        f.write("/* Feature order - features.c MUST produce values in exactly this order */\n")
        f.write("static const char *VAD_FEATURE_NAMES[VAD_N_FEATURES] = {\n")
        for name in feature_names:
            f.write(f'    "{name}",\n')
        f.write("};\n\n")

        f.write("/* Scaler already folded in - dot this directly against raw features */\n")
        f.write("static const float VAD_WEIGHTS[VAD_N_FEATURES] = {\n")
        for i, val in enumerate(w_eff):
            f.write(f"    {val:.8e}f,  /* {feature_names[i]} */\n")
        f.write("};\n\n")

        f.write(f"static const float VAD_BIAS = {b_eff:.8e}f;\n\n")

        f.write(f"/* Compare raw score (logit) against this - equivalent to \n"
                f" * sigmoid(score) >= {threshold:.4f}, but skips exp() entirely */\n")
        f.write(f"static const float VAD_LOGIT_THRESHOLD = {logit_threshold:.8e}f;\n\n")

        f.write("""/* score = VAD_WEIGHTS . features + VAD_BIAS
 * speech if score >= VAD_LOGIT_THRESHOLD
 * Apply VAD_SMOOTH_K-window smoothing on the SCORE (not the bool decision)
 * across consecutive calls before thresholding, matching how the model
 * was evaluated during training. */
static inline float vad_score(const float *features)
{
    float score = VAD_BIAS;
    for (int i = 0; i < VAD_N_FEATURES; ++i) {
        score += VAD_WEIGHTS[i] * features[i];
    }
    return score;
}

static inline int vad_is_speech(float smoothed_score)
{
    return smoothed_score >= VAD_LOGIT_THRESHOLD;
}

#endif /* VAD_MODEL_H */
""")

    print(f"Wrote {args.out}: {n} features, feature_version={feature_version}")
    print(f"Logit threshold: {logit_threshold:.4f}  (probability threshold was {threshold:.4f})")
    if dropped:
        print(f"Dropped features from training: {dropped}")
    print("\nRemember: features.c must produce the feature vector in the exact "
          "order listed in VAD_FEATURE_NAMES, with the SAME preprocessing "
          "(high-pass, per-utterance context, etc.) as features.py used at "
          "training time. That C port is the remaining big piece of work.")


if __name__ == "__main__":
    main()