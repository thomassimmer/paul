"""Page counting for the fit check.

LibreOffice does the real conversion when it is installed — the default Docker
image includes it. Without it, a line-count estimate keeps the fit check useful
instead of silently skipped, which is what happens when the image is built with
``WITH_PDF=0``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.models import DraftLine

# A page holds roughly this many rendered lines; the estimate only has to be
# close enough to notice a document that is far too long.
LINES_PER_PAGE = 45


def converter() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def count_pages(docx_bytes: bytes, *, timeout: float = 120.0) -> int | None:
    """The real page count, or ``None`` when no converter is available."""
    binary = converter()
    if binary is None:
        return None

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "document.docx"
        source.write_bytes(docx_bytes)
        try:
            subprocess.run(
                [
                    binary,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    folder,
                    str(source),
                ],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        pdf = Path(folder) / "document.pdf"
        if not pdf.exists():
            return None
        try:
            from pypdf import PdfReader

            return len(PdfReader(str(pdf)).pages)
        except Exception:
            return None


def estimate_pages(lines: list[DraftLine]) -> int:
    """The fallback used when the converter is missing."""
    return max(1, -(-len(lines) // LINES_PER_PAGE))


def to_pdf(docx_bytes: bytes, *, timeout: float = 120.0) -> bytes | None:
    """The document as a PDF, or ``None`` when no converter is available."""
    binary = converter()
    if binary is None:
        return None

    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "document.docx"
        source.write_bytes(docx_bytes)
        try:
            subprocess.run(
                [
                    binary,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    folder,
                    str(source),
                ],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        pdf = Path(folder) / "document.pdf"
        return pdf.read_bytes() if pdf.exists() else None


def page_count(docx_bytes: bytes, lines: list[DraftLine]) -> tuple[int, bool]:
    """``(pages, exact)`` — ``exact`` is False when this is only an estimate."""
    measured = count_pages(docx_bytes)
    if measured is None:
        return estimate_pages(lines), False
    return measured, True
