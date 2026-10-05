/*
 * main.c - K210 acoustic survivor detector. Everything runs on device.
 *
 * Captures four microphones SIMULTANEOUSLY, runs the three-feature voice
 * detector on one of them, and when it fires, computes a bearing from all
 * four. Sends a 16-byte result packet, not audio.
 *
 * ============================================================================
 * WHY SIMULTANEOUS, WHERE THE PREVIOUS BUILD CYCLED
 * ============================================================================
 * k210_1mic.c streams one mic at a time so the link stays at 21%
 * utilisation. That was right for collecting a per-mic dataset and for
 * proving the wiring. It is useless for direction finding: TDOA measures
 * the delay between microphones, so they must be the same instant.
 *
 * Streaming four channels would need 128 kB/s, 85% of the 1.5 Mbaud link.
 * Computing on device sidesteps that entirely - the bearing is 16 bytes at
 * 10 Hz, about 160 B/s, 0.1% of the link.
 *
 * ============================================================================
 * COST, MEASURED
 * ============================================================================
 *   DOA    4 x 1024-pt FFT + 2 lag searches = 110 k ops per estimate
 *          at 10 Hz that is 0.28% of a 400 MHz core
 *   VAD    harmonicity dominates: 227 lags x 512 samples x 31 frames
 *          ~3.6 M MAC per second of audio, roughly 1% duty cycle
 *   Total well under 2%. There is no need to economise here.
 *
 * ============================================================================
 * BEFORE THIS WILL MEAN ANYTHING
 * ============================================================================
 *   1. All four slots must pass the probe in k210_1mic.c. A wrong slot map
 *      gives a confident bearing rotated by an unknown amount, and nothing
 *      on screen will hint at it.
 *   2. MIC_EAST/WEST/NORTH/SOUTH below must match the physical positions.
 *      Tap each mic and confirm against the #tap lines.
 *   3. Both baselines must be EQUAL. The angle comes from a ratio of two
 *      delays, so the absolute spacing cancels - but only if dx == dy.
 * ========================================================================= */

#include <stdint.h>
#include <string.h>
#include <math.h>      /* log10f - the SDK builds with -Werror */
#include "fpioa.h"
#include "i2s.h"
#include "dmac.h"
#include "sysctl.h"
#include "uarths.h"

#include "sar_features.h"
#include "sar_doa.h"
#include "vad3_model.h"

/* ---- Pins: match what the Recorder proved ------------------------------ */
#define IO_SCLK      18
#define IO_WS        19
#define IO_SD1       23        /* I2S_CHANNEL_0 -> slots 0, 1 */
#define IO_SD2       22        /* I2S_CHANNEL_1 -> slots 2, 3 */

#define SAMPLE_SHIFT 13        /* smallest value with clip=0 on every slot */

/* Slot -> physical position, from the tap test.
 *
 * The two pairs must be OPPOSING corners, and the pairs perpendicular to
 * each other - that is what makes atan2(lag_y, lag_x) a bearing rather
 * than a number. Slots 0/3 are one diagonal, 1/2 the other. */
#define MIC_EAST     0
#define MIC_WEST     3
#define MIC_NORTH    1
#define MIC_SOUTH    2

#define NUM_SLOTS    4
#define BLK          256
#define SAMPLE_RATE  16000
#define SAMPLE_SHIFT 13        /* set from the k210_1mic.c #shift scan      */
#define DMA_CH       DMAC_CHANNEL1
#define PLL2_FREQ    49152000UL
#define BAUD_RATE    1500000UL



/* Which mic feeds the voice detector. Any will do; East is arbitrary. */
#define MIC_VAD      MIC_EAST

/* Emit a packet at most this often, and only while speech is detected. */
#define REPORT_HZ    20
#define BLOCKS_PER_REPORT ((SAMPLE_RATE / BLK) / REPORT_HZ)

/* Compute a bearing for ANY sound above this level, not only for speech.
 *
 * Gating DOA on the VAD was wrong for this application. The display shows
 * human AND non-human sources, so the bearing has to exist before the
 * classifier has an opinion - otherwise a machine running nearby is
 * invisible rather than shown as a blue circle. It also means one
 * mis-tuned threshold makes the whole screen empty, which is exactly what
 * happened.
 *
 * So: localise everything audible, classify separately, and let the host
 * decide where the human/non-human line sits. -45 dBFS is above the mic's
 * own noise floor but well below normal speech. */
#define DOA_LEVEL_GATE_DB  (-45.0f)

/* Stream one microphone's audio alongside the DOA packets. Costs 32 kB/s
 * against a 150 kB/s link - 21% utilisation, the same as the Recorder
 * build - so you can watch a waveform and a bearing at the same time.
 *
 * Set to 0 to send bearings only (160 B/s), which is what a deployed node
 * would do over LoRa. */
