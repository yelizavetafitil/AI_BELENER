"""Форматирование tile OCR в читаемый Markdown через локальную Ollama."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

log = logging.getLogger("belener.report_llm")

_SYSTEM = (
    "Ты редактор инженерных чертежей. На вход — сырой OCR (дубли, опечатки). "
    "Сформируй читаемый отчёт на русском в Markdown для инженера. "
    "ЗАПРЕЩЕНО: не выводи сырой OCR, блоки ```text```, повторы схемы и мусор распознавания. "
    "Не выдумывай факты — только OCR и блок «Проверенные нормативы». "
    "Номера ГОСТ/ОСТ/ТУ из «Проверенных нормативов» точнее OCR. "
    "Разделы (пропускай пустые): "
    "## Основная надпись (организация, объект, лист, подписи), "
    "## Спецификация (таблица | Поз. | Обозначение | Наименование | Кол. | Масса | Примечание |), "
    "## Технические требования (нумерованный список), "
    "## Нормативные документы (таблица | Тип | Обозначение |). "
    "Объедини дубли таблиц в одну. Исправляй очевидные OCR-ошибки в словах, не в номерах ГОСТ."
)


def _normalize_block(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip().casefold())
    return t[:120]


def dedupe_ocr_text(text: str, *, max_chars: int = 14000) -> str:
    """Убрать повторяющиеся фрагменты OCR перед отправкой в LLM."""
    if not (text or "").strip():
        return ""
    seen: set[str] = set()
    kept: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if len(b) < 8:
            continue
        key = _normalize_block(b)
        if key in seen:
            continue
        seen.add(key)
        kept.append(b)
    out = "\n\n".join(kept)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n…"
    return out


def _normative_block(refs: list[dict[str, str]]) -> str:
    if not refs:
        return ""
    lines = ["Проверенные нормативы (парсер, приоритет над OCR):"]
    for n in refs:
        kind = str(n.get("kind") or "").strip()
        ref = str(n.get("ref") or "").strip()
        if kind and ref:
            lines.append(f"- {kind} {ref}")
    return "\n".join(lines)


def _build_user_prompt(
    ocr: str,
    refs: list[dict[str, str]],
    *,
    mode: str,
    filename: str,
) -> str:
    parts = [f"Файл: {filename or 'document.pdf'}", ""]
    nb = _normative_block(refs)
    if nb:
        parts.extend([nb, ""])
    parts.extend(["Сырой OCR (только для анализа, не копируй в ответ):", ocr, ""])
    if mode == "text":
        parts.append("Режим: извлечение. Структурируй весь текст листа без сырого OCR.")
    elif mode == "analysis":
        parts.append("Режим: разбор. Полная структура + нормативы.")
    else:
        parts.append("Режим: полный отчёт по листу.")
    return "\n".join(parts)


def _available_models(client) -> list[str]:
    try:
        return [str(m.get("name") or "") for m in client.list().get("models") or [] if m.get("name")]
    except Exception:
        return []


def _model_candidates(preferred: str, available: list[str]) -> list[str]:
    out: list[str] = []
    if preferred in available:
        out.append(preferred)
    base = preferred.split(":")[0]
    for name in available:
        if name not in out and name.startswith(base + ":"):
            out.append(name)
    for name in available:
        if name not in out and "vl" not in name.casefold():
            out.append(name)
    for name in available:
        if name not in out:
            out.append(name)
    return out


def _resolve_model(client) -> str:
    from belener.config import report_llm_model

    available = _available_models(client)
    preferred = report_llm_model()
    candidates = _model_candidates(preferred, available)
    return candidates[0] if candidates else preferred


def _call_ollama(model: str, prompt: str, *, timeout_sec: int, num_predict: int) -> str:
    import ollama

    from belener.config import ollama_host

    def _run() -> str:
        client = ollama.Client(host=ollama_host())
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            options={
                "temperature": 0.05,
                "num_predict": num_predict,
                "num_ctx": 6144,
            },
            stream=False,
        )
        return str(resp.get("message", {}).get("content") or "").strip()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        return fut.result(timeout=timeout_sec)


def format_ocr_report_llm(
    drawing: dict[str, Any],
    *,
    mode: str = "full",
    filename: str = "",
) -> str | None:
    from belener.config import ollama_host, report_llm_num_predict, report_llm_timeout_sec

    pages = drawing.get("full_text_pages") or []
    raw = "\n\n".join(str(p.get("text") or "").strip() for p in pages if str(p.get("text") or "").strip())
    if len(raw.strip()) < 40:
        return None

    ocr = dedupe_ocr_text(raw)
    refs = list(drawing.get("normative_refs") or [])
    prompt = _build_user_prompt(ocr, refs, mode=mode, filename=filename)

    try:
        import ollama

        client = ollama.Client(host=ollama_host())
        available = _available_models(client)
        preferred = _resolve_model(client)
        candidates = _model_candidates(preferred, available) if available else [preferred]

        last_err: Exception | None = None
        for model in candidates[:4]:
            try:
                log.info("report LLM start model=%s ocr_chars=%s mode=%s", model, len(ocr), mode)
                text = _call_ollama(
                    model,
                    prompt,
                    timeout_sec=report_llm_timeout_sec(),
                    num_predict=report_llm_num_predict(),
                )
                if len(text) < 120:
                    log.warning("report LLM: короткий ответ model=%s (%s chars)", model, len(text))
                    continue
                if "```text" in text.casefold() and text.count("```") >= 2:
                    log.warning("report LLM: сырой OCR в ответе model=%s", model)
                    continue
                log.info("report LLM done model=%s chars=%s", model, len(text))
                return text.strip() + "\n"
            except Exception as e:
                last_err = e
                log.warning("report LLM model=%s failed: %s", model, e)
                continue

        if last_err:
            raise last_err
        return None
    except FuturesTimeout:
        log.warning("report LLM timeout %ss", report_llm_timeout_sec())
        return None
    except Exception:
        log.warning("report LLM failed", exc_info=True)
        return None
