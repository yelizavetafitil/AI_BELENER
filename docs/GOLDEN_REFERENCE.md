# Эталонные листы (2 правильных вывода)

Используются для регрессии парсера и обучения OCR (labels).

| Файл | Тип | Golden JSON |
|------|-----|-------------|
| `BNP_1760-228-ЭМ1 л.5.pdf` | Заземление, спецификация | `data/training/golden/BNP_1760-228-ЭМ1_л.5.json` |
| `(10-16-25…)1118-0-ГП9 л.4.pdf` | Генплан, экспликация | `data/training/golden/_10-16-25_…_1118-0-ГП9_л.4.json` |

Текст для OCR: `data/training/labels/<stem>_spec_right.txt`, `…_stamp_frame.txt`.

## Проверка парсера

```powershell
python scripts/validate_golden.py
python -m pytest tests/test_golden_reference.py -q
```

## Пересборка train_list после правки labels

```powershell
python scripts/rebuild_train_list.py
```

## Веб: новый независимый PDF

```powershell
copy .env.golden.example .env
docker compose -f docker-compose.yml -f docker-compose.surya.yml --profile surya up -d
```

Откройте http://localhost:8090 и загрузите **любой** PDF из `scan/` (не из эталонных двух).

Ожидание: без vision-предупреждений; экспликация/спецификация по типу листа; для сложных листов — сверка с PDF.
