#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple


SUPPORTED_TYPES = {"vobsub", "pgs"}
DEFAULT_LANG = "eng"


@dataclass
class FrameInfo:
    start: float
    end: float


class JobError(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def run_command(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    log(f"[cmd] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def seconds_to_srt_time(value: float) -> str:
    if value < 0:
        value = 0
    millis = int(round(value * 1000))
    hours = millis // 3_600_000
    millis -= hours * 3_600_000
    minutes = millis // 60_000
    millis -= minutes * 60_000
    seconds = millis // 1000
    millis -= seconds * 1000
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def normalize_language(lang: Optional[str]) -> str:
    if not lang:
        return DEFAULT_LANG
    lang = lang.lower().strip()
    aliases = {
        "en": "eng",
        "english": "eng",
        "ja": "jpn",
        "jp": "jpn",
        "japanese": "jpn",
        "und": DEFAULT_LANG,
    }
    return aliases.get(lang, lang)


def list_tesseract_languages() -> List[str]:
    try:
        result = run_command(["tesseract", "--list-langs"], check=True)
    except FileNotFoundError as exc:
        raise JobError("tesseract not found in PATH") from exc
    lines = result.stdout.strip().splitlines()
    if not lines:
        return []
    return [line.strip() for line in lines[1:] if line.strip()]


def resolve_language(requested: Optional[str]) -> Tuple[str, List[str]]:
    normalized = normalize_language(requested)
    available = list_tesseract_languages()
    if normalized in available:
        return normalized, available
    log(f"[warn] Requested language '{normalized}' not installed. Falling back to '{DEFAULT_LANG}'.")
    if DEFAULT_LANG not in available:
        raise JobError("tesseract language data missing (eng not installed)")
    return DEFAULT_LANG, available


def ensure_readable(path: str) -> None:
    if not path:
        raise JobError("Missing required file path")
    if not os.path.isfile(path):
        raise JobError(f"File not found: {path}")
    if not os.access(path, os.R_OK):
        raise JobError(f"File not readable: {path}")


def ensure_writable(path: str) -> None:
    directory = os.path.dirname(path) or "."
    if not os.path.isdir(directory):
        raise JobError(f"Output directory does not exist: {directory}")
    if not os.access(directory, os.W_OK):
        raise JobError(f"Output directory not writable: {directory}")


def load_frames(input_path: str) -> List[FrameInfo]:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "s:0",
            "-show_entries",
            "frame=pkt_pts_time,pkt_duration_time",
            "-of",
            "json",
            input_path,
        ],
        check=True,
    )
    data = json.loads(result.stdout or "{}")
    frames = []
    for frame in data.get("frames", []):
        start = float(frame.get("pkt_pts_time", 0.0))
        duration = float(frame.get("pkt_duration_time", 0.0))
        end = start + max(duration, 0.0)
        frames.append(FrameInfo(start=start, end=end))
    return frames


def extract_images(input_path: str, output_dir: str) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    output_pattern = os.path.join(output_dir, "subtitle-%06d.png")
    run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            input_path,
            "-map",
            "0:s:0",
            "-vsync",
            "0",
            "-f",
            "image2",
            output_pattern,
        ],
        check=True,
    )
    images = sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.lower().endswith(".png")
    )
    return images


def ocr_image(image_path: str, language: str) -> str:
    result = run_command(
        ["tesseract", image_path, "stdout", "-l", language, "--psm", "6", "--dpi", "300"],
        check=True,
    )
    text = result.stdout.strip()
    return " ".join(text.split())


def build_srt(frames: List[FrameInfo], texts: List[str]) -> str:
    entries = []
    index = 1
    for frame, text in zip(frames, texts):
        if not text:
            continue
        start = seconds_to_srt_time(frame.start)
        end = seconds_to_srt_time(frame.end)
        if start == end:
            continue
        entries.append(f"{index}\n{start} --> {end}\n{text}\n")
        index += 1
    return "\n".join(entries).strip() + ("\n" if entries else "")


