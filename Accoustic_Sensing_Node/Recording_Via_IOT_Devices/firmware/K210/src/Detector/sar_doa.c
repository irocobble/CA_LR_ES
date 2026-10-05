/*
 * sar_doa.c - see sar_doa.h.
 *
 * Cost per estimate: 4 forward FFTs of 1024 points, then two lag searches.
 * The search is coarse-to-fine rather than a flat grid: 51 coarse lags plus
 * 21 fine ones is 72 evaluations against ~200 masked bins, so about 14 k
 * complex multiply-accumulates per pair. A flat 0.01 grid would be 500
 * evaluations for the same answer. At 10 Hz updates this is well under 1%
 * duty cycle on a 400 MHz core.
 */

#include "sar_doa.h"
#include "sar_features.h"     /* sf_fft */
#include <math.h>
#include <string.h>

#define PI_F 3.14159265358979f

/* Bin indices of the correlation band. Computed once at first use. */
static int   g_bin_lo = -1, g_bin_hi = -1;
static float g_win[DOA_FFT_N];

static void doa_lazy_init(void)
{
    if (g_bin_lo >= 0) return;
    g_bin_lo = (DOA_BAND_LO_HZ * DOA_FFT_N) / DOA_SAMPLE_RATE;
    g_bin_hi = (DOA_BAND_HI_HZ * DOA_FFT_N) / DOA_SAMPLE_RATE;
    if (g_bin_lo < 1) g_bin_lo = 1;                  /* never include DC */
    if (g_bin_hi > DOA_FFT_N / 2) g_bin_hi = DOA_FFT_N / 2;
    for (int i = 0; i < DOA_FFT_N; ++i)
        g_win[i] = 0.5f - 0.5f * cosf(2.0f * PI_F * (float)i
                                       / (float)(DOA_FFT_N - 1));
}

float doa_max_lag_samples(void)
{
    return (DOA_SPACING_MM * 0.001f / DOA_C_SOUND) * (float)DOA_SAMPLE_RATE;
}


/* Evaluate the PHAT correlation at one lag, directly, via the shift
 * theorem:  r(tau) = sum_k R_k exp(2j pi k tau / N), summed over the
 * masked bins only.
 *
 * Evaluating lags directly rather than zero-padding the spectrum and
 * inverse-transforming avoids the question of what an index in an
 * upsampled array means - which is precisely where a first attempt at this
 * went wrong, returning 0.25 for a true delay of 1.5. */
static float phat_at_lag(const float *rr, const float *ri, float lag)
{
    float acc = 0.0f;
    for (int k = g_bin_lo; k < g_bin_hi; ++k) {
        const float ang = 2.0f * PI_F * (float)k * lag / (float)DOA_FFT_N;
        acc += rr[k] * cosf(ang) - ri[k] * sinf(ang);
    }
    return acc;
}


float doa_gcc_phat(const float *a, const float *b, float *corr)
{
    static float ar[DOA_FFT_N], ai[DOA_FFT_N];
    static float br[DOA_FFT_N], bi[DOA_FFT_N];
    static float rr[DOA_FFT_N / 2 + 1], ri[DOA_FFT_N / 2 + 1];

    doa_lazy_init();

    for (int i = 0; i < DOA_FFT_N; ++i) {
        ar[i] = a[i] * g_win[i];  ai[i] = 0.0f;
        br[i] = b[i] * g_win[i];  bi[i] = 0.0f;
    }
    sf_fft(ar, ai, DOA_FFT_N, 0);
    sf_fft(br, bi, DOA_FFT_N, 0);

    /* R = A * conj(B), then PHAT-whiten - but ONLY inside the band. Bins
     * outside hold numerical noise; whitening would raise them to unit
     * weight alongside the real ones and drown the estimate. */
    int n_bins = 0;
    for (int k = 0; k <= DOA_FFT_N / 2; ++k) {
        if (k < g_bin_lo || k >= g_bin_hi) { rr[k] = ri[k] = 0.0f; continue; }
        const float xr =  ar[k] * br[k] + ai[k] * bi[k];
        const float xi = -ar[k] * bi[k] + ai[k] * br[k];
        const float mag = sqrtf(xr * xr + xi * xi) + 1e-12f;
        rr[k] = xr / mag;
        ri[k] = xi / mag;
        ++n_bins;
    }
    if (n_bins == 0) { if (corr) *corr = 0.0f; return 0.0f; }

    /* Coarse sweep over the whole permitted range. */
    float best_lag = 0.0f, best_val = -1e30f;
    for (float lag = -DOA_MAX_LAG; lag <= DOA_MAX_LAG; lag += DOA_COARSE_STEP) {
        const float v = phat_at_lag(rr, ri, lag);
        if (v > best_val) { best_val = v; best_lag = lag; }
    }

    /* Fine sweep around the coarse winner. */
    const float lo = best_lag - DOA_COARSE_STEP;
    const float hi = best_lag + DOA_COARSE_STEP;
    for (float lag = lo; lag <= hi; lag += DOA_FINE_STEP) {
        const float v = phat_at_lag(rr, ri, lag);
        if (v > best_val) { best_val = v; best_lag = lag; }
    }

    /* Normalise by the bin count so the peak reads 0..1 and the threshold
     * means the same thing regardless of the band width. */
    if (corr) *corr = best_val / (float)n_bins;

    /* R = A conj(B) peaks at the lag by which A LEADS B, so the delay of b
     * is the negative of the peak position. Getting this backwards costs
     * you a bearing mirrored about the array axis, which looks entirely
     * plausible on screen. */
    return -best_lag;
}


void doa_estimate(const float *east, const float *west,
                  const float *north, const float *south,
                  doa_result_t *out)
{
    float cx = 0.0f, cy = 0.0f;

    memset(out, 0, sizeof(*out));
    out->angle_deg = -1.0f;

    /* Subtract the constant cross-channel offset before anything uses
     * these. The magnitude gate below and the atan2 both depend on the
     * lags being symmetric about zero, so correcting here rather than at
     * the point of use means nothing downstream needs to know. */
    out->lag_x = doa_gcc_phat(east, west, &cx)   - DOA_LAG_X_BIAS;
    out->lag_y = doa_gcc_phat(north, south, &cy) - DOA_LAG_Y_BIAS;
    out->confidence = (cx < cy) ? cx : cy;

    out->magnitude = sqrtf(out->lag_x * out->lag_x + out->lag_y * out->lag_y);

    if (out->confidence < DOA_MIN_CORR) return;

    /* The vector length cannot exceed what the geometry allows. If it does,
     * the two axes disagree and the estimate is not trustworthy however
     * strong each correlation looked on its own. 1.6x leaves room for
     * measurement error without accepting the impossible. */
    if (out->magnitude > 1.6f * doa_max_lag_samples()) return;

    out->angle_deg = atan2f(out->lag_y, out->lag_x) * 180.0f / PI_F;
    if (out->angle_deg < 0.0f) out->angle_deg += 360.0f;
    out->valid = 1;
}