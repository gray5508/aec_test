from __future__ import annotations

import argparse
import json
import queue
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx
import sounddevice as sd
from aec_audio_processing import AudioProcessor

from audio_utils import read_mono, resample_to, rms_db, write_wav


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play random TTS samples, run WebRTC AEC in memory, and feed cleaned mic audio to sherpa-mini ASR."
    )
    parser.add_argument("--tts-dir", default=str(ROOT / "TTS_module" / "voice" / "samples"))
    parser.add_argument("--model-dir", default=str(ROOT / "ASR_module" / "models" / "sherpa_mini"))
    parser.add_argument("--out-dir", default=str(ROOT / "outputs" / "barge_in_aec_asr"))
    parser.add_argument("--input-device", type=int, default=1)
    parser.add_argument("--output-device", type=int, default=4)
    parser.add_argument("--samplerate", type=int, default=16000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--gap-seconds", type=float, default=1.2)
    parser.add_argument("--tail-seconds", type=float, default=2.0)
    parser.add_argument("--volume", type=float, default=1.0)
    parser.add_argument("--delay-ms", type=int, default=240)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--enable-ns", action="store_true")
    parser.add_argument("--enable-agc", action="store_true")
    parser.add_argument("--raw-asr", action="store_true", help="Also run ASR on raw mic for comparison.")
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--vad-min-silence", type=float, default=0.45)
    parser.add_argument("--asr-gain", type=float, default=2.5, help="Gain applied before sherpa VAD/ASR only.")
    return parser.parse_args()


def float_to_i16_bytes(x: np.ndarray) -> bytes:
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)
    return pcm.tobytes()


def i16_bytes_to_float(data: bytes, count: int) -> np.ndarray:
    return (np.frombuffer(data, dtype=np.int16, count=count).astype(np.float32) / 32768.0).copy()


def pad_or_trim(frame: np.ndarray, frame_size: int) -> np.ndarray:
    frame = np.asarray(frame, dtype=np.float32).reshape(-1)
    if len(frame) == frame_size:
        return frame
    out = np.zeros(frame_size, dtype=np.float32)
    n = min(len(frame), frame_size)
    out[:n] = frame[:n]
    return out


def load_tts_sequence(tts_dir: Path, sr: int, rounds: int, gap_seconds: float, tail_seconds: float, volume: float, seed: int | None):
    files = sorted(tts_dir.glob("*.wav"))
    if not files:
        raise FileNotFoundError(f"No wav files found in {tts_dir}")

    rng = random.Random(seed)
    chosen = [rng.choice(files) for _ in range(rounds)]
    gap = np.zeros(int(gap_seconds * sr), dtype=np.float32)
    tail = np.zeros(int(tail_seconds * sr), dtype=np.float32)

    chunks: list[np.ndarray] = []
    timeline = []
    cursor = 0
    for index, path in enumerate(chosen, start=1):
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
        end = cursor
        timeline.append(
            {
                "round": index,
                "file": str(path),
                "start_sec": round(start / sr, 3),
                "end_sec": round(end / sr, 3),
                "duration_sec": round(len(audio) / sr, 3),
            }
        )
        chunks.append(gap)
        cursor += len(gap)

    chunks.append(tail)
    playback = np.concatenate(chunks).astype(np.float32)
    return playback, timeline


@dataclass
class AsrEvent:
    source: str
    text: str
    start_time: float
    duration: float
    rms_db: float


