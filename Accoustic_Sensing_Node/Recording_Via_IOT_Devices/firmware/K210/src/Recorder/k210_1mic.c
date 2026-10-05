/*
 * main.c - K210 4-mic array. Probes first, then streams ONE mic at a time.
 *
 * ============================================================================
 * WHAT THIS BUILD ADDS
 * ============================================================================
 *
 * (1) "#switch mic=0" IS NOW SENT BEFORE THE FIRST FRAME.  [BUG FIX]
 *
 *     The previous version only printed that line when the mic CHANGED. So
 *     the first window - three whole seconds of mic 0 - arrived at the host
 *     with no marker in front of it. record.py starts with current_mic =
 *     None and tags anything before the first marker as "UNKNOWN", so
 *     mic 0's opening audio went into a file called UNKNOWN and vanished
 *     from the per-mic split. Nothing errored. The recording just quietly
 *     lost its first slice.
 *
 * (2) LIVE MIC SELECTION over the same UART.
 *
 *     Send a key while it runs:
 *         0 1 2 3   lock onto that mic and stay there
 *         c         resume cycling every SWITCH_SECONDS
 *         p         print one #stat line per slot without stopping
 *         ?         reprint the key list
 *
 *     Locking is what you want for "listen to one mic at a time": pick a
 *     mic, leave it, tap that microphone, confirm the audio moves. Cycling
 *     is for unattended capture of all four in one pass.
 *
 *     Every selection re-emits "#switch mic=N", so the host stays in step
 *     whether the change came from the timer or from your keyboard.
 *
 * (3) DIAGNOSTICS RUN BEFORE STREAMING, NOT DURING.
 *
 *     Previously the raw dump and shift scan fired at block 4 - after four
 *     binary frames had already gone out - injecting text into the middle
 *     of the stream. record.py survives that, because it resyncs on the
 *     magic word and harvests '#' lines, but it is needless mess. There is
 *     now an explicit probe phase, then a single transition, then binary.
 *
 * (4) SAMPLE_SHIFT IS 13, NOT 15.
 *
 *     Measured on this board at 15: peak p2p 5005 of 65535, i.e. 7.6% of
 *     full scale, about four of sixteen bits doing any work. The boot scan
 *     showed clip=9 at shift 12 and clip=0 at 14, so 13 is the smallest
 *     safe step. Re-read the #shift lines after this change - the second
 *     data line was not part of the run that number came from.
 *
 * ============================================================================
 * THE FAULTS THE PROBE DISTINGUISHES
 * ============================================================================
 *   L/R STRAPPING. Two INMP441s share one SD line by taking opposite halves
 *     of the frame: one L/R to GND, the other to VDD. Strapped alike, they
 *     drive the same half simultaneously - two push-pull outputs fighting.
 *     That slot reads garbage, the other reads nothing.
 *   WRONG PIN. 18/19/20/21 are proven on this board.
 *   RESOLUTION vs SHIFT. 16_BIT puts the sample in the LOW 16 bits; 32_BIT
 *     puts 24 bits MSB-aligned high. One function below owns that
 *     conversion so the two cannot drift apart.
 *   WRAPPING vs CLIPPING. A cast to int16_t truncates, it does not clamp -
 *     0x3F0000 >> 13 = 129024 becomes -2048, so an overloaded channel
 *     turns into a sawtooth that looks exactly like a floating pin.
 *     word_to_sample saturates instead.
 *
 * ============================================================================
 * SEQUENCE
 * ============================================================================
 *   PROBE_SECONDS of text-only diagnostics, then streaming begins.
 *   Set PROBE_SECONDS to 0 once you trust the wiring.
 *   Set MIC_SLOT_OK to 0 to stay in the probe forever.
 */

#include <stdint.h>
#include <string.h>
#include "fpioa.h"
#include "i2s.h"
#include "dmac.h"
#include "sysctl.h"
#include "uarths.h"

/* ---- Build switches ---------------------------------------------------- */
#define NUM_DATA_LINES   2      /* both SD lines -> 4 mic slots             */
#define MIC_SLOT_OK      0      /* 0 = probe forever, 1 = probe then stream */

#define PROBE_SECONDS    6      /* text-only diagnostics before streaming   */

/* Which mic to start on, and whether to cycle.
 * START_MIC 0..NUM_SLOTS-1. START_CYCLING 0 means lock to START_MIC until
 * you press 'c'. Either can be changed live from the keyboard. */
