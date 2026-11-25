"""Replay matched synthetic command audio under controlled noise levels."""

import argparse
import io
import json
import math
import wave
from pathlib import Path

import httpx
import numpy as np
from scipy.signal import resample_poly

from qa.run_acceptance import CASES, json_api, replay, synthesize, wav_pcm

SAMPLE_RATE = 16000


def samples(wave_bytes):
    with wave.open(io.BytesIO(wave_bytes), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("Expected mono 16-bit PCM")
        sr = wav.getframerate()
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    factor = math.gcd(sr, SAMPLE_RATE)
    return resample_poly(audio, SAMPLE_RATE // factor, sr // factor)


def fan_noise(length, rng):
    t = np.arange(length) / SAMPLE_RATE
    return np.sin(2 * np.pi * 123 * t) + .4 * np.sin(2 * np.pi * 246 * t) + .25 * rng.normal(size=length)


def keyboard_noise(length, rng):
    noise = np.zeros(length)
    for start in range(500, length - 240, 3500):
        noise[start:start + 240] = rng.normal(size=240) * np.exp(-np.arange(240) / 43)
    return noise


def mix(signal, sound, snr):
    level = np.linalg.norm(signal) / max(1e-8, np.linalg.norm(sound))
    mixed = signal + level * 10 ** (-snr / 20) * sound
    return mixed / max(1.0, np.max(np.abs(mixed)) / .97)


def pcm(audio):
    return wav_pcm(np.rint(np.clip(audio, -.999, .999) * 32768).astype(np.int16).tolist())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extra-only", action="store_true", help="Test room echo, nearby speech and synthesized speaker bleed.")
    options = parser.parse_args()
    if not 8010 <= options.port <= 8099:
        parser.error("Use a disposable loopback backend.")
    selected = {"turn-09", "turn-42", "turn-51", "turn-58", "turn-62"}
    rng = np.random.default_rng(20261126)
    rows = []
    with httpx.Client(base_url=f"http://127.0.0.1:{options.port}", timeout=120, trust_env=False) as client:
        status = json_api(client, "/readiness")
        if status["connectivity"]["mode"] != "strict_offline":
            parser.error("Expected a strict-offline disposable backend.")
        if options.extra_only:
            background = samples(synthesize(client, {"text": "There is a book on the table.", "language": "en"}))
            bleed = samples(synthesize(client, {"text": "The time is four fifteen.", "language": "en"}))
        for case in CASES:
            if case["id"] not in selected:
                continue
            wave_bytes = synthesize(client, case)
            signal = samples(wave_bytes)
            fan = fan_noise(len(signal), rng)
            keyboard = keyboard_noise(len(signal), rng)
            variants = {"clean": signal, "quiet_-24db": signal * 10 ** (-24 / 20),
                        **{f"fan_{db}db": mix(signal, fan, db) for db in (20, 10, 5)},
                        "keyboard_10db": mix(signal, keyboard, 10)}
            if options.extra_only:
                # Two delayed reflections at 55 and 95 ms. Motion and sounds are synthetic.
                echo = np.zeros(len(signal) + 1520)
                echo[:len(signal)] = signal
                echo[880:880 + len(signal)] += .35 * signal
                echo[1520:] += .22 * signal
                variants = {"two_reflections": echo / max(1, np.max(np.abs(echo))),
                            "nearby_speech_8db": mix(signal, np.resize(background, len(signal)), 8),
                            "speaker_bleed_6db": mix(signal, np.resize(bleed, len(signal)), 6)}
            for name, audio in variants.items():
                row = replay(client, case, "auto", pcm=pcm(audio))
                rows.append(dict(row, condition=name))
                print(case["id"], name, row["actual"]["intent"], row["passed"], flush=True)
        noise_case = {"id": "fan_only", "text": "", "language": "en", "category": "silence"}
        rows.append(dict(replay(client, noise_case, "auto", pcm=pcm(fan_noise(SAMPLE_RATE * 2, rng) * .015)),
                         condition="fan_only"))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
