/* Auto-generated from vad3_model.joblib - DO NOT EDIT BY HAND.
 * Regenerate with: python export_to_c_header.py --model vad3_model.joblib --out vad3_model.h
 *
 * Scaler is folded into these weights already - do NOT apply any
 * mean/scale normalization on-device, just take the raw feature vector
 * from features.c (in the SAME ORDER as VAD_FEATURE_NAMES below) and
 * compute one dot product.
 *
 * feature_version=unspecified - if features.c is ever changed,
 * this model must be retrained and this header regenerated, or the
 * feature vector layout will silently no longer match these weights.
 */
#ifndef VAD_MODEL_H
#define VAD_MODEL_H

/* feature_version not recorded in this bundle - verify manually that the feature-extraction code matches VAD_FEATURE_NAMES below */
#define VAD_N_FEATURES 3
#define VAD_WINDOW_SEC 1.0f
#define VAD_HOP_SEC 0.5f
#define VAD_SAMPLE_RATE 16000
#define VAD_SMOOTH_K 3

/* Feature order - features.c MUST produce values in exactly this order */
static const char *VAD_FEATURE_NAMES[VAD_N_FEATURES] = {
    "harmonicity",
    "voice_band_ratio",
    "snr_db",
};

/* Scaler already folded in - dot this directly against raw features */
static const float VAD_WEIGHTS[VAD_N_FEATURES] = {
    1.66642796e+01f,  /* harmonicity */
    4.03455134e+00f,  /* voice_band_ratio */
    -1.76534497e-01f,  /* snr_db */
};

static const float VAD_BIAS = -1.21305809e+01f;

/* Compare raw score (logit) against this - equivalent to 
 * sigmoid(score) >= 0.9160, but skips exp() entirely */
static const float VAD_LOGIT_THRESHOLD = 2.38873807e+00f;

/* score = VAD_WEIGHTS . features + VAD_BIAS
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
