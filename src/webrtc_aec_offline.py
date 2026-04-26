from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from aec_audio_processing import AudioProcessor

from audio_utils import read_mono, resample_to, rms_db, write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline test using WebRTC Audio Processing AEC.")
    parser.add_argument("--mic", default=str(ROOT / "outputs" / "tts_after_speaker_change" / "mic_recording.wav"))
    parser.add_argument("--ref", default=str(ROOT / "outputs" / "tts_after_speaker_change" / "farend_ref.wav"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "webrtc_aec"))
    parser.add_argument("--samplerate", type=int, default=16000, choices=[8000, 16000, 32000, 48000])
    parser.add_argument("--delay-ms", type=int, default=240)
    parser.add_argument("--warmup-ms", type=int, default=500, help="Feed far-end silence before real audio.")
    parser.add_argument("--enable-ns", action="store_true")
    parser.add_argument("--enable-agc", action="store_true")
    parser.add_argument("--keep-dc", action="store_true")
    return parser.parse_args()


def float_to_i16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)


def i16_to_float(x: np.ndarray) -> np.ndarray:
    return (x.astype(np.float32) / 32768.0).astype(np.float32)


def pad_to_frame(x: np.ndarray, frame_size: int) -> np.ndarray:
    pad = (-len(x)) % frame_size
    if pad:
        x = np.pad(x, (0, pad))
    return x


def run_webrtc_aec(
    mic: np.ndarray,
    ref: np.ndarray,
    sr: int,
    delay_ms: int,
    warmup_ms: int,
    enable_ns: bool,
    enable_agc: bool,
) -> np.ndarray:
    processor = AudioProcessor(
        enable_aec=True,
        enable_ns=enable_ns,
        ns_level=2,
        enable_agc=enable_agc,
        agc_mode=1,
        enable_vad=False,
    )
    processor.set_stream_format(sr, 1, sr, 1)
    processor.set_reverse_stream_format(sr, 1)
    processor.set_stream_delay(delay_ms)

    frame_size = int(processor.get_frame_size())
    if frame_size <= 0:
        frame_size = sr // 100

    mic_i16 = pad_to_frame(float_to_i16(mic), frame_size)
    ref_i16 = pad_to_frame(float_to_i16(ref), frame_size)
    total = min(len(mic_i16), len(ref_i16))
    mic_i16 = mic_i16[:total]
    ref_i16 = ref_i16[:total]

    silence = np.zeros(frame_size, dtype=np.int16).tobytes()
    for _ in range(max(0, warmup_ms) // 10):
        processor.process_reverse_stream(silence)

    out = np.zeros(total, dtype=np.int16)
    for start in range(0, total, frame_size):
        end = start + frame_size
        ref_frame = ref_i16[start:end].tobytes()
        mic_frame = mic_i16[start:end].tobytes()
        processor.process_reverse_stream(ref_frame)
        processed = processor.process_stream(mic_frame)
        out[start:end] = np.frombuffer(processed, dtype=np.int16, count=frame_size)

    return i16_to_float(out[: len(mic)])


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

    write_wav(out_dir / f"cleaned_webrtc_delay_{args.delay_ms}ms.wav", cleaned, args.samplerate)
    write_wav(out_dir / "mic_input_centered.wav", mic, args.samplerate)
    write_wav(out_dir / "ref_input_centered.wav", ref, args.samplerate)

    mic_rms = rms_db(mic)
    clean_rms = rms_db(cleaned)
    print(f"Sample rate: {args.samplerate} Hz")
    print(f"Delay: {args.delay_ms} ms")
    if not args.keep_dc:
        print(f"Removed DC offset: mic={mic_dc:.6f}, ref={ref_dc:.6f}")
    print(f"WebRTC AEC enabled. NS={args.enable_ns}, AGC={args.enable_agc}")
    print(f"Mic RMS: {mic_rms:.1f} dBFS")
    print(f"Cleaned RMS: {clean_rms:.1f} dBFS")
    print(f"RMS reduction: {mic_rms - clean_rms:.1f} dB")
    print(f"Saved: {out_dir / f'cleaned_webrtc_delay_{args.delay_ms}ms.wav'}")


if __name__ == "__main__":
    main()
