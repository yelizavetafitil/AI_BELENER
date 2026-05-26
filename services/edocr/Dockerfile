FROM python:3.10-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 1) Зависимости без tensorflow (eDOCr тянет полный tensorflow ~600 МБ — не нужен)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) eDOCr без зависимостей — используем tensorflow-cpu из requirements.txt
RUN pip install --no-cache-dir --no-deps "eDOCr==0.0.4"

COPY app.py .

ENV EDOCR_PORT=5001
EXPOSE 5001

CMD ["python", "-u", "app.py"]