#define START_MIC        0
#define START_CYCLING    1

#define USE_16_BIT       0      /* 0 = RESOLUTION_32_BIT, 1 = 16_BIT        */

#if USE_16_BIT
#  define SAMPLE_SHIFT   0      /* sample already sits in the low 16 bits   */
#else
/* 16 is unity for a 24-bit MSB-aligned sample; lower means more gain.
 * 13 measured as the smallest safe value on this board - see note (4). */
#  define SAMPLE_SHIFT   13
#endif

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

#define SAMPLE_RATE  16000
#define BLK          256
#define NUM_SLOTS    (NUM_DATA_LINES * 2)

/* One I2S frame is two 32-bit slots, so the bit clock is 64x the sample
 * rate. This is what i2s_set_sample_rate actually returns. */
#define BCLK_PER_SAMPLE  64

#define DMA_CH       DMAC_CHANNEL1
#define PLL2_FREQ    49152000UL
#define BAUD_RATE    1500000UL

#define SWITCH_SECONDS  3
#define BLOCKS_PER_SEC  (SAMPLE_RATE / BLK)
#define BLOCKS_PER_MIC  ((SWITCH_SECONDS * SAMPLE_RATE + BLK/2) / BLK)
#define PROBE_BLOCKS    ((uint32_t)PROBE_SECONDS * BLOCKS_PER_SEC)

/* Diagnostics ignore this many blocks at startup: whatever the DMA caught
 * before the clock settled is garbage, and reporting it as a fault sends
 * you chasing a problem that does not exist. */
#define SETTLE_BLOCKS  4

static uint32_t g_raw[2][BLK * NUM_SLOTS];
static int16_t  g_pcm[BLK];
static uint8_t  g_seq;

static uint8_t  g_mic     = START_MIC;
static int      g_cycling = START_CYCLING;
static int      g_want_stats = 0;


/* ============================================================================
 * Text output
 * ========================================================================= */
static void put(const char *s) { while (*s) uarths_putchar(*s++); }

static void put_hex32(uint32_t v)
{
    static const char H[] = "0123456789abcdef";
    for (int i = 28; i >= 0; i -= 4) uarths_putchar(H[(v >> i) & 0xF]);
}

static void put_u32(uint32_t v)
{
    char b[11]; int i = 10; b[i] = 0;
    if (!v) { uarths_putchar('0'); return; }
    while (v && i) { b[--i] = (char)('0' + v % 10); v /= 10; }
    put(&b[i]);
}

static void put_i32(int32_t v)
{
    if (v < 0) { uarths_putchar('-'); put_u32((uint32_t)(-v)); }
    else       { put_u32((uint32_t)v); }
}

/* The host keys its per-mic split off this exact line, so it is emitted
 * from ONE place - on the timer, on a keypress, and once before the first
 * frame ever goes out. */
static void announce_mic(void)
{
    put("#switch mic="); put_u32((uint32_t)g_mic); put("\r\n");
}


/* ============================================================================
 * Sample extraction - ONE definition, driven by USE_16_BIT
 * ========================================================================= */
static int32_t word_to_wide(uint32_t w)
{
#if USE_16_BIT
    return (int32_t)(int16_t)(w & 0xFFFFu);
#else
    return (int32_t)w >> SAMPLE_SHIFT;
#endif
}

/* SATURATE, do not truncate. A cast to int16_t keeps the low 16 bits and
 * discards the rest - it does NOT clamp, so an overloaded sample wraps to
 * the opposite rail and a sine becomes a sawtooth. A sawtooth has
 * mean|x|/peak near 0.5, the same fingerprint as random noise, so a
 * wrapping channel looks exactly like a floating pin while the clip counter
 * reads zero. Clamping turns that into an honest, countable failure. */
static int16_t word_to_sample(uint32_t w)
{
    const int32_t v = word_to_wide(w);
    if (v >  32767) return  32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}


/* ============================================================================
 * Setup - one call per line
 * ========================================================================= */
