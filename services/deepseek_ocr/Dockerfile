FROM python:3.12-slim-bookworm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

ENV DEEPSEEK_BACKEND_URL=http://host.docker.internal:8000
ENV DEEPSEEK_BACKEND_TIMEOUT=300

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
