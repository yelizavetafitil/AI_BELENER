FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# PaddleOCR 2.9 train совместим с paddle 2.6 (paddle 3.x ломает tools/train.py)
RUN pip install --no-cache-dir paddlepaddle==2.6.2 "paddleocr>=2.9.0,<3.0.0" pillow "numpy<2"

RUN git clone --depth 1 --branch release/2.9 https://github.com/PaddlePaddle/PaddleOCR.git /PaddleOCR

RUN pip install --no-cache-dir -r /PaddleOCR/requirements.txt imgaug lmdb

# PP-OCRv3 cyrillic rec (SVTR_LCNet) — совместим с cyrillic_PP-OCRv3_rec_train.tar
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates \
    && mkdir -p /pretrain \
    && wget -q --show-progress -O /pretrain/cyrillic_PP-OCRv3_rec_train.tar \
       https://paddleocr.bj.bcebos.com/PP-OCRv3/multilingual/cyrillic_PP-OCRv3_rec_train.tar \
    && tar -xf /pretrain/cyrillic_PP-OCRv3_rec_train.tar -C /pretrain \
    && rm /pretrain/cyrillic_PP-OCRv3_rec_train.tar \
    && apt-get purge -y wget && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

ENV PADDLEOCR_REPO=/PaddleOCR
WORKDIR /app

COPY scripts/train_paddle_rec.py scripts/export_paddle_line_dataset.py scripts/rebuild_train_list.py /app/scripts/
# контекст сборки — корень репозитория (docker-compose.train.yml)

CMD ["python", "/app/scripts/train_paddle_rec.py", "--min-lines", "40", "--run-train", "--export", "--epochs", "35"]
