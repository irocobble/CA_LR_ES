/*
 * sar_features.c - see sar_features.h.
 *
 * Everything here is FFT, a biquad, and scalar reductions. No dynamic
 * allocation, no libm beyond sqrtf/logf/cosf/sinf.
 */

#include "sar_features.h"
#include <math.h>
#include <string.h>

/* 100 Hz 4th-order Butterworth high-pass at 16 kHz, as two biquads.
 * Generated from the same scipy call features3.py uses:
 *     butter(4, 100, 'highpass', fs=16000, output='sos')
 * Columns are b0, b1, b2, a1, a2 (a0 is 1 by construction).
 *
 * These are not "about 100 Hz" - they are the exact coefficients the model
 * was trained through. Rounding them changes the features. */
static const float HP_SOS[2][5] = {
    { 9.4998178264e-01f, -1.8999635653e+00f, 9.4998178264e-01f,
      -1.9285084851e+00f, 9.2999644240e-01f },
    { 1.0000000000e+00f, -2.0000000000e+00f, 1.0000000000e+00f,
      -1.9688774974e+00f, 9.7039660176e-01f },
};


void sf_init(sf_state_t *st)
{
    memset(st, 0, sizeof(*st));
}


/* Transposed direct form II, one section at a time. This is the same
 * structure scipy's sosfilt uses, which matters: a different but
 * mathematically equivalent structure gives slightly different rounding,
 * and the parity test is tight enough to see it. */
void sf_highpass(sf_state_t *st, float *x, int n)
{
    for (int s = 0; s < 2; ++s) {
        const float b0 = HP_SOS[s][0], b1 = HP_SOS[s][1], b2 = HP_SOS[s][2];
        const float a1 = HP_SOS[s][3], a2 = HP_SOS[s][4];
        float z1 = st->hp_z[s][0], z2 = st->hp_z[s][1];
        for (int i = 0; i < n; ++i) {
            const float in = x[i];
            const float out = b0 * in + z1;
            z1 = b1 * in - a1 * out + z2;
            z2 = b2 * in - a2 * out;
            x[i] = out;
        }
        st->hp_z[s][0] = z1;
        st->hp_z[s][1] = z2;
    }
}


/* ------------------------------------------------------------------ FFT */
/* Iterative radix-2, decimation in time. n must be a power of two. */
void sf_fft(float *re, float *im, int n, int inverse)
{
    for (int i = 1, j = 0; i < n; ++i) {          /* bit reversal */
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            float t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        const float ang = (inverse ? 2.0f : -2.0f) * 3.14159265358979f / (float)len;
        const float wr = cosf(ang), wi = sinf(ang);
        for (int i = 0; i < n; i += len) {
            float cr = 1.0f, ci = 0.0f;
            for (int k = 0; k < len / 2; ++k) {
                const int a = i + k, b = i + k + len / 2;
                const float xr = re[b] * cr - im[b] * ci;
                const float xi = re[b] * ci + im[b] * cr;
                re[b] = re[a] - xr;  im[b] = im[a] - xi;
                re[a] += xr;         im[a] += xi;
                const float ncr = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = ncr;
            }
        }
    }
    if (inverse) {
        for (int i = 0; i < n; ++i) { re[i] /= (float)n; im[i] /= (float)n; }
    }
}


/* --------------------------------------------------------- harmonicity */
/*
 * Slide the frame over a delayed copy of itself; if some delay makes them
 * line up, that delay is the pitch period.
 *
 *   1. subtract the mean, or a DC offset correlates with itself at every
 *      lag and everything looks periodic
 *   2. energy = sum(x*x) is the correlation at lag 0 and the largest value
 *      possible, so dividing by it puts the answer on 0..1 regardless of
 *      level - that division is what makes this immune to mic gain
 *   3. try every lag from 40 to 266 samples
 *   4. keep the best, and across frames keep the MAX, so a window that is
 *      half speech and half pause still counts as speech
 *
 * Direct time-domain correlation: 227 lags x 512 samples x 31 frames is
 * about 3.6 M multiply-accumulates per second of audio. On a 400 MHz core
 * with an FPU that is a few milliseconds - cheaper than converting this to
 * an FFT and having to match the host's rounding twice over.
 */
float sf_harmonicity(const float *x, int n)
{
    const int nf = n / SF_AC_FRAME;
    if (nf <= 0) return 0.0f;

    float best_overall = 0.0f;
    for (int f = 0; f < nf; ++f) {
        const float *seg = x + f * SF_AC_FRAME;

        float mean = 0.0f;
        for (int i = 0; i < SF_AC_FRAME; ++i) mean += seg[i];
        mean /= (float)SF_AC_FRAME;

        float energy = 0.0f;
        for (int i = 0; i < SF_AC_FRAME; ++i) {
            const float v = seg[i] - mean;
            energy += v * v;
        }
        if (energy < 1e-9f) continue;

        float best = 0.0f;
        for (int lag = SF_LAG_MIN; lag < SF_LAG_MAX && lag < SF_AC_FRAME; ++lag) {
            float acc = 0.0f;
            for (int i = 0; i + lag < SF_AC_FRAME; ++i)
                acc += (seg[i] - mean) * (seg[i + lag] - mean);
            if (acc > best) best = acc;
        }
        const float score = best / energy;
        if (score > best_overall) best_overall = score;
    }
    if (best_overall < 0.0f) best_overall = 0.0f;
    if (best_overall > 1.0f) best_overall = 1.0f;
    return best_overall;
}