class SherpaMiniWorker:
    def __init__(
        self,
        name: str,
        model_dir: Path,
        sr: int,
        gain: float,
        vad_threshold: float,
        vad_min_silence: float,
    ) -> None:
        self.name = name
        self.sr = sr
        self.gain = gain
        self.queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self.events: list[AsrEvent] = []
        self._sample_cursor = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = str(model_dir / "silero_vad.onnx")
        vad_config.silero_vad.min_silence_duration = vad_min_silence
        vad_config.silero_vad.threshold = vad_threshold
        vad_config.silero_vad.max_speech_duration = 12.0
        vad_config.sample_rate = sr
        self.vad = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=60)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / "model.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            use_itn=False,
            num_threads=2,
        )

    def start(self) -> None:
        self._thread.start()

    def put(self, samples: np.ndarray) -> None:
        self.queue.put(np.asarray(samples, dtype=np.float32).copy())

    def stop(self) -> None:
        self.queue.put(None)
        self._thread.join()

    def _decode_segment(self, segment: np.ndarray, start_sample: int) -> None:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(self.sr, segment)
        self.recognizer.decode_stream(stream)
        text = stream.result.text.strip()
        if not text:
            return
        event = AsrEvent(
            source=self.name,
            text=text,
            start_time=start_sample / self.sr,
            duration=len(segment) / self.sr,
            rms_db=rms_db(segment),
        )
        self.events.append(event)
        print(
            f"[{self.name}] {event.start_time:6.2f}s +{event.duration:4.2f}s "
            f"{event.rms_db:6.1f} dBFS -> {event.text}",
            flush=True,
        )

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            if item is None:
                break
            samples = np.asarray(item, dtype=np.float32).reshape(-1) * self.gain
            self.vad.accept_waveform(samples)
            self._sample_cursor += len(samples)
            while not self.vad.empty():
                segment = self.vad.front.samples
                self.vad.pop()
                start_sample = max(0, self._sample_cursor - len(segment))
                self._decode_segment(segment, start_sample)

        # Push a bit of silence to force final endpointing.
        for _ in range(20):
            self.vad.accept_waveform(np.zeros(self.sr // 100, dtype=np.float32))
            self._sample_cursor += self.sr // 100
            while not self.vad.empty():
                segment = self.vad.front.samples
                self.vad.pop()
                start_sample = max(0, self._sample_cursor - len(segment))
                self._decode_segment(segment, start_sample)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sr = args.samplerate
    frame_size = sr // 100
    playback, timeline = load_tts_sequence(
        Path(args.tts_dir),
        sr=sr,
        rounds=args.rounds,
        gap_seconds=args.gap_seconds,
        tail_seconds=args.tail_seconds,
        volume=args.volume,
        seed=args.seed,
    )

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

    model_dir = Path(args.model_dir)
    clean_asr = SherpaMiniWorker(
        "CLEAN_ASR",
        model_dir=model_dir,
        sr=sr,
        gain=args.asr_gain,
        vad_threshold=args.vad_threshold,
        vad_min_silence=args.vad_min_silence,
    )
    raw_asr = None
    if args.raw_asr:
        raw_asr = SherpaMiniWorker(
            "RAW_ASR",
            model_dir=model_dir,
            sr=sr,
            gain=args.asr_gain,
            vad_threshold=args.vad_threshold,
            vad_min_silence=args.vad_min_silence,
        )

    mic_raw = np.zeros_like(playback)
    mic_clean = np.zeros_like(playback)
    farend_ref = np.zeros_like(playback)
    cursor = 0

    print("Selected TTS sequence:")
    for item in timeline:
        print(f"  #{item['round']}: {Path(item['file']).name} [{item['start_sec']}s - {item['end_sec']}s]")
    print()
    print("Speak while TTS is playing. Watch CLEAN_ASR; robot TTS should be strongly suppressed.")
    print(f"Device=(input {args.input_device}, output {args.output_device}), delay={args.delay_ms}ms, NS={args.enable_ns}")

    clean_asr.start()
    if raw_asr is not None:
        raw_asr.start()

    def callback(indata, outdata, frames, time_info, status) -> None:
        nonlocal cursor
        if status:
            print(status, flush=True)

        start = cursor
        end = min(cursor + frames, len(playback))
        n = end - start

        ref_frame = np.zeros(frames, dtype=np.float32)
        if n > 0:
            ref_frame[:n] = playback[start:end]

        mic_frame = pad_or_trim(indata[:, 0], frames)

        processor.process_reverse_stream(float_to_i16_bytes(ref_frame))
        clean_frame = i16_bytes_to_float(processor.process_stream(float_to_i16_bytes(mic_frame)), frames)

        outdata[:, 0] = ref_frame
        if n > 0:
            mic_raw[start:end] = mic_frame[:n]
            mic_clean[start:end] = clean_frame[:n]
            farend_ref[start:end] = ref_frame[:n]
            clean_asr.put(clean_frame[:n])
            if raw_asr is not None:
                raw_asr.put(mic_frame[:n])

        cursor += frames
        if cursor >= len(playback):
            raise sd.CallbackStop()

    started = time.perf_counter()
    with sd.Stream(
        samplerate=sr,
        blocksize=frame_size,
        dtype="float32",
        channels=(1, 1),
        device=(args.input_device, args.output_device),
        callback=callback,
    ):
        sd.sleep(int((len(playback) / sr + 0.5) * 1000))

    clean_asr.stop()
    if raw_asr is not None:
        raw_asr.stop()

    elapsed = time.perf_counter() - started
    write_wav(out_dir / "farend_ref.wav", farend_ref, sr)
    write_wav(out_dir / "mic_raw.wav", mic_raw, sr)
    write_wav(out_dir / "mic_clean_webrtc.wav", mic_clean, sr)

    events = [event.__dict__ for event in clean_asr.events]
    if raw_asr is not None:
        events.extend(event.__dict__ for event in raw_asr.events)
    report = {
        "args": vars(args),
        "elapsed_sec": round(elapsed, 3),
        "timeline": timeline,
        "rms": {
            "farend_ref": rms_db(farend_ref),
            "mic_raw": rms_db(mic_raw),
            "mic_clean_webrtc": rms_db(mic_clean),
        },
        "events": events,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("Saved:")
    print(f"  {out_dir / 'farend_ref.wav'}")
    print(f"  {out_dir / 'mic_raw.wav'}")
    print(f"  {out_dir / 'mic_clean_webrtc.wav'}")
    print(f"  {out_dir / 'report.json'}")
    print()
    print("Summary:")
    print(f"  farend_ref RMS: {rms_db(farend_ref):.1f} dBFS")
    print(f"  mic_raw RMS: {rms_db(mic_raw):.1f} dBFS")
    print(f"  mic_clean_webrtc RMS: {rms_db(mic_clean):.1f} dBFS")


if __name__ == "__main__":
    main()