static void clocks_init(void)
{
    /* PLL0 and PLL1 BEFORE uarths_config: the UART divisor is computed from
     * the CPU clock at the moment it is configured, so changing PLL0 after
     * that silently invalidates the baud rate. */
    sysctl_pll_set_freq(SYSCTL_PLL0, 320000000UL);
    sysctl_pll_set_freq(SYSCTL_PLL1, 160000000UL);

    /* PLL2 feeds the I2S divider. BCLK = 16000 * 64 = 1.024 MHz, and
     * 49.152 / 1.024 = 48 exactly. The PLL lands NEAR this, not on it. */
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
#if NUM_DATA_LINES >= 2
    fpioa_set_function(IO_SD2,  FUNC_I2S0_IN_D1);
#endif
}

static void i2s_setup(void)
{
    dmac_init();

    /* Channel mask is TWO bits per stereo channel: 0x3 for one data line,
     * 0xF for two. Literals rather than arithmetic, so what is enabled is
     * visible at a glance. */
#if NUM_DATA_LINES == 1
    i2s_init(I2S_DEVICE_0, I2S_RECEIVER, 0x3);
#else
    i2s_init(I2S_DEVICE_0, I2S_RECEIVER, 0xF);
#endif

#if USE_16_BIT
    i2s_rx_channel_config(I2S_DEVICE_0, I2S_CHANNEL_0,
                          RESOLUTION_16_BIT, SCLK_CYCLES_32,
                          TRIGGER_LEVEL_4, STANDARD_MODE);
#  if NUM_DATA_LINES >= 2
    i2s_rx_channel_config(I2S_DEVICE_0, I2S_CHANNEL_1,
                          RESOLUTION_16_BIT, SCLK_CYCLES_32,
                          TRIGGER_LEVEL_4, STANDARD_MODE);
#  endif
#else
    i2s_rx_channel_config(I2S_DEVICE_0, I2S_CHANNEL_0,
                          RESOLUTION_32_BIT, SCLK_CYCLES_32,
                          TRIGGER_LEVEL_4, STANDARD_MODE);
#  if NUM_DATA_LINES >= 2
    i2s_rx_channel_config(I2S_DEVICE_0, I2S_CHANNEL_1,
                          RESOLUTION_32_BIT, SCLK_CYCLES_32,
                          TRIGGER_LEVEL_4, STANDARD_MODE);
#  endif
#endif

    /* Returns the BIT clock, not the sample rate. Divide by 64 first and
     * allow the PLL's rounding, so a healthy chain stops reporting a
     * mismatch over an inaudible 0.08%. */
    const uint32_t bclk = i2s_set_sample_rate(I2S_DEVICE_0, SAMPLE_RATE);
    const uint32_t rate = bclk / BCLK_PER_SAMPLE;

    put("#pll2 requested="); put_u32((uint32_t)PLL2_FREQ);
    put(" actual=");         put_u32(sysctl_pll_get_freq(SYSCTL_PLL2));
    put("\r\n#i2s bclk=");   put_u32(bclk);
    put(" rate=");           put_u32(rate);
    put(" wanted=");         put_u32(SAMPLE_RATE);
    put((rate > 15900u && rate < 16100u)
        ? "  OK\r\n"
        : "  *** MISMATCH - fix this before reading anything below ***\r\n");
}


/* ============================================================================
 * Diagnostics
 * ========================================================================= */
static void dump_raw(const uint32_t *raw)
{
    for (int slot = 0; slot < NUM_SLOTS; ++slot) {
        put("#raw slot"); put_u32((uint32_t)slot); put(":");
        for (int f = 0; f < 6; ++f) { put(" "); put_hex32(raw[f * NUM_SLOTS + slot]); }
        put("\r\n");
    }
}

/* Verdicts run from most specific to least, because several can be true at
 * once and the most actionable one should win. */
