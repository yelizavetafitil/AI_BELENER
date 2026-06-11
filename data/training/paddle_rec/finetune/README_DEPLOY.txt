После обучения скопируйте inference-модель rec в:
  data/training/paddle_rec/models/rec_finetuned/
или смонтируйте в Docker:
  PADDLE_REC_MODEL_DIR=/models/rec_finetuned

В .env:
  PADDLE_OCR_URL=http://paddle-ocr:8082
  PDF_OCR_PADDLE_ZONES=1
  PDF_OCR_ENGINE=tesseract
