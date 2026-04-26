from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import sounddevice as sd

from audio_utils import peak_normalize, read_mono, write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play data/test.wav and record microphone at the same time.")
    parser.add_argument("--wav", default=str(ROOT / "data" / "test.wav"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs"))
    parser.add_argument("--samplerate", type=int, default=16000)
    parser.add_argument("--blocksize", type=int, default=256)
    parser.add_argument("--tail-seconds", type=float, default=1.0, help="Record silence after playback to capture echo tail.")
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--volume", type=float, default=0.85, help="Playback gain applied to test.wav.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tts, sr = read_mono(args.wav, target_sr=args.samplerate)
    tts = peak_normalize(tts) * args.volume
    tail = np.zeros(int(args.tail_seconds * sr), dtype=np.float32)
    playback = np.concatenate([tts, tail]).astype(np.float32)

    mic = np.zeros_like(playback)
    farend_ref = np.zeros_like(playback)
    cursor = 0

    def callback(indata, outdata, frames, time_info, status) -> None:
        nonlocal cursor
        if status:
            print(status)

        end = min(cursor + frames, len(playback))
        n = end - cursor
        block = np.zeros(frames, dtype=np.float32)
        if n > 0:
            block[:n] = playback[cursor:end]
            mic[cursor:end] = indata[:n, 0]
            farend_ref[cursor:end] = block[:n]
        outdata[:, 0] = block
        cursor += frames
        if cursor >= len(playback):
            raise sd.CallbackStop()

    device = (args.input_device, args.output_device)
    print(f"Playing and recording at {sr} Hz. Device={device}. Duration={len(playback) / sr:.2f}s")
    with sd.Stream(
        samplerate=sr,
        blocksize=args.blocksize,
        dtype="float32",
        channels=(1, 1),
        device=device,
        callback=callback,
    ):
        sd.sleep(int((len(playback) / sr + 0.5) * 1000))

    write_wav(out_dir / "mic_recording.wav", mic, sr)
    write_wav(out_dir / "farend_ref.wav", farend_ref, sr)

    metadata = {
        "samplerate": sr,
        "blocksize": args.blocksize,
        "input_device": args.input_device,
        "output_device": args.output_device,
        "volume": args.volume,
        "tail_seconds": args.tail_seconds,
        "source_wav": str(Path(args.wav).resolve()),
    }
    (out_dir / "recording_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved: {out_dir / 'mic_recording.wav'}")
    print(f"Saved: {out_dir / 'farend_ref.wav'}")


if __name__ == "__main__":
    main()