static void slot_stats(const uint32_t *raw)
{
    for (int slot = 0; slot < NUM_SLOTS; ++slot) {
        int32_t  lo = 2147483647, hi = -2147483648;
        uint32_t zeros = 0, ones = 0, clipped = 0;
        int64_t  absum = 0;

        for (int f = 0; f < BLK; ++f) {
            const uint32_t w = raw[f * NUM_SLOTS + slot];
            if (w == 0u)          zeros++;
            if (w == 0xFFFFFFFFu) ones++;

            /* Count saturation on the WIDE value, before clamping. Testing
             * the clamped sample would miss every overload. */
            const int32_t wide = word_to_wide(w);
            if (wide > 32767 || wide < -32768) clipped++;

            const int32_t s = (int32_t)word_to_sample(w);
            if (s < lo) lo = s;
            if (s > hi) hi = s;
            absum += (s < 0 ? -s : s);
        }

        const uint32_t mean = (uint32_t)(absum / BLK);
        const int32_t  p2p  = hi - lo;

        put("#stat slot"); put_u32((uint32_t)slot);
        put(" mean=");     put_u32(mean);
        put(" min=");      put_i32(lo);
        put(" max=");      put_i32(hi);
        put(" p2p=");      put_i32(p2p);
        put(" clip=");     put_u32(clipped);
        put(" zero=");     put_u32(zeros);
        put(" ones=");     put_u32(ones);
        if (slot == g_mic) put(" *");

        if (zeros > (uint32_t)BLK - 4) {
            put("   UNDRIVEN - no clock, no power, or wrong pin");
        } else if (ones > (uint32_t)BLK - 4) {
            put("   FLOATING-HIGH - SD not connected");
        } else if (clipped > (uint32_t)(BLK / 20)) {
            put("   CLIPPING - raise SAMPLE_SHIFT");
        } else if (p2p > 60000 && mean > 15000u && mean < 17500u) {
            /* Uniform random data has mean|x| almost exactly half of peak
             * (16384 on the int16 range) and never varies. Real audio has a
             * crest factor and sits far below that. A floating pin with no
             * pull resistor produces exactly this and passes both checks
             * above. */
            put("   FLOATING/RANDOM - nothing driving this slot");
        } else if (p2p < 32) {
            put("   SILENT");
        }
        put("\r\n");
    }
    put("\r\n");
}

static void shift_scan(const uint32_t *raw)
{
#if !USE_16_BIT
    for (int slot = 0; slot < NUM_SLOTS; ++slot) {
        for (int sh = 11; sh <= 17; sh += 2) {
            int32_t lo = 32767, hi = -32768;
            uint32_t clipped = 0;
            for (int f = 0; f < BLK; ++f) {
                const int32_t wide = (int32_t)raw[f * NUM_SLOTS + slot] >> sh;
                if (wide > 32767 || wide < -32768) clipped++;
                const int32_t v = wide >  32767 ?  32767 :
                                  wide < -32768 ? -32768 : wide;
                if (v < lo) lo = v;
                if (v > hi) hi = v;
            }
            put("#shift slot"); put_u32((uint32_t)slot);
            put(" sh=");        put_u32((uint32_t)sh);
            put(" min=");       put_i32(lo);
            put(" max=");       put_i32(hi);
            put(" clip=");      put_u32(clipped);
            put("\r\n");
        }
    }
    put("#pick the smallest sh with clip=0 on every slot\r\n");
#else
    (void)raw;
    put("#shift n/a for 16-bit resolution\r\n");
#endif
}


/* ============================================================================
 * Keyboard control
 *
 * uarths_getc returns EOF when the receive register is empty, so this never
 * blocks the capture loop. One block is 16 ms, which is well inside human
 * keypress timing.
 * ========================================================================= */
static void print_keys(void)
{
    put("#keys: 0-");   put_u32((uint32_t)(NUM_SLOTS - 1));
    put(" lock to that mic | c cycle | p stats | ? this list\r\n");
}

static void poll_keys(void)
{
    int c;
    while ((c = uarths_getc()) != EOF && c != -1) {
        if (c >= '0' && c < ('0' + NUM_SLOTS)) {
            g_mic = (uint8_t)(c - '0');
            g_cycling = 0;
            put("#locked\r\n");
            announce_mic();
        } else if (c == 'c' || c == 'C') {
            g_cycling = 1;
            put("#cycling every "); put_u32(SWITCH_SECONDS); put("s\r\n");
        } else if (c == 'p' || c == 'P') {
            g_want_stats = 1;
        } else if (c == '?') {
            print_keys();
        }
    }
}


/* ============================================================================
 * Streaming - record.py's "k210" frame format
 *   [0x5A][0xA5][seq:u8][blk:u16 LE][nch:u8][payload][sum16 LE]
 * nch is CHANNEL COUNT in this frame, always 1. Mic identity travels as the
 * "#switch mic=N" text line, which record.py harvests to split the capture
 * into one WAV per mic.
 * ========================================================================= */
