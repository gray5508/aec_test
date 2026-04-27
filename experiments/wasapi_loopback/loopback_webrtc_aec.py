from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import correlate, correlation_lags

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from audio_utils import read_mono, resample_to, rms_db, write_wav  # noqa: E402
from webrtc_aec_offline import run_webrtc_aec  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Align WASAPI loopback and microphone, then run WebRTC AEC.")
    parser.add_argument("--mic", default=str(ROOT / "outputs" / "wasapi_loopback_pair" / "mic_recording.wav"))
    parser.add_argument("--ref", default=str(ROOT / "outputs" / "wasapi_loopback_pair" / "farend_loopback.wav"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "wasapi_loopback_aec"))
    parser.add_argument("--samplerate", type=int, default=16000, choices=[8000, 16000, 32000, 48000])
    parser.add_argument("--delay-ms", type=int, default=40)
    parser.add_argument("--warmup-ms", type=int, default=500)
    parser.add_argument("--enable-ns", action="store_true")
    parser.add_argument("--enable-agc", action="store_true")
    parser.add_argument("--keep-dc", action="store_true")
    parser.add_argument("--no-auto-align", action="store_true", help="Skip loopback/mic pre-alignment.")
    parser.add_argument("--max-align-ms", type=float, default=1500.0)
    parser.add_argument("--align-window-sec", type=float, default=8.0)
    parser.add_argument("--min-corr", type=float, default=0.05, help="Warn if normalized correlation is below this value.")
    return parser.parse_args()


def estimate_lag_samples(mic: np.ndarray, ref: np.ndarray, sr: int, max_align_ms: float, window_sec: float) -> tuple[int, float]:
    n = min(len(mic), len(ref), max(1, int(sr * window_sec)))
    mic_part = mic[:n].astype(np.float64)
    ref_part = ref[:n].astype(np.float64)
    mic_part -= float(np.mean(mic_part))
    ref_part -= float(np.mean(ref_part))

    mic_energy = float(np.linalg.norm(mic_part))
    ref_energy = float(np.linalg.norm(ref_part))
    if mic_energy < 1e-10 or ref_energy < 1e-10:
        return 0, 0.0

    corr = correlate(mic_part, ref_part, mode="full", method="fft")
    lags = correlation_lags(len(mic_part), len(ref_part), mode="full")
    max_lag = int(sr * max_align_ms / 1000)
    mask = (lags >= -max_lag) & (lags <= max_lag)
    if not np.any(mask):
        return 0, 0.0

    masked_corr = corr[mask]
    masked_lags = lags[mask]
    index = int(np.argmax(np.abs(masked_corr)))
    lag = int(masked_lags[index])
    score = float(masked_corr[index] / (mic_energy * ref_energy))
    return lag, score


def align_by_lag(mic: np.ndarray, ref: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    if lag > 0:
        # mic lags ref: discard the earlier part of mic.
        mic = mic[lag:]
    elif lag < 0:
        # ref lags mic: discard the earlier part of ref.
        ref = ref[-lag:]

    length = min(len(mic), len(ref))
    return mic[:length].astype(np.float32), ref[:length].astype(np.float32)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mic, mic_sr = read_mono(args.mic)
    ref, ref_sr = read_mono(args.ref)
    if mic_sr != args.samplerate:
        mic = resample_to(mic, mic_sr, args.samplerate)
    if ref_sr != args.samplerate:
        ref = resample_to(ref, ref_sr, args.samplerate)

    length = min(len(mic), len(ref))
    mic = mic[:length].astype(np.float32)
    ref = ref[:length].astype(np.float32)

    raw_mic_rms = rms_db(mic)
    raw_ref_rms = rms_db(ref)

    lag = 0
    corr_score = 0.0
    if not args.no_auto_align:
        lag, corr_score = estimate_lag_samples(
            mic,
            ref,
            args.samplerate,
            max_align_ms=args.max_align_ms,
            window_sec=args.align_window_sec,
        )
        mic, ref = align_by_lag(mic, ref, lag)

    mic_dc = float(np.mean(mic))
    ref_dc = float(np.mean(ref))
    if not args.keep_dc:
        mic = mic - mic_dc
        ref = ref - ref_dc

    cleaned = run_webrtc_aec(
        mic,
        ref,
        args.samplerate,
        delay_ms=args.delay_ms,
        warmup_ms=args.warmup_ms,
        enable_ns=args.enable_ns,
        enable_agc=args.enable_agc,
    )

    write_wav(out_dir / "mic_aligned.wav", mic, args.samplerate)
    write_wav(out_dir / "ref_aligned.wav", ref, args.samplerate)
    write_wav(out_dir / f"cleaned_webrtc_delay_{args.delay_ms}ms.wav", cleaned, args.samplerate)

    report = {
        "mic": str(Path(args.mic).resolve()),
        "ref": str(Path(args.ref).resolve()),
        "samplerate": args.samplerate,
        "auto_align": not args.no_auto_align,
        "estimated_lag_samples": lag,
        "estimated_lag_ms": lag / args.samplerate * 1000,
        "correlation_score": corr_score,
        "delay_ms": args.delay_ms,
        "enable_ns": args.enable_ns,
        "enable_agc": args.enable_agc,
        "raw_mic_rms_dbfs": raw_mic_rms,
        "raw_ref_rms_dbfs": raw_ref_rms,
        "aligned_mic_rms_dbfs": rms_db(mic),
        "aligned_ref_rms_dbfs": rms_db(ref),
        "cleaned_rms_dbfs": rms_db(cleaned),
        "rms_reduction_db": rms_db(mic) - rms_db(cleaned),
    }
    (out_dir / "alignment_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Sample rate: {args.samplerate} Hz")
    if args.no_auto_align:
        print("Auto-align: disabled")
    else:
        print(f"Estimated loopback/mic lag: {lag} samples = {lag / args.samplerate * 1000:.1f} ms")
        print(f"Correlation score: {corr_score:.3f}")
        if abs(corr_score) < args.min_corr:
            print("Warning: low correlation. Alignment may be unreliable.")
    if not args.keep_dc:
        print(f"Removed DC offset: mic={mic_dc:.6f}, ref={ref_dc:.6f}")
    print(f"Delay: {args.delay_ms} ms")
    print(f"WebRTC AEC enabled. NS={args.enable_ns}, AGC={args.enable_agc}")
    print(f"Aligned mic RMS: {rms_db(mic):.1f} dBFS")
    print(f"Cleaned RMS: {rms_db(cleaned):.1f} dBFS")
    print(f"RMS reduction: {rms_db(mic) - rms_db(cleaned):.1f} dB")
    print(f"Saved: {out_dir / 'mic_aligned.wav'}")
    print(f"Saved: {out_dir / 'ref_aligned.wav'}")
    print(f"Saved: {out_dir / f'cleaned_webrtc_delay_{args.delay_ms}ms.wav'}")
    print(f"Saved: {out_dir / 'alignment_report.json'}")


if __name__ == "__main__":
    main()
