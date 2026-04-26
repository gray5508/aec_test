from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.signal import chirp

from audio_utils import write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a logarithmic sweep for echo-path calibration.")
    parser.add_argument("--out", default=str(ROOT / "data" / "calib_chirp.wav"))
    parser.add_argument("--samplerate", type=int, default=16000)
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--f0", type=float, default=80.0)
    parser.add_argument("--f1", type=float, default=7200.0)
    parser.add_argument("--volume", type=float, default=0.45)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sr = args.samplerate
    t = np.arange(int(args.duration * sr), dtype=np.float32) / sr
    x = chirp(t, f0=args.f0, f1=args.f1, t1=args.duration, method="logarithmic").astype(np.float32)

    fade_len = int(0.05 * sr)
    fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
    fade_out = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
    x[:fade_len] *= fade_in
    x[-fade_len:] *= fade_out
    x *= args.volume

    write_wav(args.out, x, sr)
    print(f"Saved: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
