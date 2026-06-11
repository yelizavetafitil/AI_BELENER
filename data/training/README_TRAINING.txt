Датасет из кропов (авто-OCR, нужна ручная правка для обучения).

Файлы:
  manifest.jsonl     — все кропы + ocr_baseline
  labels/<stem>_<zone>.txt — текст по каждому кропу
  paddle_rec/train_list.txt — формат PaddleOCR rec (путь TAB текст)

ВАЖНО: для дообучения модели исправьте labels/*.txt в Label Studio/CVAT,
затем пересоберите train_list.txt. Обучение только на авто-OCR без правок
закрепляет ошибки.

Дообучение PaddleOCR (офлайн, отдельная машина):
  pip install paddlepaddle paddleocr
  см. https://github.com/PaddlePaddle/PaddleOCR/blob/main/doc/doc_en/recognition_en.md

YOLO зон: разметка bbox на полных страницах, классы spec_table, stamp, legend.