static void send_frame(const int16_t *pcm, uint16_t count)
{
    const uint8_t *p = (const uint8_t *)pcm;
    const int nbytes = (int)count * 2;
    uint16_t sum = 0;

    uarths_putchar(0x5A);
    uarths_putchar(0xA5);
    uarths_putchar((char)g_seq);
    g_seq++;                                  /* uint8_t, wraps at 256 */
    uarths_putchar((char)(count & 0xFF));
    uarths_putchar((char)((count >> 8) & 0xFF));
    uarths_putchar((char)1);                  /* nch */

    for (int i = 0; i < nbytes; ++i) {
        uarths_putchar((char)p[i]);
        sum = (uint16_t)(sum + p[i]);
    }
    uarths_putchar((char)(sum & 0xFF));
    uarths_putchar((char)((sum >> 8) & 0xFF));
}


/* ============================================================================
 * Main
 * ========================================================================= */
int main(void)
{
    clocks_init();
    uart_init();

    put("\r\n#K210 array\r\n#data_lines="); put_u32(NUM_DATA_LINES);
    put(" slots=");      put_u32(NUM_SLOTS);
    put(" resolution=");
#if USE_16_BIT
    put("16");
#else
    put("32");
#endif
    put(" shift=");      put_u32(SAMPLE_SHIFT);
    put("\r\n#pins sclk="); put_u32(IO_SCLK);
    put(" ws=");         put_u32(IO_WS);
    put(" sd1=");        put_u32(IO_SD1);
#if NUM_DATA_LINES >= 2
    put(" sd2=");        put_u32(IO_SD2);
#endif
    put("\r\n");

    pins_init();
    i2s_setup();

    int cur = 0;
    uint32_t blocks = 0;
    uint32_t block_in_mic = 0;
    int streaming = 0;
    g_seq = 0;

    i2s_receive_data_dma(I2S_DEVICE_0, g_raw[cur], BLK * NUM_SLOTS, DMA_CH);

    put("\r\n#PROBE - tap each mic, each slot should respond to ITS OWN\r\n");
    put("#both move together  -> reading one mic twice, L/R fault\r\n");
    put("#FLOATING/RANDOM     -> nothing driving that half-frame\r\n");
    put("#CLIPPING            -> raise SAMPLE_SHIFT\r\n");
    put("#UNDRIVEN            -> no clock, no power, or wrong pin\r\n");
    print_keys();
    put("\r\n");

    for (;;) {
        dmac_wait_done(DMA_CH);
        const int done = cur;
        cur ^= 1;

        /* Re-arm BEFORE touching the finished buffer. Putting this after the
         * processing stops capture for the duration of the send, which shows
         * up as dropouts with no error reported anywhere. */
        i2s_receive_data_dma(I2S_DEVICE_0, g_raw[cur], BLK * NUM_SLOTS, DMA_CH);

        poll_keys();

        if (blocks == SETTLE_BLOCKS) {
            dump_raw(g_raw[done]);
            shift_scan(g_raw[done]);
            put("\r\n");
        }

        /* ---- probe phase: text only, nothing binary on the wire -------- */
        if (!streaming) {
            if (blocks > SETTLE_BLOCKS &&
                (g_want_stats || (blocks % (BLOCKS_PER_SEC / 2)) == 0u)) {
                slot_stats(g_raw[done]);
                g_want_stats = 0;
            }
            blocks++;

#if MIC_SLOT_OK
            if (blocks >= PROBE_BLOCKS) {
                streaming = 1;
                block_in_mic = 0;
                put("#stream begin\r\n");
                /* [BUG FIX] Announce the starting mic BEFORE any frame goes
                 * out. Without this the host has no marker for the first
                 * window and files it under UNKNOWN. */
                announce_mic();
            }
#endif
            continue;
        }

        /* ---- streaming phase ------------------------------------------- */
        if (g_want_stats) {                 /* 'p' works mid-stream; the host
                                             * resyncs on the magic word and
                                             * harvests '#' lines. */
            slot_stats(g_raw[done]);
            g_want_stats = 0;
        }

        for (int f = 0; f < BLK; ++f) {
            g_pcm[f] = word_to_sample(g_raw[done][f * NUM_SLOTS + g_mic]);
        }
        send_frame(g_pcm, BLK);

        if (g_cycling && ++block_in_mic >= BLOCKS_PER_MIC) {
            block_in_mic = 0;
            g_mic = (uint8_t)((g_mic + 1) % NUM_SLOTS);
            announce_mic();
        }
        blocks++;
    }
    return 0;
}