"""Compare existing Whisper with local Hindi/Kannada CTC weights on matching speech."""

import argparse
import json
import time
from pathlib import Path

import httpx
import sherpa_onnx
from aura_edge.config import ComputeMode, Settings
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.speech.stt import WhisperTranscriber

from backend.scripts.benchmark_stabilization import compute_wer_cer
from qa.noise_acceptance import samples
from qa.run_acceptance import CASES, synthesize

MATCHED = {"turn-38", "turn-41", "turn-42", "turn-48", "turn-51", "turn-52"}


def decode_ctc(model, audio):
    start = time.perf_counter()
    stream = model.create_stream()
    stream.accept_waveform(16000, audio)
    model.decode_stream(stream)
    return stream.result.text.strip(), (time.perf_counter() - start) * 1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--hindi-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 8010 <= args.port <= 8099:
        parser.error("Use a disposable loopback backend.")

    settings = Settings(models_dir=args.models, compute_mode=ComputeMode.CPU)
    whisper = WhisperTranscriber(settings, ComputeManager(settings))
    specialists = {"kn": args.models / "stt" / "indic-conformer-kn", "hi": args.hindi_dir}
    router = MultilingualFastRouter()
    rows = []
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=120, trust_env=False) as client:
        for language, directory in specialists.items():
            ctc = None
            if directory:
                ctc = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                    model=str(directory / "model.int8.onnx"), tokens=str(directory / "tokens.txt"),
                    num_threads=4, provider="cpu",
                )
            for phrase in CASES:
                if phrase["id"] not in MATCHED or phrase["language"] != language:
                    continue
                audio = samples(synthesize(client, phrase))
                # Benchmark stock Whisper separately from AURA's Kannada specialist.
                with whisper._lock:
                    model, device, compute_type, reason = whisper._load_model_locked()
                    segs, _, _, _, _, ms = whisper._run_whisper_locked(
                        model, audio, "small", device, compute_type, reason, lang_arg=language, prompt_arg=None,
                    )
                    text, *_ = whisper._extract_segment_metrics(segs)
                outputs = [("whisper-small-int8-cpu", text, ms)]
                if ctc is not None:
                    text, ms = decode_ctc(ctc, audio)
                    outputs.append((f"indicconformer-{language}-int8-cpu", text, ms))
                for engine, text, ms in outputs:
                    intent, _ = router.route(text, requested_language=language)
                    actual = intent.intent_name.value if intent else "Unrouted"
                    wer, cer = compute_wer_cer(phrase["text"], text)
                    row = {"id": phrase["id"], "language": language, "engine": engine, "expected_text": phrase["text"],
                           "transcript": text, "expected_intent": phrase["intent"], "actual_intent": actual,
                           "intent_match": actual == phrase["intent"], "wer": wer, "cer": cer, "decode_ms": round(ms, 2)}
                    rows.append(row)
                    print(engine, phrase["id"], actual, row["intent_match"], row["decode_ms"], flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
