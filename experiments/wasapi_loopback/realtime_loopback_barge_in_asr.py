from __future__ import annotations

import argparse
import json
import queue
import random
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import soundcard as sc
import sounddevice as sd
from aec_audio_processing import AudioProcessor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from audio_utils import read_mono, resample_to, rms_db, write_wav  # noqa: E402
from barge_in_aec_asr_test import SherpaMiniWorker  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Realtime WASAPI loopback + WebRTC AEC + sherpa ASR experiment."
    )
    parser.add_argument("--tts-dir", default=str(ROOT / "TTS_module" / "voice" / "samples"))
    parser.add_argument("--model-dir", default=str(ROOT / "ASR_module" / "models" / "sherpa_mini"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "wasapi_loopback_realtime_asr"))
    parser.add_argument("--samplerate", type=int, default=16000)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--delay-ms", type=int, default=120, help="Residual WebRTC stream delay after pre-alignment.")
    parser.add_argument(
        "--loopback-lag-ms",
        type=float,
        default=230.0,
        help="Positive means loopback arrives later than mic, so mic is buffered before AEC.",
    )
    parser.add_argument("--enable-ns", action="store_true")
    parser.add_argument("--enable-agc", action="store_true")
    parser.add_argument("--raw-asr", action="store_true")
    parser.add_argument("--play-random-tts", action="store_true", help="Also play random cached TTS samples.")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--gap-seconds", type=float, default=1.2)
    parser.add_argument("--volume", type=float, default=0.85)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--asr-gain", type=float, default=2.5)
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--vad-min-silence", type=float, default=0.45)
    parser.add_argument("--queue-timeout", type=float, default=2.0)
    return parser.parse_args()


def float_to_i16_bytes(x: np.ndarray) -> bytes:
    pcm = np.clip(np.round(np.asarray(x, dtype=np.float32) * 32767.0), -32768, 32767).astype(np.int16)
    return pcm.tobytes()


def i16_bytes_to_float(data: bytes, count: int) -> np.ndarray:
    return (np.frombuffer(data, dtype=np.int16, count=count).astype(np.float32) / 32768.0).copy()


def mono_frame(frame: np.ndarray, frame_size: int) -> np.ndarray:
    frame = np.asarray(frame, dtype=np.float32)
    if frame.ndim == 2:
        frame = frame.mean(axis=1)
    frame = frame.reshape(-1)
    if len(frame) == frame_size:
        return frame
    out = np.zeros(frame_size, dtype=np.float32)
    n = min(frame_size, len(frame))
    out[:n] = frame[:n]
    return out


def load_random_tts_sequence(
    tts_dir: Path,
    sr: int,
    rounds: int,
    gap_seconds: float,
    volume: float,
    seed: int | None,
) -> tuple[np.ndarray, list[dict]]:
    files = sorted(tts_dir.glob("*.wav"))
    if not files:
        return np.zeros(0, dtype=np.float32), []

    rng = random.Random(seed)
    gap = np.zeros(int(gap_seconds * sr), dtype=np.float32)
    chunks: list[np.ndarray] = []
    timeline: list[dict] = []
    cursor = 0
    for index in range(1, rounds + 1):
        path = rng.choice(files)
        audio, file_sr = read_mono(path)
        if file_sr != sr:
            audio = resample_to(audio, file_sr, sr)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1e-8:
            audio = audio / max(1.0, peak)
        audio = (audio * volume).astype(np.float32)
        start = cursor
        chunks.append(audio)
        cursor += len(audio)
        timeline.append(
            {
                "round": index,
                "file": str(path),
                "start_sec": round(start / sr, 3),
                "end_sec": round(cursor / sr, 3),
            }
        )
        chunks.append(gap)
        cursor += len(gap)
    return np.concatenate(chunks).astype(np.float32), timeline


def play_sequence(sequence: np.ndarray, sr: int, stop_event: threading.Event) -> threading.Thread:
    def worker() -> None:
        if len(sequence) == 0:
            return
        frame_size = sr // 100
        cursor = 0

        def callback(outdata, frames, time_info, status) -> None:
            nonlocal cursor
            if status:
                print(status, flush=True)
            block = np.zeros(frames, dtype=np.float32)
            if not stop_event.is_set() and cursor < len(sequence):
                end = min(cursor + frames, len(sequence))
                n = end - cursor
                block[:n] = sequence[cursor:end]
                cursor = end
            outdata[:, 0] = block
            if cursor >= len(sequence):
                raise sd.CallbackStop()

        with sd.OutputStream(samplerate=sr, blocksize=frame_size, channels=1, dtype="float32", callback=callback):
            while cursor < len(sequence) and not stop_event.is_set():
                sd.sleep(100)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def capture_worker(
    name: str,
    mic,
    sr: int,
    frame_size: int,
    channels: int,
    stop_event: threading.Event,
    frames: queue.Queue[np.ndarray],
) -> None:
    try:
        with mic.recorder(samplerate=sr, channels=channels) as recorder:
            while not stop_event.is_set():
                frame = mono_frame(recorder.record(numframes=frame_size), frame_size)
                frames.put(frame, timeout=0.5)
    except Exception as exc:
        print(f"[{name}] capture stopped: {type(exc).__name__}: {exc}", flush=True)
        stop_event.set()