#define STREAM_AUDIO   1

/* Send a packet even with no detection, so the host can see we are alive.
 * Counted in REPORT ticks, not raw blocks: a block-based counter only fires
 * when it happens to coincide with a report tick, so most heartbeats are
 * silently skipped and the link looks dead during quiet periods. */
#define HEARTBEAT_REPORTS  REPORT_HZ               /* once a second */

static uint32_t g_raw[2][BLK * NUM_SLOTS];

/* DOA needs the last DOA_FFT_N samples of every mic, aligned. */
static float g_doa_ring[NUM_SLOTS][DOA_FFT_N];
static int   g_doa_head;

/* The VAD needs a 1 s window of one mic. */
static float g_vad_ring[SF_WINDOW];
static int   g_vad_head;
static int   g_vad_filled;

static sf_state_t g_sf[NUM_SLOTS];
static uint8_t    g_seq;


/* ============================================================================
 * Text and packet output
 * ========================================================================= */
static void put(const char *s) { while (*s) uarths_putchar(*s++); }

static void put_u32(uint32_t v)
{
    char b[11]; int i = 10; b[i] = 0;
    if (!v) { uarths_putchar('0'); return; }
    while (v && i) { b[--i] = (char)('0' + v % 10); v /= 10; }
    put(&b[i]);
}

/* Result packet, 16 bytes:
 *
 *   0-1  magic 0x5A 0xA6  (distinct from the audio magic 0x5A 0xA5)
 *   2    type 0x10 (detection report)
 *   3    seq, wraps at 256
 *   4-5  angle x10, int16, -10 means no confident bearing
 *   6-7  doa confidence x1000, int16
 *   8-9  vad score x100, int16 (the smoothed logit)
 *   10-11 level dBFS x10, int16
 *   12   speech flag
 *   13   warm flag - 0 while the noise floor is still filling
 *   14-15 lag_x x100, int16   } the raw per-axis delays, so a bad bearing
 *   16-17 lag_y x100, int16   } can be diagnosed without reflashing
 *   18    xor8 of bytes 2..17
 *   19    pad
 *
 * Same magic word as the audio format, but a distinct type byte, so a host
 * reading either can tell them apart without guessing from the length. */
/* Audio frame, byte-identical to the Recorder build's format so record.py
 * reads it unchanged:
 *   [0x5A][0xA5][seq:u8][blk:u16 LE][nch:u8][payload][sum16 LE]
 *
 * Note the DOA packet below uses a DIFFERENT magic word, 0x5A 0xA6. Sharing
 * one magic and distinguishing by a type byte would collide whenever an
 * audio sequence number happened to equal that type value - rare, and
 * therefore exactly the kind of fault that appears once during a demo and
 * cannot be reproduced afterwards. */
static uint8_t g_aseq;

static void send_audio(const float *x, int n)
{
    uint16_t sum = 0;
    uarths_putchar(0x5A);
    uarths_putchar(0xA5);
    uarths_putchar((char)g_aseq++);
    uarths_putchar((char)(n & 0xFF));
    uarths_putchar((char)((n >> 8) & 0xFF));
    uarths_putchar((char)1);
    for (int i = 0; i < n; ++i) {
        float v = x[i] * 32768.0f;
        if (v >  32767.0f) v =  32767.0f;
        if (v < -32768.0f) v = -32768.0f;
        const int16_t s = (int16_t)v;
        const uint8_t lo = (uint8_t)(s & 0xFF), hi = (uint8_t)((s >> 8) & 0xFF);
        uarths_putchar((char)lo); sum = (uint16_t)(sum + lo);
        uarths_putchar((char)hi); sum = (uint16_t)(sum + hi);
    }
    uarths_putchar((char)(sum & 0xFF));
    uarths_putchar((char)((sum >> 8) & 0xFF));
}

static void send_report(float angle, float conf, float score,
                        float level_db, int speech, int warm,
                        float lag_x, float lag_y)
{
    uint8_t p[20];
    int16_t a10 = (int16_t)(angle * 10.0f);
    int16_t c1k = (int16_t)(conf * 1000.0f);
    int16_t s100 = (int16_t)(score * 100.0f);
    int16_t l10 = (int16_t)(level_db * 10.0f);

    p[0] = 0x5A; p[1] = 0xA6; p[2] = 0x10; p[3] = g_seq++;
    p[4] = (uint8_t)(a10 & 0xFF);  p[5] = (uint8_t)((a10 >> 8) & 0xFF);
    p[6] = (uint8_t)(c1k & 0xFF);  p[7] = (uint8_t)((c1k >> 8) & 0xFF);
    p[8] = (uint8_t)(s100 & 0xFF); p[9] = (uint8_t)((s100 >> 8) & 0xFF);
    p[10] = (uint8_t)(l10 & 0xFF); p[11] = (uint8_t)((l10 >> 8) & 0xFF);
    p[12] = (uint8_t)speech;
    p[13] = (uint8_t)warm;
    int16_t lx = (int16_t)(lag_x * 100.0f), ly = (int16_t)(lag_y * 100.0f);
    p[14] = (uint8_t)(lx & 0xFF); p[15] = (uint8_t)((lx >> 8) & 0xFF);
    p[16] = (uint8_t)(ly & 0xFF); p[17] = (uint8_t)((ly >> 8) & 0xFF);
    uint8_t x = 0;
    for (int i = 2; i < 18; ++i) x ^= p[i];
    p[18] = x; p[19] = 0;
    for (int i = 0; i < 20; ++i) uarths_putchar((char)p[i]);
}


