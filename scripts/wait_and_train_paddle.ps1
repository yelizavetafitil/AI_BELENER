Write-Host "Waiting for OCR pseudo-labeling (Surya) to finish..."
while ($true) {
    $dockerTop = docker top ai_belener-web-1
    if ($dockerTop -notmatch "ocr_training_crops") {
        Write-Host "OCR labeling finished!"
        break
    }
    Start-Sleep -Seconds 60
}

Write-Host "Rebuilding train list..."
python scripts/rebuild_train_list.py

Write-Host "Exporting paddle line dataset..."
python scripts/export_paddle_line_dataset.py

Write-Host "Starting PaddleOCR training..."
docker compose -f docker-compose.train.yml --profile train run --rm paddle-train
Write-Host "PaddleOCR training finished! The new model is in data/training/paddle_rec/models/rec_finetuned/"
