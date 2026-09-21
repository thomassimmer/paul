"""PDF export and page counting.

LibreOffice does the real conversion when it is installed — the default Docker
image includes it. Without it, a line-count estimate keeps the fit check useful
instead of silently skipped, which is what happens when the image is built with
``WITH_PDF=0``, and the preview on the review screen falls back to plain HTML.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from app.models import DraftLine

# A page holds roughly this many rendered lines; the estimate only has to be
# close enough to notice a document that is far too long.
LINES_PER_PAGE = 45

# LibreOffice runs on a single shared user profile and fails when two headless
# conversions start on it at once. A preview and a background fit check can
# overlap, so every conversion goes through this lock.
_CONVERT_LOCK = threading.Lock()


def converter() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _convert(docx_bytes: bytes, timeout: float) -> bytes | None:
    """The document as a PDF, or ``None`` when the conversion is not possible."""
    binary = converter()
    if binary is None:
        return None

    with _CONVERT_LOCK, tempfile.TemporaryDirectory() as folder:
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


def count_pages(docx_bytes: bytes, *, timeout: float = 120.0) -> int | None:
    """The real page count, or ``None`` when no converter is available."""
    data = _convert(docx_bytes, timeout)
    if data is None:
        return None
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - an unreadable PDF falls back to the estimate
        return None


def estimate_pages(lines: list[DraftLine]) -> int:
    """The fallback used when the converter is missing."""
    return max(1, -(-len(lines) // LINES_PER_PAGE))


def to_pdf(docx_bytes: bytes, *, timeout: float = 120.0) -> bytes | None:
    """The document as a PDF, or ``None`` when no converter is available."""
    return _convert(docx_bytes, timeout)


def page_count(docx_bytes: bytes, lines: list[DraftLine]) -> tuple[int, bool]:
    """``(pages, exact)`` — ``exact`` is False when this is only an estimate."""
    measured = count_pages(docx_bytes)
    if measured is None:
        return estimate_pages(lines), False
    return measured, True