/* ============================================================================
 * Setup
 * ========================================================================= */
static void clocks_init(void)
{
    /* PLL0/PLL1 before uarths_config: the UART divisor is computed from the
     * CPU clock at that moment, so changing PLL0 later invalidates it. */
    sysctl_pll_set_freq(SYSCTL_PLL0, 320000000UL);
    sysctl_pll_set_freq(SYSCTL_PLL1, 160000000UL);
    sysctl_pll_set_freq(SYSCTL_PLL2, PLL2_FREQ);
}

static void uart_init(void)
{
    fpioa_set_function(4, FUNC_UARTHS_RX);
    fpioa_set_function(5, FUNC_UARTHS_TX);
    uarths_init();
    uarths_config(BAUD_RATE, UARTHS_STOP_1);
}

static void pins_init(void)
{
    fpioa_set_function(IO_SCLK, FUNC_I2S0_SCLK);
    fpioa_set_function(IO_WS,   FUNC_I2S0_WS);
    fpioa_set_function(IO_SD1,  FUNC_I2S0_IN_D0);
    fpioa_set_function(IO_SD2,  FUNC_I2S0_IN_D1);
}

static void i2s_setup(void)
{
    dmac_init();
    i2s_init(I2S_DEVICE_0, I2S_RECEIVER, 0xF);   /* 2 bits per stereo chan */

    i2s_rx_channel_config(I2S_DEVICE_0, I2S_CHANNEL_0,
                          RESOLUTION_32_BIT, SCLK_CYCLES_32,
                          TRIGGER_LEVEL_4, STANDARD_MODE);
    i2s_rx_channel_config(I2S_DEVICE_0, I2S_CHANNEL_1,
                          RESOLUTION_32_BIT, SCLK_CYCLES_32,
                          TRIGGER_LEVEL_4, STANDARD_MODE);

    /* Returns the BIT clock, not the sample rate - divide by 64 and allow
     * the PLL's rounding, or a healthy chain reports a mismatch over an
     * inaudible 0.08%. */
    const uint32_t bclk = i2s_set_sample_rate(I2S_DEVICE_0, SAMPLE_RATE);
    put("#i2s rate="); put_u32(bclk / 64u);
    put((bclk / 64u > 15900u && bclk / 64u < 16100u) ? " OK\r\n" : " *** BAD ***\r\n");
}


/* ============================================================================
 * Main
 * ========================================================================= */
