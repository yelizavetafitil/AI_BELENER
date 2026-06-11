from belener.extract import extract_pdf_bytes, extract_pdf_path
from belener.extract_report import extraction_to_markdown


def analyze_pdf_path(path: str, filename: str | None = None):
    return extract_pdf_path(path, filename)


def analyze_pdf_path_markdown(path: str, filename: str | None = None) -> str:
    return extraction_to_markdown(extract_pdf_path(path, filename))


__all__ = [
    "extract_pdf_bytes",
    "extract_pdf_path",
    "extraction_to_markdown",
    "analyze_pdf_path",
    "analyze_pdf_path_markdown",
]