def validate_output(path: str) -> None:
    if not os.path.isfile(path):
        raise JobError("Output SRT not created")
    if os.path.getsize(path) == 0:
        raise JobError("Output SRT is empty")


def process_job(job: dict, dry_run: bool = False) -> int:
    job_type = (job.get("type") or "").lower().strip()
    if job_type not in SUPPORTED_TYPES:
        log(f"[warn] Unsupported type '{job_type}'. Marking as needs-review.")
        return 2

    srt_output = job.get("srt_output")
    if not srt_output:
        raise JobError("Missing srt_output in job")

    ensure_writable(srt_output)

    if job_type == "vobsub":
        idx_path = job.get("idx")
        if not idx_path:
            raise JobError("Missing idx path for vobsub job")
        ensure_readable(idx_path)
        companion = os.path.splitext(idx_path)[0] + ".sub"
        ensure_readable(companion)
        input_path = idx_path
    else:
        bitmap_path = job.get("bitmap_file")
        if not bitmap_path:
            raise JobError("Missing bitmap_file path for pgs job")
        ensure_readable(bitmap_path)
        input_path = bitmap_path

    language, _available = resolve_language(job.get("language"))
    log(f"[info] Using tesseract language: {language}")

    frames = load_frames(input_path)
    if not frames:
        log("[warn] No subtitle frames detected.")
        return 2

    if dry_run:
        log("[info] Dry run successful.")
        return 0

    with tempfile.TemporaryDirectory(prefix="ocr_worker_") as tempdir:
        images_dir = os.path.join(tempdir, "frames")
        images = extract_images(input_path, images_dir)
        if not images:
            log("[warn] No images extracted.")
            return 2

        if len(images) != len(frames):
            log(
                f"[warn] Frame count mismatch (frames={len(frames)} images={len(images)}). Using minimum count."
            )

        count = min(len(images), len(frames))
        texts = []
        for image_path in images[:count]:
            try:
                text = ocr_image(image_path, language)
            except subprocess.CalledProcessError as exc:
                log(f"[error] OCR failed on {image_path}: {exc.stderr.strip()}")
                text = ""
            texts.append(text)

        srt_content = build_srt(frames[:count], texts)
        if not srt_content.strip():
            log("[warn] OCR produced empty output.")
            return 2

        with open(srt_output, "w", encoding="utf-8") as handle:
            handle.write(srt_content)

    validate_output(srt_output)
    log(f"[info] Wrote SRT: {srt_output}")
    return 0


def doctor() -> int:
    log("Subtitle OCR worker capability report")
    log("--------------------------------------")
    tesseract_path = shutil.which("tesseract")
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    log(f"tesseract: {tesseract_path or 'missing'}")
    log(f"ffmpeg: {ffmpeg_path or 'missing'}")
    log(f"ffprobe: {ffprobe_path or 'missing'}")

    if not tesseract_path:
        log("[error] tesseract is required for OCR")
        return 1
    version = run_command(["tesseract", "--version"], check=True).stdout.splitlines()[0]
    log(f"tesseract version: {version}")
    try:
        languages = list_tesseract_languages()
        log(f"tesseract languages ({len(languages)}): {', '.join(languages)}")
    except JobError as exc:
        log(f"[error] {exc}")
        return 1

    log("Sample invocation:")
    log("  ocr_worker /queue/incoming/job.json")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Subtitle OCR worker")
    parser.add_argument("job", nargs="?", help="Path to job JSON")
    parser.add_argument("--dry-run", action="store_true", help="Validate job without OCR")
    parser.add_argument("--doctor", action="store_true", help="Print capability report")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.doctor:
        return doctor()

    if not args.job:
        raise JobError("Missing job JSON argument")

    ensure_readable(args.job)
    with open(args.job, "r", encoding="utf-8") as handle:
        job = json.load(handle)

    return process_job(job, dry_run=args.dry_run)


def entrypoint() -> None:
    try:
        code = main(sys.argv[1:])
    except JobError as exc:
        log(f"[error] {exc}")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        log(f"[error] Command failed: {exc.cmd}\n{exc.stderr}")
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        log(f"[error] Unexpected error: {exc}")
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    entrypoint()
