"""
Device profiles.

Each board differs in three ways that matter to the PC: how fast the link
runs, how the audio is framed, and whether opening the port resets the board.
Everything else in the recorder is shared, so those three things live here and
nowhere else. Adding a board means adding one entry to this dict.
"""

# ---------------------------------------------------------------------------
# Frame layouts
#
# "v2"  [0x5A][0xA5][seq16][count16][int16 payload][xor8]
#       What esp32_1mic.ino sends. Sequence numbers let the PC count dropped
#       frames; the checksum catches mangled ones.
#
# "v1"  [0x5A][0xA5][count16][int16 payload]
#       The older K210 firmware. No sequence, no checksum - a dropped byte
#       still costs you a resync, but at least the magic word lets you
#       recover instead of corrupting the rest of the recording.
#
# "k210" [0x5A][0xA5][seq8][blk16][nch8][int16 payload][sum16]
#       What k210_1mic.c sends, and the strongest of the three. The magic
#       word alone is NOT a sync point - 0x5A 0xA5 occurs by chance inside
#       int16 audio in about 1.6% of blocks - so the host checks four things
#       before accepting a candidate: blk is sane, nch matches, the checksum
#       matches, and seq incremented by one. A false magic fails at least
#       one of those nearly always.
#
# "raw" no framing at all, just int16 little-endian forever.
#       Only here because early firmware did this. Do not use it for new
#       work: a single dropped byte flips the sample boundary and every
#       sample afterwards becomes noise, with no way for the PC to notice.
# ---------------------------------------------------------------------------

DEVICES = {
    "esp32": {
        "label": "ESP32-S3 + INMP441",
        "baud": 921600,
        "format": "v2",
        "sample_rate": 16000,
        # The ESP32 bootloader watches DTR and RTS. pyserial asserts both by
        # default when the port opens, which reboots the board mid-capture.
        "dtr": False,
        "rts": False,
        "firmware": "firmware/esp32_1mic/esp32_1mic.ino",
        "notes": "USB CDC. Read the '#' probe lines before trusting audio.",
    },
    "k210": {
        "label": "Sipeed M1 / K210 + INMP441",
        # 921600 to match k210_1mic.c. The array firmware ran the same rate;
        # asking for more does not help, see the bridge note below.
        "baud": 921600,
        "format": "k210",
        "sample_rate": 16000,
        # Asserting DTR/RTS drops the K210 into its ISP bootloader, and the
        # port then reads zero bytes forever. This cost a full debugging
        # session once already.
        "dtr": False,
        "rts": False,
        "firmware": "firmware/k210_1mic/k210_1mic.c",
        "notes": "CH552/CH340 bridge has no flow control back to the K210, so "
                 "link headroom is the only defence against drops. One slot "
                 "instead of two takes utilisation from 70% to 35%.",
    },
    "stm32": {
        "label": "STM32F446 + INMP441 (SAI)",
        "baud": 921600,
        "format": "v2",
        "sample_rate": 16000,
        "dtr": False,
        "rts": False,
        "firmware": "(not written yet)",
        "notes": "Use the same v2 frame layout as the ESP32 so one host "
                 "script serves both.",
    },
}


def get(name):
    key = name.lower()
    if key not in DEVICES:
        raise KeyError(f"unknown device {name!r}. Choose from: "
                       f"{', '.join(sorted(DEVICES))}")
    return DEVICES[key]


def describe():
    lines = [f"{'device':<8}{'baud':>9}{'frame':>7}  description"]
    lines.append("-" * 60)
    for k, v in sorted(DEVICES.items()):
        lines.append(f"{k:<8}{v['baud']:>9}{v['format']:>7}  {v['label']}")
    return "\n".join(lines)
