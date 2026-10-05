"""
link_test.py - sanity-check the K210 link + k210 frame format in isolation,
against the LINK_TEST_PATTERN firmware build (sends 0,1,2,...,BLK-1 instead
of real mic data). Doesn't touch record.py's WAV-writing path at all - this
only tells you whether frames are arriving structurally intact.

    python link_test.py COM12 1500000
"""
import sys
import time
import numpy as np
import serial

from record import FrameReader   # reuse the exact same parser record.py uses

def sweep(port):
    candidates = [115200, 230400, 460800, 921600, 1000000, 1500000, 2000000,
                  750000, 375000, 187500]
    print(f"sweeping {len(candidates)} baud rates on {port}, 2s each...\n")
    for baud in candidates:
        try:
            ser = serial.Serial(port, baud, timeout=0.5)
        except serial.SerialException as e:
            print(f"  {baud:>8}: could not open ({e})")
            continue
        ser.dtr = False
        ser.rts = False
        reader = FrameReader("k210")
        t0 = time.time()
        while time.time() - t0 < 2:
            data = ser.read(4096)
            if data:
                reader.feed(data)
        ser.close()
        flag = "  <-- frames parsed!" if reader.frames > 0 else ""
        print(f"  {baud:>8}: frames={reader.frames:<4} resync={reader.resyncs:<6} "
              f"bad_crc={reader.bad_crc}{flag}")
    print("\nIf NONE of these show frames>0, the issue is upstream of baud "
          "entirely - check the board has ever completed a successful UART "
          "transaction at all on this exact PCB, with any firmware.")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM12"
    if len(sys.argv) > 2 and sys.argv[2] == "sweep":
        sweep(port)
        return
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 1500000

    ser = serial.Serial(port, baud, timeout=1)
    ser.dtr = False
    ser.rts = False

    reader = FrameReader("k210")
    checked = 0
    good = 0
    t0 = time.time()

    print(f"reading {port} @ {baud} for 5s...")
    while time.time() - t0 < 5:
        data = ser.read(4096)
        if not data:
            continue
        blocks, mic_ids = reader.feed(data)
        for block in blocks:
            checked += 1
            expected = np.arange(len(block), dtype="<i2")
            if np.array_equal(block, expected):
                good += 1
            elif checked <= 3:
                # Show the first couple of mismatches so you can see HOW
                # it's wrong, not just that it is.
                print(f"  mismatch: got {block[:10]}... expected {expected[:10]}...")

    ser.close()
    print(f"\nframes seen: {reader.frames}  good: {good}/{checked}  "
          f"dropped: {reader.dropped}  bad_crc: {reader.bad_crc}  "
          f"resync: {reader.resyncs}")

    if reader.frames == 0:
        print("\n[!] zero frames parsed at all - link/baud/framing problem, "
              "not a mic/DMA problem. Check baud, cable, DTR/RTS.")
    elif good == checked and checked > 0:
        print("\n[OK] every frame matched exactly - link and k210 framing "
              "are solid. The problem is specific to the real audio path "
              "(DMA/I2S config), not the UART link. Revert "
              "LINK_TEST_PATTERN and look there next.")
    else:
        print(f"\n[!] {checked - good}/{checked} frames arrived corrupted "
              "despite passing the checksum - this points at intermittent "
              "bit errors on the physical link (cable/connector/bridge "
              "chip), not a protocol logic bug.")

if __name__ == "__main__":
    main()