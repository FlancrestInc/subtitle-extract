FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-jpn \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ocr_worker.py /app/ocr_worker.py

RUN chmod +x /app/ocr_worker.py \
    && ln -s /app/ocr_worker.py /usr/local/bin/ocr_worker

ENTRYPOINT ["/app/ocr_worker.py"]
CMD ["--doctor"]
