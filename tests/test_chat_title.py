import importlib.util
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("belener_app", _root / "app.py")
app_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(app_mod)

derive_chat_title = app_mod.derive_chat_title


def test_normative_with_pdf():
    t = derive_chat_title("все gost", "BNP_007-1-ГСВ1_L2_Изм.1.pdf")
    assert t == "ГОСТ: BNP_007-1-ГСВ1_L2_Изм.1"


def test_extract_default_with_pdf():
    t = derive_chat_title("Извлечь весь текст с листа", "sheet.pdf")
    assert t == "Разбор листа: sheet"


def test_question_only():
    t = derive_chat_title("Какой диаметр трубы на схеме?", None)
    assert "диаметр" in t.casefold()


def test_file_only():
    t = derive_chat_title("", "BNP_007-1-ГСВ1_L2_Изм.1.pdf")
    assert t == "BNP_007-1-ГСВ1_L2_Изм.1"
