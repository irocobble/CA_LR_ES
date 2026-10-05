/*
 * sar_doa.h - bearing from a 2x2 microphone square, on device.
 *
 * ============================================================================
 * WHAT THIS ARRAY CAN AND CANNOT DO  (56.6 mm spacing, 16 kHz)
 * ============================================================================
 *
 * Max delay across one axis:  40 mm / 343 m/s = 116.6 us = 1.87 SAMPLES.
 * The sample period is 62.5 us. So integer-lag correlation gives you four
 * distinct values in total - sub-sample interpolation is not an
 * optimisation here, it is the entire mechanism.
 *
 * Simulated GCC-PHAT accuracy, 64 ms windows, 300-3400 Hz:
 *
 *      SNR      delay RMSE      bearing RMSE at broadside
 *      30 dB      0.014 sm            0.4 deg
 *      20 dB      0.020 sm            0.6 deg
 *      10 dB      0.055 sm            1.7 deg
 *       5 dB      0.102 sm            3.1 deg
 *       0 dB      0.226 sm            6.9 deg
 *
 * Recorded voice-band SNR on this hardware runs 5-12 dB, so expect roughly
 * 2-3 degrees at broadside. Near endfire the array goes blind: the
 * sensitivity is d(tau)/d(theta) = (d/c) sin(theta), which vanishes as
 * theta -> 0, so a 0.1-sample error becomes 9 degrees at 20 degrees off
 * axis and worse beyond that. Report confidence, not just an angle.
 *
 * SPATIAL ALIASING above c/(2d) = 4288 Hz. The correlation band below stops
 * well short of that, which costs almost nothing because speech energy is
 * mostly under 4 kHz anyway.
 *
 * BEAMFORMING IS NOT WORTH BUILDING AT THIS SIZE. The array spans 57 mm on
 * the diagonal; at 1 kHz that is 0.17 of a wavelength. Delay-and-sum would
 * be summing four nearly identical signals - you get sqrt(4) = 6 dB of
 * noise averaging and no directivity. Call it 4-channel averaging and be
 * honest about it rather than calling it a beamformer.
 *
 * ============================================================================
 * WHY OPPOSING PAIRS
 * ============================================================================
 *
 *                 North
 *                   |
 *      West ------- + ------- East        pair X: East / West
 *                   |                     pair Y: North / South
 *                 South
 *
 *   lag_x -> cos(theta),  lag_y -> sin(theta),  theta = atan2(lag_y, lag_x)
 *
 * The scale factor cancels in that ratio, so the ANGLE does not depend on
 * knowing the spacing accurately - only on both baselines being EQUAL.
 * Measure them once and make them match; do not chase the absolute value.
 *
 * If the two pairs sit on different I2S data lines, keep each pair within
 * one line. Separate DMA streams start at slightly different moments, and
 * that offset would land directly in the delay estimate.
 *
 * ============================================================================
 * THE BAND MASK IS NOT OPTIONAL
 * ============================================================================
 *
 * PHAT divides every bin by its own magnitude. Bins outside the signal band
 * hold only numerical noise, and whitening amplifies them to unit weight
 * exactly like the real ones. With a 300-3400 Hz signal at 16 kHz most of
 * the spectrum is empty, so unmasked PHAT is dominated by random phase.
 *
 * Measured: unmasked, a true delay of 1.50 samples was estimated as 0.14.
 * Masked, the same signal gave 1.49. That is the difference between a
 * working direction finder and a random number generator.
 */

#ifndef SAR_DOA_H
#define SAR_DOA_H

#include <stdint.h>

#define DOA_SAMPLE_RATE   16000
#define DOA_FFT_N         1024      /* 64 ms - matches the simulation above */
/* MEASURE THIS with a ruler, between the actual microphone ports. If the
 * mics are on breakout boards rather than a PCB, this is very likely 100 mm
 * or more, not 40. Getting it wrong does not just scale the answer - it puts
 * the true delay outside the lag search, and the correlator then returns a
 * NEGATIVE peak, which the confidence gate rejects. Symptom: everything else
 * works and no bearing ever appears. */