/* ---------------------------------------------------- voice band ratio */
/* Share of total power between 300 and 3400 Hz. A ratio, so mic gain
 * cancels. This is what rejects mains hum, rumble and wind - on the earlier
 * recordings 74-97% of all energy sat below 100 Hz. */
float sf_voice_band_ratio(const float *x, int n)
{
    static float re[SF_FFT_N], im[SF_FFT_N];
    if (n < SF_FFT_N) return 0.0f;

    const int nf = n / SF_FFT_N;
    double total = 0.0, band = 0.0;
    const int lo = (SF_VOICE_LO_HZ * SF_FFT_N) / SF_SAMPLE_RATE;
    const int hi = (SF_VOICE_HI_HZ * SF_FFT_N) / SF_SAMPLE_RATE;

    for (int f = 0; f < nf; ++f) {
        const float *seg = x + f * SF_FFT_N;
        for (int i = 0; i < SF_FFT_N; ++i) {
            /* Hann window, same as the host's np.hanning on each block */
            const float w = 0.5f - 0.5f * cosf(2.0f * 3.14159265358979f
                                               * (float)i / (float)(SF_FFT_N - 1));
            re[i] = seg[i] * w;
            im[i] = 0.0f;
        }
        sf_fft(re, im, SF_FFT_N, 0);
        for (int k = 0; k <= SF_FFT_N / 2; ++k) {
            const double p = (double)re[k] * re[k] + (double)im[k] * im[k];
            total += p;
            if (k >= lo && k < hi) band += p;
        }
    }
    if (total < 1e-20) return 0.0f;
    return (float)(band / total);
}


/* ------------------------------------------------------------- snr_db */
float sf_snr_db(const float *x, int n, float noise_floor)
{
    double p = 0.0;
    for (int i = 0; i < n; ++i) p += (double)x[i] * x[i];
    p /= (double)n;
    return (float)(10.0 * log10(p / ((double)noise_floor + 1e-12) + 1e-12));
}


/* -------------------------------------------------------- noise floor */
void sf_update_noise_floor(sf_state_t *st, const float *x, int n)
{
    /* [FIX] Buffer across calls instead of dividing block length by frame
     * length. The old version computed n / SF_AC_FRAME, which is 256 / 512
     * = 0 for a single DMA block, so the loop body never executed: nf_count
     * stayed at zero, sf_warm() never returned true, DOA never ran, and
     * sf_noise_floor() returned its 1e-12 empty fallback - making snr_db
     * read 92 dB instead of about 10, and dragging the logit down by 16.
     *
     * Everything downstream looked plausible. Nothing errored. */
    int i = 0;
    while (i < n) {
        int take = SF_AC_FRAME - st->nf_fill;
        if (take > n - i) take = n - i;
        for (int k = 0; k < take; ++k)
            st->nf_acc[st->nf_fill + k] = x[i + k];
        st->nf_fill += take;
        i += take;

        if (st->nf_fill >= SF_AC_FRAME) {
            double e = 0.0;
            for (int k = 0; k < SF_AC_FRAME; ++k)
                e += (double)st->nf_acc[k] * st->nf_acc[k];
            st->nf[st->nf_head] = (float)(e / SF_AC_FRAME);
            st->nf_head = (st->nf_head + 1) % SF_NF_FRAMES;
            if (st->nf_count < SF_NF_FRAMES) st->nf_count++;
            st->nf_fill = 0;
        }
    }
}

/* 20th percentile. Twenty percent of a normal recording is gaps between
 * words, so that percentile lands on the background rather than the speech.
 *
 * Insertion sort into a scratch array: SF_NF_FRAMES is 250 and this runs
 * twice a second, so an O(n^2) sort is 62 k operations per second - noise
 * next to the correlator. Keeping it simple beats keeping it clever. */
float sf_noise_floor(const sf_state_t *st)
{
    if (st->nf_count == 0) return 1e-12f;

    static float tmp[SF_NF_FRAMES];
    const int n = st->nf_count;
    for (int i = 0; i < n; ++i) tmp[i] = st->nf[i];
    for (int i = 1; i < n; ++i) {
        const float v = tmp[i];
        int j = i - 1;
        while (j >= 0 && tmp[j] > v) { tmp[j + 1] = tmp[j]; --j; }
        tmp[j + 1] = v;
    }
    int idx = (int)(0.20f * (float)(n - 1) + 0.5f);
    if (idx < 0) idx = 0;
    if (idx >= n) idx = n - 1;
    return tmp[idx] + 1e-12f;
}

int sf_warm(const sf_state_t *st)
{
    return st->nf_count >= (SF_NF_FRAMES / 3);
}


/* ----------------------------------------------------------- assembly */
void sf_extract(const sf_state_t *st, const float *win, float out[SF_N_FEATURES])
{
    const float floor_v = sf_noise_floor(st);
    out[0] = sf_harmonicity(win, SF_WINDOW);
    out[1] = sf_voice_band_ratio(win, SF_WINDOW);
    out[2] = sf_snr_db(win, SF_WINDOW, floor_v);
}


float sf_smooth_score(sf_state_t *st, float score, int k)
{
    if (k < 1) k = 1;
    if (k > 8) k = 8;
    st->score_hist[st->score_head] = score;
    st->score_head = (st->score_head + 1) % 8;
    if (st->score_count < 8) st->score_count++;

    const int use = (st->score_count < k) ? st->score_count : k;
    float sum = 0.0f;
    for (int i = 0; i < use; ++i) {
        int idx = st->score_head - 1 - i;
        while (idx < 0) idx += 8;
        sum += st->score_hist[idx];
    }
    return sum / (float)use;
}
