FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV PADDLE_LANG=ru
ENV PADDLE_PRELOAD=1
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

VOLUME ["/models"]

EXPOSE 8082

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8082"]
