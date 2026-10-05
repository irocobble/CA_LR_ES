/*
 * sar_features.h - the three features, on device.
 *
 * Produces the vector that vad3_model.h expects, in exactly the order its
 * VAD_FEATURE_NAMES lists:
 *
 *     [0] harmonicity        normalised autocorrelation peak, 60-400 Hz
 *     [1] voice_band_ratio   share of power in 300-3400 Hz
 *     [2] snr_db             level over this recording's own noise floor
 *
 * This does NOT touch the capture path. Feed it the int16 blocks that
 * main.c already produces and it does the rest.
 *
 * WHAT MUST MATCH THE HOST, OR THE MODEL IS MEANINGLESS
 *
 *   - the 100 Hz high-pass, coefficient for coefficient
 *   - the pitch search range and frame length inside harmonicity
 *   - the 20th-percentile noise floor, and the fact that it is measured
 *     over a ROLLING window rather than the whole recording
 *
 * features3.py on the host estimates the noise floor over the entire file,
 * which is not available on a live stream. sar_features uses a trailing
 * window instead. The audit in infer.py measured that gap: the median score
 * shift is small, but in the ambiguous 0.1-0.9 band it reached 0.124, so it
 * is worth re-measuring once the model is retrained on clean data.
 */

#ifndef SAR_FEATURES_H
#define SAR_FEATURES_H

#include <stdint.h>

#define SF_SAMPLE_RATE   16000
#define SF_N_FEATURES    3      /* must equal VAD_N_FEATURES */

/* 1 s window, 0.5 s hop - matches VAD_WINDOW_SEC / VAD_HOP_SEC. */
#define SF_WINDOW        16000
#define SF_HOP           8000

/* Autocorrelation frame. 512 at 16 kHz is 32 ms, long enough to hold two
 * periods of the lowest pitch we search for (60 Hz = 266 samples). */
#define SF_AC_FRAME      512
#define SF_F0_MIN_HZ     60
#define SF_F0_MAX_HZ     400
#define SF_LAG_MIN       (SF_SAMPLE_RATE / SF_F0_MAX_HZ)   /*  40 */
#define SF_LAG_MAX       (SF_SAMPLE_RATE / SF_F0_MIN_HZ)   /* 266 */

/* Voice band for feature [1]. Also the band the DOA code correlates over. */
#define SF_VOICE_LO_HZ   300
#define SF_VOICE_HI_HZ   3400

/* FFT for the band ratio. 512 bins at 16 kHz is 31.25 Hz resolution. */
#define SF_FFT_N         512

/* Noise floor history. 8 s of frame energies, matching the host's rolling
 * estimator. 8 s / 32 ms = 250 entries, about 1 kB. */
#define SF_NF_FRAMES     250

/* Rolling state. One instance per microphone if you run several. */
typedef struct {
    /* high-pass biquad state, 2 sections, transposed direct form II */
    float hp_z[2][2];

    /* Partial-frame accumulator. The DMA block is 256 samples but a noise
     * floor frame is 512, so an integer division of block by frame gave
     * zero and the ring was never written to. Buffering the remainder makes
     * the estimator work for ANY block size. */
    float nf_acc[SF_AC_FRAME];
    int   nf_fill;

    /* noise floor ring buffer of per-frame mean-square values */
    float nf[SF_NF_FRAMES];
    int   nf_count;      /* how many entries are valid (saturates) */
    int   nf_head;

    /* score smoothing, VAD_SMOOTH_K wide */
    float score_hist[8];
    int   score_count;
    int   score_head;
} sf_state_t;

void  sf_init(sf_state_t *st);

/* In-place 100 Hz high-pass on one block. Call on every block as it
 * arrives, before anything else looks at the audio. */
void  sf_highpass(sf_state_t *st, float *x, int n);

/* Update the noise-floor history from a high-passed block. Call this
 * BEFORE sf_extract for the same audio, so the floor includes it - that is
 * the order features3.py uses. */
void  sf_update_noise_floor(sf_state_t *st, const float *x, int n);

/* 20th percentile of the history. Returns a small positive floor before
 * enough history has accumulated. */
float sf_noise_floor(const sf_state_t *st);

/* True once the history holds at least 3 s. Scores before this are not
 * trustworthy - the host marks them "(cold)". */
int   sf_warm(const sf_state_t *st);

/* One SF_WINDOW-sample high-passed window -> SF_N_FEATURES floats.
 * out[] is in VAD_FEATURE_NAMES order. */
void  sf_extract(const sf_state_t *st, const float *win, float out[SF_N_FEATURES]);

/* Individual features, exposed for the parity test against Python. */
float sf_harmonicity(const float *x, int n);
float sf_voice_band_ratio(const float *x, int n);
float sf_snr_db(const float *x, int n, float noise_floor);

/* Push a raw logit into the smoothing window and return the mean.
 * vad3_model.h says to smooth the SCORE, not the boolean decision. */
float sf_smooth_score(sf_state_t *st, float score, int k);

/* Real FFT used by the band ratio and by the DOA code. Radix-2, in place.
 * re[] and im[] are SF_FFT_N long; im[] must be zeroed on entry. */
void  sf_fft(float *re, float *im, int n, int inverse);

#endif /* SAR_FEATURES_H */