#define DOA_SPACING_MM    56.6f
#define DOA_C_SOUND       343.0f

/* Correlate over the speech band only. Upper edge stays below the 4288 Hz
 * aliasing limit for 40 mm spacing. */
#define DOA_BAND_LO_HZ    300
#define DOA_BAND_HI_HZ    3400

/* Lag search, derived from the spacing rather than hard-coded, with 40%
 * headroom for reverberation and measurement error. Hard-coding 2.5 meant
 * that changing DOA_SPACING_MM silently did nothing to the search range. */
#define DOA_MAX_LAG  ((DOA_SPACING_MM * 0.001f / DOA_C_SOUND) * \
                      (float)DOA_SAMPLE_RATE * 1.4f)
#define DOA_COARSE_STEP   0.10f
#define DOA_FINE_STEP     0.01f

/* Below this correlation peak the estimate is not worth reporting. */
/* PHAT peaks are lower than raw correlation because whitening discards
 * amplitude. 0.20 was optimistic for a real room; 0.08 still rejects noise
 * while accepting a genuine but reverberant source. */
#define DOA_MIN_CORR      0.08f

/* ---- Cross-channel timing bias ----------------------------------------
 *
 * The K210's two I2S channels do not start on the same word-select edge.
 * Whatever offset they begin with persists for the whole run, and it rides
 * on every delay measured ACROSS them.
 *
 * On the current wiring the E/W pair is slots 0 and 3 and the N/S pair is
 * slots 1 and 2 - both diagonals straddle both channels, so BOTH axes
 * carry the offset. Measured from four cardinal claps:
 *
 *       true dir   lag_x   lag_y
 *          E       +1.58   +0.72
 *          N       -0.31   +2.92
 *          W       -3.44   +0.65
 *          S       -1.10   -1.02
 *
 * lag_x spans -3.44..+1.58, centre -0.93; lag_y spans -1.02..+2.92, centre
 * +0.95. Both should be symmetric about zero. lag_x also reached -3.44,
 * beyond the 2.64-sample physical maximum for 56.6 mm - that is not sound,
 * it is the offset.
 *
 * TO REFINE: put a source equidistant from all four microphones (directly
 * above the array centre). Every true delay is then zero, so whatever
 * lag_x and lag_y read IS the bias. That is a far better measurement than
 * inferring it from four claps.
 *
 * THE REAL FIX, for the PCB: keep each opposing pair on ONE data line, so
 * no measurement ever spans two channels and no calibration is needed. */
#define DOA_LAG_X_BIAS   (-0.93f)
#define DOA_LAG_Y_BIAS   (+0.95f)

typedef struct {
    float angle_deg;    /* 0-360, or -1 when no confident estimate       */
    float lag_x;        /* East-West delay, samples                      */
    float lag_y;        /* North-South delay, samples                    */
    float confidence;   /* min of the two normalised correlation peaks   */
    float magnitude;    /* sqrt(lag_x^2 + lag_y^2), samples              */
    int   valid;        /* 0 when gated out - read this before angle_deg */
} doa_result_t;

/* One estimate from four aligned DOA_FFT_N-sample windows.
 *
 * The four buffers MUST be the same instant of time in each microphone -
 * that simultaneity is the whole basis of the measurement. Feed them
 * straight from one DMA block, de-interleaved, not from separate captures.
 *
 * Inputs should already be high-passed (sf_highpass) but NOT normalised:
 * PHAT removes amplitude anyway, and per-channel gain differences would
 * corrupt nothing here but would corrupt any later beamforming.
 */
void doa_estimate(const float *east, const float *west,
                  const float *north, const float *south,
                  doa_result_t *out);

/* Band-masked GCC-PHAT for one pair. Returns the delay of b relative to a,
 * in samples, and writes the normalised peak height to *corr.
 * Exposed separately so the bench test can check one axis at a time. */
float doa_gcc_phat(const float *a, const float *b, float *corr);

/* Largest delay the geometry permits, in samples. Anything beyond this is
 * a bad estimate, not a distant source. */
float doa_max_lag_samples(void);

#endif /* SAR_DOA_H */