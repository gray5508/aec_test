from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.linalg import solve_toeplitz
from scipy.signal import correlate, fftconvolve

from audio_utils import read_mono, rms_db, write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit a fixed FIR echo path from a pure-TTS recording, then subtract the estimated echo."
    )
    parser.add_argument("--mic", default=str(ROOT / "outputs" / "mic_recording.wav"))
    parser.add_argument("--ref", default=str(ROOT / "outputs" / "farend_ref.wav"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs"))
    parser.add_argument("--filter-ms", type=float, default=500.0)
    parser.add_argument("--reg", type=float, default=0.001, help="Ridge regularization ratio.")
    parser.add_argument("--keep-dc", action="store_true")
    return parser.parse_args()


def fit_fir(ref: np.ndarray, mic: np.ndarray, filter_len: int, reg: float) -> np.ndarray:
    n = len(mic)
    rxx = correlate(ref, ref, mode="full", method="fft")[n - 1 : n + filter_len - 1]
    rxy = correlate(mic, ref, mode="full", method="fft")[n - 1 : n + filter_len - 1]

    first_col = rxx.copy()
    first_col[0] += max(reg, 0.0) * max(float(rxx[0]), 1e-12)
    return solve_toeplitz((first_col, first_col), rxy, check_finite=False).astype(np.float32)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mic, sr = read_mono(args.mic)
    ref, _ = read_mono(args.ref, target_sr=sr)
    length = min(len(mic), len(ref))
    mic = mic[:length].astype(np.float32)
    ref = ref[:length].astype(np.float32)

    mic_dc = float(np.mean(mic))
    ref_dc = float(np.mean(ref))
    if not args.keep_dc:
        mic = mic - mic_dc
        ref = ref - ref_dc

    filter_len = max(8, int(sr * args.filter_ms / 1000))
    h = fit_fir(ref.astype(np.float64), mic.astype(np.float64), filter_len, args.reg)
    echo = fftconvolve(ref, h, mode="full")[:length].astype(np.float32)
    cleaned = mic - echo

    np.save(out_dir / "echo_path_fir.npy", h)
    write_wav(out_dir / "echo_path_fir.wav", h / (np.max(np.abs(h)) + 1e-12), sr)
    write_wav(out_dir / "echo_estimate_fir.wav", echo, sr)
    write_wav(out_dir / "cleaned_fir.wav", cleaned, sr)

    mic_rms = rms_db(mic)
    clean_rms = rms_db(cleaned)
    echo_ratio = (float(np.sqrt(np.mean(echo * echo))) + 1e-12) / (
        float(np.sqrt(np.mean(mic * mic))) + 1e-12
    )
    print(f"Sample rate: {sr} Hz")
    print(f"Filter length: {filter_len} samples = {filter_len / sr * 1000:.1f} ms")
    if not args.keep_dc:
        print(f"Removed DC offset: mic={mic_dc:.6f}, ref={ref_dc:.6f}")
    print(f"Mic RMS: {mic_rms:.1f} dBFS")
    print(f"Cleaned FIR RMS: {clean_rms:.1f} dBFS")
    print(f"RMS reduction: {mic_rms - clean_rms:.1f} dB")
    print(f"Echo estimate ratio: {echo_ratio:.2f}x mic RMS")
    print(f"Saved: {out_dir / 'cleaned_fir.wav'}")
    print(f"Saved: {out_dir / 'echo_estimate_fir.wav'}")
    print(f"Saved: {out_dir / 'echo_path_fir.npy'}")


if __name__ == "__main__":
    main()
