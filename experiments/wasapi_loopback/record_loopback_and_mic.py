from __future__ import annotations

import argparse
import queue
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
    parser = argparse.ArgumentParser(description="Record WASAPI loopback and microphone at the same time.")
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "wasapi_loopback_pair"))
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--samplerate", type=int, default=48000)
    parser.add_argument("--play-test-wav", action="store_true", help="Play data/test.wav while recording.")
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


def record_soundcard_microphone(mic, sr: int, seconds: float, channels: int, result_queue: queue.Queue[np.ndarray]) -> None:
    frames = int(seconds * sr)
    with mic.recorder(samplerate=sr, channels=channels) as recorder:
        result_queue.put(recorder.record(numframes=frames))


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    speaker = sc.default_speaker()
    loopback_mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    room_mic = sc.default_microphone()

    print(f"Loopback source: {loopback_mic}")
    print(f"Room microphone: {room_mic}")
    print(f"Sample rate: {args.samplerate} Hz, duration: {args.seconds:.2f}s")

    loopback_queue: queue.Queue[np.ndarray] = queue.Queue()
    mic_queue: queue.Queue[np.ndarray] = queue.Queue()

    loopback_thread = threading.Thread(
        target=record_soundcard_microphone,
        args=(loopback_mic, args.samplerate, args.seconds, 2, loopback_queue),
        daemon=True,
    )
    mic_thread = threading.Thread(
        target=record_soundcard_microphone,
        args=(room_mic, args.samplerate, args.seconds, 1, mic_queue),
        daemon=True,
    )

    loopback_thread.start()
    mic_thread.start()
    player = play_test_wav(args.samplerate, args.volume) if args.play_test_wav else None

    loopback_thread.join()
    mic_thread.join()
    if player is not None:
        player.join(timeout=2.0)

    loopback = np.asarray(loopback_queue.get(), dtype=np.float32).mean(axis=1)
    mic = np.asarray(mic_queue.get(), dtype=np.float32).reshape(-1)
    length = min(len(loopback), len(mic))
    loopback = loopback[:length]
    mic = mic[:length]

    write_wav(out_dir / "farend_loopback.wav", loopback, args.samplerate)
    write_wav(out_dir / "mic_recording.wav", mic, args.samplerate)
    print(f"Saved: {out_dir / 'farend_loopback.wav'}")
    print(f"Saved: {out_dir / 'mic_recording.wav'}")


if __name__ == "__main__":
    main()
