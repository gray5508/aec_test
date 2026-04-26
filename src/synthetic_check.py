from __future__ import annotations

from pathlib import Path

import numpy as np

from aec_offline import delayed_ref, nlms_cancel
from audio_utils import rms_db, write_wav


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sr = 16000
    rng = np.random.default_rng(7)
    ref = rng.normal(0.0, 0.08, sr * 4).astype(np.float32)

    delay = int(0.08 * sr)
    echo_path = np.zeros(delay + 420, dtype=np.float32)
    echo_path[delay] = 0.55
    echo_path[delay + 80] = 0.25
    echo_path[delay + 240] = -0.12
    echo = np.convolve(ref, echo_path)[: len(ref)].astype(np.float32)

    near_voice_like = 0.015 * rng.normal(size=len(ref)).astype(np.float32)
    mic = echo + near_voice_like

    aligned = delayed_ref(ref, len(mic), delay)
    cleaned, echo_est = nlms_cancel(mic, aligned, filter_len=512, mu=0.08)

    out_dir = ROOT / "outputs" / "synthetic"
    write_wav(out_dir / "synthetic_mic.wav", mic, sr)
    write_wav(out_dir / "synthetic_cleaned.wav", cleaned, sr)
    write_wav(out_dir / "synthetic_echo_estimate.wav", echo_est, sr)

    improvement = rms_db(mic) - rms_db(cleaned)
    print(f"Synthetic mic RMS: {rms_db(mic):.1f} dBFS")
    print(f"Synthetic cleaned RMS: {rms_db(cleaned):.1f} dBFS")
    print(f"RMS reduction: {improvement:.1f} dB")
    print(f"Saved synthetic wavs to: {out_dir}")


if __name__ == "__main__":
    main()