def take_pair_with_prealign(
    mic_frame: np.ndarray,
    loop_frame: np.ndarray,
    mic_delay: deque[np.ndarray],
    loop_delay: deque[np.ndarray],
    lag_frames: int,
    frame_size: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    if lag_frames > 0:
        mic_delay.append(mic_frame)
        if len(mic_delay) <= lag_frames:
            return None
        return mic_delay.popleft(), loop_frame
    if lag_frames < 0:
        loop_delay.append(loop_frame)
        if len(loop_delay) <= -lag_frames:
            return None
        return mic_frame, loop_delay.popleft()
    return mic_frame, loop_frame


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sr = args.samplerate
    frame_size = sr // 100
    lag_frames = int(round(args.loopback_lag_ms / 10.0))

    speaker = sc.default_speaker()
    loopback_mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    room_mic = sc.default_microphone()

    processor = AudioProcessor(
        enable_aec=True,
        enable_ns=args.enable_ns,
        ns_level=2,
        enable_agc=args.enable_agc,
        agc_mode=1,
        enable_vad=False,
    )
    processor.set_stream_format(sr, 1, sr, 1)
    processor.set_reverse_stream_format(sr, 1)
    processor.set_stream_delay(args.delay_ms)
    actual_frame = int(processor.get_frame_size()) or frame_size
    if actual_frame != frame_size:
        raise RuntimeError(f"Expected {frame_size} samples/frame, WebRTC reports {actual_frame}")

    clean_asr = SherpaMiniWorker(
        "CLEAN_ASR",
        model_dir=Path(args.model_dir),
        sr=sr,
        gain=args.asr_gain,
        vad_threshold=args.vad_threshold,
        vad_min_silence=args.vad_min_silence,
    )
    raw_asr = None
    if args.raw_asr:
        raw_asr = SherpaMiniWorker(
            "RAW_ASR",
            model_dir=Path(args.model_dir),
            sr=sr,
            gain=args.asr_gain,
            vad_threshold=args.vad_threshold,
            vad_min_silence=args.vad_min_silence,
        )

    tts_sequence = np.zeros(0, dtype=np.float32)
    tts_timeline: list[dict] = []
    if args.play_random_tts:
        tts_sequence, tts_timeline = load_random_tts_sequence(
            Path(args.tts_dir),
            sr=sr,
            rounds=args.rounds,
            gap_seconds=args.gap_seconds,
            volume=args.volume,
            seed=args.seed,
        )

    print("Realtime WASAPI loopback AEC ASR experiment")
    print(f"Loopback source: {loopback_mic}")
    print(f"Room microphone: {room_mic}")
    print(f"Default speaker: {speaker}")
    print(f"Sample rate: {sr} Hz, frame={frame_size} samples / 10ms")
    print(f"Duration: {args.seconds:.1f}s")
    print(f"WebRTC delay-ms: {args.delay_ms}, loopback-lag-ms: {args.loopback_lag_ms} ({lag_frames} frames)")
    print(f"NS={args.enable_ns}, AGC={args.enable_agc}, raw_asr={args.raw_asr}")
    if args.play_random_tts:
        print("Random TTS playback:")
        for item in tts_timeline:
            print(f"  #{item['round']}: {Path(item['file']).name} [{item['start_sec']}s - {item['end_sec']}s]")
    print()
    print("Start NetEase Cloud Music before running this script. Speak now; watch CLEAN_ASR.")

    stop_event = threading.Event()
    loop_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=300)
    mic_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=300)
    loop_thread = threading.Thread(
        target=capture_worker,
        args=("loopback", loopback_mic, sr, frame_size, 1, stop_event, loop_queue),
        daemon=True,
    )
    mic_thread = threading.Thread(
        target=capture_worker,
        args=("mic", room_mic, sr, frame_size, 1, stop_event, mic_queue),
        daemon=True,
    )

    loop_thread.start()
    mic_thread.start()
    player_thread = play_sequence(tts_sequence, sr, stop_event) if args.play_random_tts else None
    clean_asr.start()
    if raw_asr is not None:
        raw_asr.start()

    mic_delay: deque[np.ndarray] = deque()
    loop_delay: deque[np.ndarray] = deque()
    saved_loopback: list[np.ndarray] = []
    saved_mic: list[np.ndarray] = []
    saved_clean: list[np.ndarray] = []
    saved_raw_input: list[np.ndarray] = []

    processed_frames = 0
    dropped_frames = 0
    started = time.perf_counter()
    deadline = started + args.seconds
    try:
        while time.perf_counter() < deadline and not stop_event.is_set():
            try:
                loop_frame = loop_queue.get(timeout=args.queue_timeout)
                mic_frame = mic_queue.get(timeout=args.queue_timeout)
            except queue.Empty:
                print("Timed out waiting for audio frames.", flush=True)
                break

            pair = take_pair_with_prealign(
                mic_frame,
                loop_frame,
                mic_delay=mic_delay,
                loop_delay=loop_delay,
                lag_frames=lag_frames,
                frame_size=frame_size,
            )
            if pair is None:
                dropped_frames += 1
                continue
            aligned_mic, aligned_loop = pair

            processor.process_reverse_stream(float_to_i16_bytes(aligned_loop))
            clean_frame = i16_bytes_to_float(processor.process_stream(float_to_i16_bytes(aligned_mic)), frame_size)

            clean_asr.put(clean_frame)
            if raw_asr is not None:
                raw_asr.put(aligned_mic)

            saved_loopback.append(aligned_loop.copy())
            saved_mic.append(aligned_mic.copy())
            saved_clean.append(clean_frame.copy())
            saved_raw_input.append(mic_frame.copy())
            processed_frames += 1
    finally:
        stop_event.set()
        if player_thread is not None:
            player_thread.join(timeout=2.0)
        clean_asr.stop()
        if raw_asr is not None:
            raw_asr.stop()

    elapsed = time.perf_counter() - started
    loop_audio = np.concatenate(saved_loopback).astype(np.float32) if saved_loopback else np.zeros(0, dtype=np.float32)
    mic_audio = np.concatenate(saved_mic).astype(np.float32) if saved_mic else np.zeros(0, dtype=np.float32)
    clean_audio = np.concatenate(saved_clean).astype(np.float32) if saved_clean else np.zeros(0, dtype=np.float32)
    raw_input_audio = np.concatenate(saved_raw_input).astype(np.float32) if saved_raw_input else np.zeros(0, dtype=np.float32)

    write_wav(out_dir / "loopback_ref_realtime.wav", loop_audio, sr)
    write_wav(out_dir / "mic_aligned_realtime.wav", mic_audio, sr)
    write_wav(out_dir / "mic_clean_realtime.wav", clean_audio, sr)
    write_wav(out_dir / "mic_raw_input_realtime.wav", raw_input_audio, sr)

    events = [event.__dict__ for event in clean_asr.events]
    if raw_asr is not None:
        events.extend(event.__dict__ for event in raw_asr.events)
    report = {
        "args": vars(args),
        "loopback_source": str(loopback_mic),
        "room_microphone": str(room_mic),
        "default_speaker": str(speaker),
        "elapsed_sec": round(elapsed, 3),
        "processed_frames": processed_frames,
        "dropped_prealign_frames": dropped_frames,
        "tts_timeline": tts_timeline,
        "rms": {
            "loopback_ref_realtime": rms_db(loop_audio) if loop_audio.size else None,
            "mic_aligned_realtime": rms_db(mic_audio) if mic_audio.size else None,
            "mic_clean_realtime": rms_db(clean_audio) if clean_audio.size else None,
        },
        "events": events,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("Saved:")
    print(f"  {out_dir / 'loopback_ref_realtime.wav'}")
    print(f"  {out_dir / 'mic_aligned_realtime.wav'}")
    print(f"  {out_dir / 'mic_clean_realtime.wav'}")
    print(f"  {out_dir / 'mic_raw_input_realtime.wav'}")
    print(f"  {out_dir / 'report.json'}")
    print()
    print("Summary:")
    print(f"  elapsed: {elapsed:.1f}s, processed_frames: {processed_frames}, prealign_wait_frames: {dropped_frames}")
    if loop_audio.size:
        print(f"  loopback_ref RMS: {rms_db(loop_audio):.1f} dBFS")
    if mic_audio.size:
        print(f"  mic_aligned RMS: {rms_db(mic_audio):.1f} dBFS")
    if clean_audio.size:
        print(f"  mic_clean RMS: {rms_db(clean_audio):.1f} dBFS")


if __name__ == "__main__":
    main()
