from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.signal import correlate, correlation_lags, stft

from audio_utils import read_mono, rms_db, write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline adaptive acoustic echo cancellation with NLMS.")
    parser.add_argument("--mic", default=str(ROOT / "outputs" / "mic_recording.wav"))
    parser.add_argument("--ref", default=str(ROOT / "outputs" / "farend_ref.wav"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs"))
    parser.add_argument("--filter-ms", type=float, default=80.0, help="Adaptive echo path length in milliseconds.")
    parser.add_argument("--mu", type=float, default=0.0002, help="NLMS step size. Try 0.00005 to 0.002 for real recordings.")
    parser.add_argument("--max-delay-ms", type=float, default=500.0)
    parser.add_argument("--fixed-delay-ms", type=float, default=None, help="Override automatic delay estimate.")
    parser.add_argument("--skip-ms", type=float, default=0.0, help="Skip initial samples for metrics/plot only.")
    parser.add_argument("--keep-dc", action="store_true", help="Do not remove DC offset before AEC.")
    parser.add_argument("--plot", action="store_true", help="Generate diagnostic.png.")
    return parser.parse_args()


def estimate_delay_samples(mic: np.ndarray, ref: np.ndarray, sr: int, max_delay_ms: float) -> int:
    max_delay = int(sr * max_delay_ms / 1000)
    n = min(len(mic), len(ref), sr * 12)
    mic_part = mic[:n] - np.mean(mic[:n])
    ref_part = ref[:n] - np.mean(ref[:n])
    corr = correlate(mic_part, ref_part, mode="full", method="fft")
    lags = correlation_lags(len(mic_part), len(ref_part), mode="full")
    mask = (lags >= 0) & (lags <= max_delay)
    if not np.any(mask):
        return 0
    best = int(lags[mask][np.argmax(np.abs(corr[mask]))])
    return best


def delayed_ref(ref: np.ndarray, length: int, delay: int) -> np.ndarray:
    x = np.zeros(length, dtype=np.float32)
    if delay < length:
        n = min(len(ref), length - delay)
        x[delay : delay + n] = ref[:n]
    return x


def nlms_cancel(mic: np.ndarray, ref: np.ndarray, filter_len: int, mu: float) -> tuple[np.ndarray, np.ndarray]:
    mic = mic.astype(np.float32)
    ref = ref.astype(np.float32)
    padded = np.pad(ref, (filter_len - 1, 0))
    weights = np.zeros(filter_len, dtype=np.float32)
    cleaned = np.zeros_like(mic)
    echo_est = np.zeros_like(mic)
    eps = 1e-7

    for n in range(len(mic)):
        x = padded[n : n + filter_len][::-1]
        norm = float(np.dot(x, x))
        if norm < eps:
            cleaned[n] = mic[n]
            continue
        y = float(np.dot(weights, x))
        e = float(mic[n] - y)
        weights += (mu * e / (norm + eps)) * x
        echo_est[n] = y
        cleaned[n] = e

    return cleaned, echo_est


def make_diagnostic_plot(path: Path, mic: np.ndarray, cleaned: np.ndarray, echo: np.ndarray, sr: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = np.arange(len(mic)) / sr
    win = max(1, int(sr * 0.05))

    def smooth_energy(x: np.ndarray) -> np.ndarray:
        return np.convolve(x * x, np.ones(win) / win, mode="same")

    _, _, mic_z = stft(mic, fs=sr, nperseg=512, noverlap=384)
    _, _, clean_z = stft(cleaned, fs=sr, nperseg=512, noverlap=384)
    mic_mag = 20 * np.log10(np.maximum(np.abs(mic_z), 1e-6))
    clean_mag = 20 * np.log10(np.maximum(np.abs(clean_z), 1e-6))

    fig, axes = plt.subplots(3, 1, figsize=(12, 9))
    axes[0].plot(t, mic, label="mic", alpha=0.75)
    axes[0].plot(t, echo, label="estimated echo", alpha=0.75)
    axes[0].plot(t, cleaned, label="cleaned", alpha=0.75)
    axes[0].set_title("Waveforms")
    axes[0].legend(loc="upper right")

    axes[1].plot(t, 10 * np.log10(smooth_energy(mic) + 1e-12), label="mic energy")
    axes[1].plot(t, 10 * np.log10(smooth_energy(cleaned) + 1e-12), label="cleaned energy")
    axes[1].set_title("Short-term energy")
    axes[1].legend(loc="upper right")

    diff = clean_mag - mic_mag
    im = axes[2].imshow(diff, origin="lower", aspect="auto", cmap="coolwarm", vmin=-20, vmax=20)
    axes[2].set_title("STFT delta: cleaned dB - mic dB")
    axes[2].set_xlabel("Frame")
    axes[2].set_ylabel("Frequency bin")
    fig.colorbar(im, ax=axes[2])
    fig.subplots_adjust(left=0.08, right=0.92, top=0.95, bottom=0.07, hspace=0.35)
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mic, sr = read_mono(args.mic)
    ref, ref_sr = read_mono(args.ref, target_sr=sr)
    length = min(len(mic), len(ref))
    mic = mic[:length]
    ref = ref[:length]

    mic_dc = float(np.mean(mic))
    ref_dc = float(np.mean(ref))
    if not args.keep_dc:
        mic = mic - mic_dc
        ref = ref - ref_dc

    if args.fixed_delay_ms is None:
        delay = estimate_delay_samples(mic, ref, sr, args.max_delay_ms)
    else:
        delay = int(sr * args.fixed_delay_ms / 1000)

    filter_len = max(8, int(sr * args.filter_ms / 1000))
    aligned_ref = delayed_ref(ref, len(mic), delay)
    cleaned, echo = nlms_cancel(mic, aligned_ref, filter_len=filter_len, mu=args.mu)

    write_wav(out_dir / "cleaned.wav", cleaned, sr)
    write_wav(out_dir / "echo_estimate.wav", echo, sr)

    skip = int(sr * args.skip_ms / 1000)
    print(f"Sample rate: {sr} Hz")
    print(f"Estimated delay: {delay} samples = {delay / sr * 1000:.1f} ms")
    print(f"Filter length: {filter_len} samples = {filter_len / sr * 1000:.1f} ms")
    if not args.keep_dc:
        print(f"Removed DC offset: mic={mic_dc:.6f}, ref={ref_dc:.6f}")
    print(f"Mic RMS: {rms_db(mic[skip:]):.1f} dBFS")
    print(f"Cleaned RMS: {rms_db(cleaned[skip:]):.1f} dBFS")
    if rms_db(cleaned[skip:]) > rms_db(mic[skip:]) + 3.0:
        print("Warning: cleaned output is louder than mic input. Try smaller --mu, shorter --filter-ms, or check the delay.")
    print(f"Saved: {out_dir / 'cleaned.wav'}")
    print(f"Saved: {out_dir / 'echo_estimate.wav'}")
    if args.plot:
        make_diagnostic_plot(out_dir / "diagnostic.png", mic, cleaned, echo, sr)
        print(f"Saved: {out_dir / 'diagnostic.png'}")


if __name__ == "__main__":
    main()