int main(void)
{
    clocks_init();
    uart_init();
    put("\r\n#K210 detector: 4 mics simultaneous, VAD + DOA on device\r\n");
    put("#spacing_mm="); put_u32((uint32_t)DOA_SPACING_MM);
    put(" report_hz=");  put_u32(REPORT_HZ);
    put(" max_lag_x100="); put_u32((uint32_t)(DOA_MAX_LAG * 100.0f));
    put("\r\n#features:");
    for (int i = 0; i < VAD_N_FEATURES; ++i) {
        put(" "); put(VAD_FEATURE_NAMES[i]);
    }
    put("\r\n");

    pins_init();
    i2s_setup();

    for (int m = 0; m < NUM_SLOTS; ++m) sf_init(&g_sf[m]);
    memset(g_doa_ring, 0, sizeof(g_doa_ring));
    memset(g_vad_ring, 0, sizeof(g_vad_ring));
    g_doa_head = g_vad_head = g_vad_filled = 0;
    g_seq = 0;
    g_aseq = 0;

    static float chan[NUM_SLOTS][BLK];
    static float win[SF_WINDOW];
    static float dwin[NUM_SLOTS][DOA_FFT_N];

    int cur = 0;
    uint32_t blocks = 0;
    uint32_t reports = 0;
    float last_score = 0.0f;
    int   last_speech = 0;

    i2s_receive_data_dma(I2S_DEVICE_0, g_raw[cur], BLK * NUM_SLOTS, DMA_CH);
    put("#running\r\n");

    for (;;) {
        dmac_wait_done(DMA_CH);
        const int done = cur;
        cur ^= 1;
        /* Re-arm BEFORE processing, or capture stops for the duration and
         * you get dropouts with no error anywhere. */
        i2s_receive_data_dma(I2S_DEVICE_0, g_raw[cur], BLK * NUM_SLOTS, DMA_CH);

        /* --- de-interleave. All NUM_SLOTS words in a frame were captured on
         * the same word-select period, i.e. the same instant. That
         * simultaneity is the entire basis of the bearing, so the layout is
         * taken exactly as the hardware produced it. --- */
        for (int f = 0; f < BLK; ++f) {
            for (int m = 0; m < NUM_SLOTS; ++m) {
                int32_t v = (int32_t)g_raw[done][f * NUM_SLOTS + m] >> SAMPLE_SHIFT;
                if (v >  32767) v =  32767;      /* saturate, never truncate */
                if (v < -32768) v = -32768;
                chan[m][f] = (float)v / 32768.0f;
            }
        }

        /* --- high-pass each channel with ITS OWN filter state, identical
         * coefficients. Same filter means same phase shift on every channel,
         * so the DIFFERENCE between them - the only thing DOA measures -
         * survives untouched. Different coefficients per channel would
         * destroy the array while each waveform still looked healthy. --- */
        for (int m = 0; m < NUM_SLOTS; ++m) {
            sf_highpass(&g_sf[m], chan[m], BLK);
            for (int f = 0; f < BLK; ++f)
                g_doa_ring[m][(g_doa_head + f) % DOA_FFT_N] = chan[m][f];
        }
        g_doa_head = (g_doa_head + BLK) % DOA_FFT_N;

        /* --- VAD channel: noise floor first, then the ring --- */
        sf_update_noise_floor(&g_sf[MIC_VAD], chan[MIC_VAD], BLK);
        for (int f = 0; f < BLK; ++f)
            g_vad_ring[(g_vad_head + f) % SF_WINDOW] = chan[MIC_VAD][f];
        g_vad_head = (g_vad_head + BLK) % SF_WINDOW;
        if (g_vad_filled < SF_WINDOW) g_vad_filled += BLK;

#if STREAM_AUDIO
        /* Audio goes out every block. The DOA gate below only controls how
         * often a BEARING is computed - it must not gate the audio, or the
         * waveform arrives in 100 ms bursts with gaps. */
        send_audio(chan[MIC_VAD], BLK);
#endif

        blocks++;
        if ((blocks % BLOCKS_PER_REPORT) != 0) continue;
        if (g_vad_filled < SF_WINDOW) continue;
        reports++;

        /* --- unwrap the VAD ring oldest-first --- */
        for (int i = 0; i < SF_WINDOW; ++i)
            win[i] = g_vad_ring[(g_vad_head + i) % SF_WINDOW];

        float feat[SF_N_FEATURES];
        sf_extract(&g_sf[MIC_VAD], win, feat);

        /* vad3_model.h has the scaler folded into the weights - do NOT
         * normalise here, just dot and smooth the SCORE (not the boolean),
         * which is how the model was evaluated during training. */
        float score = vad_score(feat);
        score = sf_smooth_score(&g_sf[MIC_VAD], score, VAD_SMOOTH_K);
        const int speech = vad_is_speech(score);
        const int warm = sf_warm(&g_sf[MIC_VAD]);
        last_score = score;

        /* --- bearing, only when there is something worth pointing at.
         * Skipping DOA on silence is not about saving cycles - it stops the
         * correlator locking onto room noise and reporting a confident
         * direction to nothing. --- */
        float p = 0.0f;
        for (int i = 0; i < SF_WINDOW; ++i) p += win[i] * win[i];
        const float level_db = 10.0f * log10f(p / (float)SF_WINDOW + 1e-12f);

        doa_result_t doa;
        memset(&doa, 0, sizeof(doa));
        doa.angle_deg = -1.0f;

        if (warm && level_db > DOA_LEVEL_GATE_DB) {
            for (int m = 0; m < NUM_SLOTS; ++m)
                for (int i = 0; i < DOA_FFT_N; ++i)
                    dwin[m][i] = g_doa_ring[m][(g_doa_head + i) % DOA_FFT_N];
            doa_estimate(dwin[MIC_EAST], dwin[MIC_WEST],
                         dwin[MIC_NORTH], dwin[MIC_SOUTH], &doa);
        }

        /* Report every tick once warm. At 10 Hz that is 160 B/s - there is
         * no reason to be stingy, and a screen that only updates when the
         * VAD agrees looks broken while you are tuning it. */
        if (warm || (reports % HEARTBEAT_REPORTS) == 0) {
            send_report(doa.valid ? doa.angle_deg : -1.0f,
                        doa.confidence, score, level_db, speech, warm,
                        doa.lag_x, doa.lag_y);
        }
        last_speech = speech;
        (void)last_score; (void)last_speech;
    }
    return 0;
}
