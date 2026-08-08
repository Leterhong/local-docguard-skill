"""
OCR service for scanned PDFs and images.

Uses PaddleOCR when available (best for Chinese). Gracefully degrades:
if PaddleOCR / paddlepaddle is not installed, the service reports
`available = False` and the parser will simply retain whatever text it
could extract (or an empty result). It never blocks the pipeline.

An OpenVINO-based OCR path can be wired in by implementing OcrEngine and
selecting it via the config `ocr.engine` field.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from server.config import Settings
from server.services.security import get_logger

logger = get_logger("ocr")


class OcrService:
    def __init__(self, settings: Settings):
        self.settings = settings
        cfg = settings.ocr_cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.engine_name = cfg.get("engine", "paddleocr")
        self.lang = cfg.get("lang", "ch")
        self.device = cfg.get("device", "CPU")
        self._engine = None
        self.available = False
        self._init_engine()

    def _init_engine(self) -> None:
        if not self.enabled:
            logger.info("OCR disabled by configuration.")
            return
        if self.engine_name == "paddleocr":
            self._init_paddleocr()
        elif self.engine_name == "openvino":
            self._init_openvino_ocr()
        else:
            logger.warning("Unknown OCR engine '%s'; OCR disabled.", self.engine_name)

    def _init_paddleocr(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore

            self._engine = PaddleOCR(
                use_angle_cls=self.settings.ocr_cfg.get("use_angle_cls", True),
                lang=self.lang,
                show_log=False,
            )
            self.available = True
            logger.info("PaddleOCR initialized (lang=%s).", self.lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PaddleOCR not available (%s). OCR will be skipped. "
                "Install with: pip install paddlepaddle paddleocr",
                exc,
            )
            self.available = False

    def _init_openvino_ocr(self) -> None:
        # Placeholder: OpenVINO OCR model integration.
        logger.info("OpenVINO OCR engine selected; not yet bundled. Skipping.")
        self.available = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def ocr_image(self, image_path: Path) -> str:
        if not self.available or self._engine is None:
            return ""
        try:
            result = self._engine.ocr(str(image_path), cls=True)
            lines: List[str] = []
            for page in result or []:
                for line in page or []:
                    if line and len(line) >= 2:
                        text = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
                        lines.append(text)
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            logger.error("OCR failed for %s: %s", image_path, exc)
            return ""

    def ocr_pdf(self, pdf_path: Path) -> List[str]:
        """Render PDF pages to images then OCR each page.

        Requires pdf2image + poppler, or PyMuPDF. Returns one string per page.
        """
        if not self.available:
            return []
        images = self._render_pdf_to_images(pdf_path)
        pages: List[str] = []
        for i, img in enumerate(images, start=1):
            text = self.ocr_image(img)
            pages.append(text)
            try:
                img.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        return pages

    def _render_pdf_to_images(self, pdf_path: Path) -> List[Path]:
        """Render PDF pages to temporary PNG files. Tries PyMuPDF then pdf2image."""
        import tempfile

        tmpdir = Path(tempfile.mkdtemp(prefix="docguard_ocr_"))
        out: List[Path] = []

        # Try PyMuPDF (fitz)
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(pdf_path))
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=200)
                img_path = tmpdir / f"page_{i + 1:04d}.png"
                pix.save(str(img_path))
                out.append(img_path)
            doc.close()
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("PyMuPDF render unavailable (%s); trying pdf2image.", exc)

        # Fallback: pdf2image (requires poppler)
        try:
            from pdf2image import convert_from_path  # type: ignore

            pages = convert_from_path(str(pdf_path), dpi=200)
            for i, img in enumerate(pages, start=1):
                img_path = tmpdir / f"page_{i:04d}.png"
                img.save(str(img_path), "PNG")
                out.append(img_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PDF rendering unavailable (install PyMuPDF or pdf2image+poppler): %s",
                exc,
            )
        return out
