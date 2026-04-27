from __future__ import annotations

import argparse
import threading
from pathlib import Path

import numpy as np
import soundcard as sc
import sounddevice as sd

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from audio_utils import peak_normalize, read_mono, resample_to, write_wav  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record Windows WASAPI loopback from the default speaker.")
    parser.add_argument("--out", default=str(ROOT / "outputs" / "wasapi_loopback" / "loopback.wav"))
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--samplerate", type=int, default=48000)
    parser.add_argument("--play-test-wav", action="store_true", help="Play data/test.wav while recording loopback.")
    parser.add_argument("--volume", type=float, default=0.85)
    return parser.parse_args()


def play_test_wav(sr: int, volume: float) -> threading.Thread:
    def worker() -> None:
        wav, wav_sr = read_mono(ROOT / "data" / "test.wav")
        if wav_sr != sr:
            wav = resample_to(wav, wav_sr, sr)
        wav = peak_normalize(wav) * volume
        sd.play(wav, samplerate=sr, blocking=True)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def main() -> None:
    args = parse_args()
    speaker = sc.default_speaker()
    print(f"Recording loopback from default speaker: {speaker}")
    print(f"Sample rate: {args.samplerate} Hz, duration: {args.seconds:.2f}s")

    player = play_test_wav(args.samplerate, args.volume) if args.play_test_wav else None

    frames = int(args.seconds * args.samplerate)
    with sc.get_microphone(id=str(speaker.name), include_loopback=True).recorder(
        samplerate=args.samplerate,
        channels=2,
    ) as recorder:
        audio = recorder.record(numframes=frames)

    if player is not None:
        player.join(timeout=2.0)

    mono = np.asarray(audio, dtype=np.float32).mean(axis=1)
    write_wav(args.out, mono, args.samplerate)
    print(f"Saved: {Path(args.out)}")


if __name__ == "__main__":
    main()
