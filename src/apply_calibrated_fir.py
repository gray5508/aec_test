from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.linalg import solve_toeplitz
from scipy.signal import correlate, fftconvolve

from audio_utils import read_mono, rms_db, write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit echo path from chirp calibration, then apply it to a TTS recording.")
    parser.add_argument("--calib-mic", default=str(ROOT / "outputs" / "calib_chirp" / "mic_recording.wav"))
    parser.add_argument("--calib-ref", default=str(ROOT / "outputs" / "calib_chirp" / "farend_ref.wav"))
    parser.add_argument("--target-mic", default=str(ROOT / "outputs" / "mic_recording.wav"))
    parser.add_argument("--target-ref", default=str(ROOT / "outputs" / "farend_ref.wav"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "calibrated_tts"))
    parser.add_argument("--filter-ms", type=float, default=700.0)
    parser.add_argument("--reg", type=float, default=0.001)
    return parser.parse_args()


def fit_fir(ref: np.ndarray, mic: np.ndarray, filter_len: int, reg: float) -> np.ndarray:
    n = len(mic)
    rxx = correlate(ref, ref, mode="full", method="fft")[n - 1 : n + filter_len - 1]
    rxy = correlate(mic, ref, mode="full", method="fft")[n - 1 : n + filter_len - 1]
    first_col = rxx.copy()
    first_col[0] += max(reg, 0.0) * max(float(rxx[0]), 1e-12)
    return solve_toeplitz((first_col, first_col), rxy, check_finite=False).astype(np.float32)


def remove_dc(x: np.ndarray) -> tuple[np.ndarray, float]:
    dc = float(np.mean(x))
    return (x - dc).astype(np.float32), dc


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    calib_mic, sr = read_mono(args.calib_mic)
    calib_ref, _ = read_mono(args.calib_ref, target_sr=sr)
    n = min(len(calib_mic), len(calib_ref))
    calib_mic, calib_ref = calib_mic[:n], calib_ref[:n]
    calib_mic, calib_mic_dc = remove_dc(calib_mic)
    calib_ref, calib_ref_dc = remove_dc(calib_ref)

    filter_len = max(8, int(sr * args.filter_ms / 1000))
    h = fit_fir(calib_ref.astype(np.float64), calib_mic.astype(np.float64), filter_len, args.reg)

    target_mic, target_sr = read_mono(args.target_mic)
    target_ref, _ = read_mono(args.target_ref, target_sr=target_sr)
    if target_sr != sr:
        raise ValueError(f"Calibration sr={sr}, target sr={target_sr}; keep both recordings at the same sample rate.")
    m = min(len(target_mic), len(target_ref))
    target_mic, target_ref = target_mic[:m], target_ref[:m]
    target_mic, target_mic_dc = remove_dc(target_mic)
    target_ref, target_ref_dc = remove_dc(target_ref)

    echo = fftconvolve(target_ref, h, mode="full")[:m].astype(np.float32)
    cleaned = target_mic - echo

    np.save(out_dir / "calibrated_echo_path.npy", h)
    write_wav(out_dir / "calibrated_echo_path.wav", h / (np.max(np.abs(h)) + 1e-12), sr)
    write_wav(out_dir / "echo_estimate_calibrated.wav", echo, sr)
    write_wav(out_dir / "cleaned_calibrated.wav", cleaned, sr)

    mic_rms = rms_db(target_mic)
    clean_rms = rms_db(cleaned)
    echo_ratio = (float(np.sqrt(np.mean(echo * echo))) + 1e-12) / (
        float(np.sqrt(np.mean(target_mic * target_mic))) + 1e-12
    )
    print(f"Sample rate: {sr} Hz")
    print(f"Filter length: {filter_len} samples = {filter_len / sr * 1000:.1f} ms")
    print(f"Calibration DC removed: mic={calib_mic_dc:.6f}, ref={calib_ref_dc:.6f}")
    print(f"Target DC removed: mic={target_mic_dc:.6f}, ref={target_ref_dc:.6f}")
    print(f"Target mic RMS: {mic_rms:.1f} dBFS")
    print(f"Cleaned calibrated RMS: {clean_rms:.1f} dBFS")
    print(f"RMS reduction: {mic_rms - clean_rms:.1f} dB")
    print(f"Echo estimate ratio: {echo_ratio:.2f}x target mic RMS")
    print(f"Saved: {out_dir / 'cleaned_calibrated.wav'}")
    print(f"Saved: {out_dir / 'echo_estimate_calibrated.wav'}")


if __name__ == "__main__":
    main()
