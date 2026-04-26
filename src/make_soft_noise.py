from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from audio_utils import write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a softer broadband calibration signal.")
    parser.add_argument("--out", default=str(ROOT / "data" / "calib_soft_noise.wav"))
    parser.add_argument("--samplerate", type=int, default=16000)
    parser.add_argument("--duration", type=float, default=14.0)
    parser.add_argument("--low-hz", type=float, default=120.0)
    parser.add_argument("--high-hz", type=float, default=5200.0)
    parser.add_argument("--volume", type=float, default=0.22)
    parser.add_argument("--seed", type=int, default=20260426)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sr = args.samplerate
    rng = np.random.default_rng(args.seed)
    n = int(args.duration * sr)
    spectrum = np.zeros(n // 2 + 1, dtype=np.complex64)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    band = (freqs >= args.low_hz) & (freqs <= args.high_hz)
    phases = rng.uniform(0.0, 2.0 * np.pi, int(np.sum(band)))
    # Mild pink tilt: lower frequencies are a little stronger and less harsh.
    tilt = 1.0 / np.sqrt(np.maximum(freqs[band], args.low_hz) / args.low_hz)
    spectrum[band] = tilt * np.exp(1j * phases)
    x = np.fft.irfft(spectrum, n=n).astype(np.float32)

    # Gentle slow level motion makes the noise less fatiguing while staying broadband.
    t = np.arange(len(x), dtype=np.float32) / sr
    envelope = 0.75 + 0.25 * np.sin(2 * np.pi * 0.37 * t)
    x *= envelope.astype(np.float32)

    fade_len = int(0.35 * sr)
    fade_in = np.sin(np.linspace(0, np.pi / 2, fade_len, dtype=np.float32)) ** 2
    fade_out = fade_in[::-1]
    x[:fade_len] *= fade_in
    x[-fade_len:] *= fade_out

    x /= np.max(np.abs(x)) + 1e-12
    x *= args.volume
    write_wav(args.out, x, sr)
    print(f"Saved: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
