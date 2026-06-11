FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch (~200 MB), без nvidia-cuda / cuda-toolkit (лишние ~3 GB на AMD)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
# transformers<5 обязательно до surya-ocr (иначе pad_token_id)
RUN pip install --no-cache-dir "transformers>=4.56.1,<5.0.0" \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV SURYA_DEVICE=cpu
ENV HF_HOME=/models/huggingface
ENV TRANSFORMERS_CACHE=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV XDG_CACHE_HOME=/models/cache
ENV SURYA_PRELOAD=1
ENV SURYA_MAX_SIDE=2048
ENV SURYA_LANGS=ru,en

VOLUME ["/models"]

EXPOSE 8081

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
