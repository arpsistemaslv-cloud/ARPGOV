"""Gera PDF a partir de MANUAL_SISTEMA_ARPGOV.md."""
from __future__ import annotations

from pathlib import Path

import markdown
from xhtml2pdf import pisa

ROOT = Path(__file__).resolve().parent.parent
MD_PATH = ROOT / "MANUAL_SISTEMA_ARPGOV.md"
PDF_PATH = ROOT / "MANUAL_SISTEMA_ARPGOV.pdf"

CSS = """
@page { size: A4; margin: 18mm 15mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; line-height: 1.45; color: #1a202c; }
h1 { color: #1a365d; font-size: 22pt; border-bottom: 3px solid #2b6cb0; padding-bottom: 8px; page-break-after: avoid; }
h2 { color: #2c5282; font-size: 15pt; margin-top: 22px; page-break-after: avoid; }
h3 { color: #2d3748; font-size: 12pt; margin-top: 16px; page-break-after: avoid; }
h4 { color: #4a5568; font-size: 11pt; }
p, li { margin: 4px 0; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 9pt; page-break-inside: avoid; }
th, td { border: 1px solid #cbd5e0; padding: 5px 7px; text-align: left; vertical-align: top; }
th { background: #edf2f7; font-weight: bold; }
code { background: #edf2f7; padding: 1px 4px; font-size: 9pt; }
pre { background: #f7fafc; border: 1px solid #e2e8f0; padding: 10px; font-size: 8pt; line-height: 1.3; white-space: pre-wrap; page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 18px 0; }
strong { color: #1a365d; }
ul, ol { margin: 6px 0 10px 18px; }
a { color: #2b6cb0; text-decoration: none; }
"""


def main() -> None:
    md_content = MD_PATH.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_content,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>{html_body}</body></html>"
    )

    with PDF_PATH.open("wb") as pdf_file:
        status = pisa.CreatePDF(html, dest=pdf_file, encoding="utf-8")

    if status.err:
        raise SystemExit(f"Erro ao gerar PDF (code={status.err})")

    size_kb = PDF_PATH.stat().st_size // 1024
    print(f"PDF gerado: {PDF_PATH} ({size_kb} KB)")


if __name__ == "__main__":
    main()
