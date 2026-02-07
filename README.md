# Subtitle OCR Worker

A Dockerized subtitle OCR worker designed for FileFlows + n8n automation. It focuses on VobSub (`.idx/.sub`) with a secondary path for PGS (`.sup`). Jobs are driven by JSON tickets and can be invoked via CLI (primary) from n8n.

## Why this OCR approach is reliable
The worker uses `ffmpeg`/`ffprobe` to decode bitmap subtitle streams into ordered images with timestamps, then performs OCR with modern Tesseract (v5) using the CLI interface. This avoids deprecated Tesseract API bindings while still processing VobSub streams accurately and deterministically for DVD-era sources.

## Features
- **Primary path:** VobSub (`.idx/.sub`) OCR to SRT
- **Secondary path:** PGS (`.sup`) OCR to SRT (or returns “needs review”)
- Language auto-detection via job ticket with safe fallback to `eng`
- Preflight checks for files, permissions, companion files, and output validity
- Deterministic exit codes for automation
- `--doctor` capability report

## FileFlows + n8n flow (high level)
1. FileFlows extracts bitmap subtitles and writes a job JSON into `/queue/incoming`.
2. n8n executes the container with `ocr_worker /queue/incoming/job.json`.
3. The worker writes the SRT into the movie folder and exits with a status code.

## Exit codes
- **0**: Success and SRT written
- **2**: Needs review (unsupported format, empty OCR output, no frames)
- **1+**: Hard failure (missing files, missing dependencies, internal error)

## Docker build
```bash
docker compose build
```

## Docker run (doctor)
```bash
docker compose up
```

## Example job JSON
```json
{
  "type": "vobsub",
  "language": "eng",
  "idx": "/movies/Movie (2019)/Movie (2019).idx",
  "srt_output": "/movies/Movie (2019)/Movie (2019).eng.srt"
}
```
This output naming follows Jellyfin best practice: `<video basename>.<lang>.srt`.

PGS example:
```json
{
  "type": "pgs",
  "language": "jpn",
  "bitmap_file": "/movies/Anime (2015)/Anime (2015).sup",
  "srt_output": "/movies/Anime (2015)/Anime (2015).jpn.srt"
}
```

## Run the worker via CLI (n8n Execute Command)
```bash
docker exec subtitle-ocr-worker ocr_worker /queue/incoming/job.json
```

Dry run validation:
```bash
docker exec subtitle-ocr-worker ocr_worker --dry-run /queue/incoming/job.json
```

Capability report:
```bash
docker exec subtitle-ocr-worker ocr_worker --doctor
```

## Adding more Tesseract languages
The container installs `tesseract-ocr-eng` and `tesseract-ocr-jpn` by default. To add more languages, extend the Dockerfile:
```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr-spa \
        tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*
```

## Testing with sample files
1. Place `.idx/.sub` or `.sup` in a movie folder mounted under `/movies`.
2. Create a job JSON in `/queue/incoming`.
3. Run:
   ```bash
   docker exec subtitle-ocr-worker ocr_worker /queue/incoming/job.json
   ```
4. Confirm the SRT file is written and non-empty.

## Known limitations
- OCR accuracy depends on bitmap quality. Low-contrast subtitles may require pre-processing.
- PGS OCR uses the same ffmpeg + tesseract pipeline and may produce empty results on stylized fonts.
- The worker currently OCRs the first subtitle stream in the bitmap file. For multi-stream bitmap containers, generate separate job tickets per stream.

## Extending to additional formats
- For additional bitmap formats, ensure `ffmpeg` can decode them into images and provide timestamps via `ffprobe`.
- For text-based subtitles (e.g., SRT/ASS), bypass OCR and use a direct copy or conversion step before invoking this worker.
