FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    hunspell-ru \
    libgl1 \
    libglib2.0-0 \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Промежуточные CA tnpa.by (GlobalSign AlphaSSL R6 + R46) — в системное хранилище Docker.
COPY belener/certs/globalsign-r6-alphassl-2025.pem /usr/local/share/ca-certificates/globalsign-r6-alphassl-ca-2025.crt
COPY belener/certs/globalsign-r46-alphassl-2025.pem /usr/local/share/ca-certificates/globalsign-r46-alphassl-ca-2025.crt
RUN update-ca-certificates

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py schema.sql index.html ./
COPY belener ./belener
COPY admin ./admin
COPY scripts ./scripts
COPY static ./static
COPY *.png ./

EXPOSE 5000
CMD ["python", "-u", "app.py"]
