from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def read_mono(path: str | Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    if target_sr is not None and sr != target_sr:
        mono = resample_to(mono, sr, target_sr)
        sr = target_sr
    return np.asarray(mono, dtype=np.float32), sr


def write_wav(path: str | Path, audio: np.ndarray, sr: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.asarray(audio, dtype=np.float32)
    sf.write(str(path), np.clip(audio, -1.0, 1.0), sr, subtype="PCM_16")


def resample_to(audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    if source_sr == target_sr:
        return np.asarray(audio, dtype=np.float32)
    factor = gcd(source_sr, target_sr)
    up = target_sr // factor
    down = source_sr // factor
    return resample_poly(audio, up, down).astype(np.float32)


def peak_normalize(audio: np.ndarray, peak: float = 0.95) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    current = float(np.max(np.abs(audio))) if audio.size else 0.0
    if current <= 1e-8:
        return audio
    return audio * min(1.0, peak / current)


def rms_db(audio: np.ndarray, eps: float = 1e-12) -> float:
    audio = np.asarray(audio, dtype=np.float32)
    return 20.0 * np.log10(float(np.sqrt(np.mean(audio * audio))) + eps)
