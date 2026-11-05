FROM python:3.12-slim

# ffmpeg converts audio; libchromaprint-tools gives "fpcalc", which fingerprints songs for AcoustID
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY templates ./templates

ENV DOWNLOAD_DIR=/data PORT=8080
VOLUME ["/data"]
EXPOSE 8080

# One worker keeps the in-memory job list consistent; threads handle concurrency.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8080", "--timeout", "1200", "app:app"]
