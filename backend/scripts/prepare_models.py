"""Offline model preparation script for AURA EDGE Phase 1 (NOT run at runtime).

Downloads and extracts verified open-source models into gitignored ``models/``:
- STT: Systran/faster-whisper-base & Systran/faster-whisper-small
- VAD: snakers4/silero-vad (silero_vad.onnx)
- TTS (EN): k2-fsa/sherpa-onnx vits-piper-en_US-lessac-medium
- TTS (HI): k2-fsa/sherpa-onnx vits-piper-hi_IN-rohan-medium
- TTS (KN): Symmetris-labs/tts-mms kan/{model.onnx,tokens.txt} (facebook/mms-tts-kan)
  plus optional micahchoo/piper-kn comparison checkpoint.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tarfile
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download, snapshot_download

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"

SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)
SHERPA_PIPER_EN_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
    "vits-piper-en_US-lessac-medium.tar.bz2"
)
SHERPA_PIPER_HI_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
    "vits-piper-hi_IN-rohan-medium.tar.bz2"
)


def _download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with httpx.stream("GET", url, follow_redirects=True, timeout=180.0) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                fh.write(chunk)
        tmp.replace(dest)
    return dest


def _download_and_extract_tar_bz2(url: str, target_dir: Path, expected_subdir: str) -> Path:
    final_dir = target_dir / expected_subdir
    if final_dir.exists() and any(final_dir.glob("*.onnx")):
        return final_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=300.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:bz2") as tf:
            tf.extractall(path=target_dir)
    if not final_dir.exists():
        raise RuntimeError(f"Expected extracted folder '{final_dir}' not found.")
    return final_dir


def prepare_stt(models_dir: Path, *, include_small: bool = True) -> dict[str, str]:
    out: dict[str, str] = {}
    targets = [("base", "Systran/faster-whisper-base")]
    if include_small:
        targets.append(("small", "Systran/faster-whisper-small"))
    for size, repo_id in targets:
        dest = models_dir / "stt" / f"faster-whisper-{size}"
        dest.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=repo_id, local_dir=str(dest))
        out[size] = str(dest)
    return out


def prepare_vad(models_dir: Path) -> str:
    dest = models_dir / "vad" / "silero_vad.onnx"
    _download_file(SILERO_VAD_URL, dest)
    return str(dest)


def prepare_tts(models_dir: Path, *, include_piper_kn_eval: bool = True) -> dict[str, str]:
    tts_root = models_dir / "tts"
    en_dir = _download_and_extract_tar_bz2(
        SHERPA_PIPER_EN_URL,
        tts_root / "en",
        "vits-piper-en_US-lessac-medium",
    )
    hi_dir = _download_and_extract_tar_bz2(
        SHERPA_PIPER_HI_URL,
        tts_root / "hi",
        "vits-piper-hi_IN-rohan-medium",
    )

    # Kannada MMS-TTS ONNX (facebook/mms-tts-kan pre-exported for sherpa-onnx, CC-BY-NC-4.0)
    kn_dir = tts_root / "kn" / "vits-mms-kan"
    kn_dir.mkdir(parents=True, exist_ok=True)
    for remote_name, local_name in [
        ("kan/model.onnx", "model.onnx"),
        ("kan/tokens.txt", "tokens.txt"),
    ]:
        target_file = kn_dir / local_name
        if not target_file.exists() or target_file.stat().st_size == 0:
            url = f"https://huggingface.co/Symmetris-labs/tts-mms/resolve/main/{remote_name}"
            _download_file(url, target_file)

    paths = {"en": str(en_dir), "hi": str(hi_dir), "kn_mms": str(kn_dir)}

    if include_piper_kn_eval:
        kn_piper_dir = tts_root / "kn_piper"
        kn_piper_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("kn_IN-syspin_male-medium.onnx", "kn_IN-syspin_male-medium.onnx.json"):
            target_file = kn_piper_dir / fname
            if not target_file.exists():
                cached = hf_hub_download(repo_id="micahchoo/piper-kn", filename=fname)
                shutil.copyfile(cached, target_file)
        paths["kn_piper_eval"] = str(kn_piper_dir)

    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Download local offline models for AURA EDGE Phase 1.")
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    parser.add_argument("--skip-small-stt", action="store_true")
    parser.add_argument("--skip-piper-kn-eval", action="store_true")
    args = parser.parse_args()

    stt_paths = prepare_stt(args.models_dir, include_small=not args.skip_small_stt)
    vad_path = prepare_vad(args.models_dir)
    tts_paths = prepare_tts(args.models_dir, include_piper_kn_eval=not args.skip_piper_kn_eval)

    manifest = {
        "models_dir": str(args.models_dir),
        "stt": stt_paths,
        "vad": vad_path,
        "tts": tts_paths,
    }
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
