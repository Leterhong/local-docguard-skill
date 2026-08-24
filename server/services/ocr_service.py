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

import os
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
        # Real OpenVINO OCR (PP-OCRv4 OpenVINO build) — runs on CPU / GPU / NPU.
        # Weights are user-supplied (NOT bundled): configure ocr.openvino_det /
        # ocr.openvino_rec / ocr.openvino_dict in model_config.yaml.
        cfg = self.settings.ocr_cfg
        det = cfg.get("openvino_det")
        rec = cfg.get("openvino_rec")
        rec_dict = cfg.get("openvino_dict")
        if not (det and rec and rec_dict):
            logger.warning(
                "OpenVINO OCR 未配置权重路径（ocr.openvino_det / openvino_rec / "
                "openvino_dict）。权重自备（PP-OCRv4 OpenVINO 版 det/rec + 字典），"
                "不随包分发；跳过。"
            )
            self.available = False
            return
        try:
            self._engine = OpenVinoOcrEngine(
                det_path=det, rec_path=rec, dict_path=rec_dict,
                device=cfg.get("device", "CPU"),
            )
            self.available = True
            logger.info("OpenVINO OCR engine initialized (device=%s).", cfg.get("device", "CPU"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenVINO OCR unavailable (%s). OCR will be skipped.", exc)
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


class OpenVinoOcrEngine:
    """OpenVINO-backed OCR using the PP-OCRv4 OpenVINO build.

    Runs on CPU / GPU / NPU via OpenVINO ``device``. Weights (det + rec models
    and the character dictionary) are user-supplied — they are NOT bundled
    with the Skill (per the contest rule that models are user-provided).

    The ``ocr()`` method mirrors the PaddleOCR interface
    (``[[[bbox, (text, score)], ...]]]``) so the public ``OcrService.ocr_image``
    parsing logic works unchanged for both engines.
    """

    def __init__(self, det_path: str, rec_path: str, dict_path: str, device: str = "CPU"):
        try:
            import openvino as ov  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"openvino not installed: {exc}")
        try:
            import cv2  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"opencv-python not installed: {exc}")
        import numpy as np  # noqa: F401

        self._cv2 = cv2
        self._np = np
        self.core = ov.Core()
        self.det = self.core.compile_model(det_path, device)
        self.rec = self.core.compile_model(rec_path, device)
        self.det_in = self.det.inputs[0]
        self.rec_in = self.rec.inputs[0]
        with open(dict_path, encoding="utf-8") as f:
            self.chars = [line.rstrip("\n") for line in f]
        self.device = device

    # PaddleOCR-compatible interface.
    def ocr(self, image_path, cls=True):
        lines = self._lines(str(image_path))
        if not lines:
            return []
        return [[[None, (line, 1.0)] for line in lines]]

    def _lines(self, image_path: str) -> List[str]:
        cv2 = self._cv2
        np = self._np
        img = cv2.imread(image_path)
        if img is None:
            return []
        boxes = self._detect(img)
        out: List[str] = []
        for (x1, y1, x2, y2) in boxes:
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            txt = self._recognize(crop)
            if txt:
                out.append(txt)
        return out

    def _detect(self, img):
        cv2 = self._cv2
        np = self._np
        h, w = img.shape[:2]
        scale = 960.0 / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(img, (nw, nh))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        rgb = (rgb - mean) / std
        blob = rgb.transpose(2, 0, 1)[np.newaxis, ...]
        pred = self.det({self.det_in.any_name: blob})[self.det.outputs[0]]
        prob = np.squeeze(pred)  # [H, W] probability map
        if prob.ndim > 2:
            prob = prob[0]
        mask = (prob > 0.3).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for cnt in contours:
            if cv2.contourArea(cnt) < 10:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            boxes.append((
                int(x / scale), int(y / scale),
                int((x + bw) / scale), int((y + bh) / scale),
            ))
        return boxes

    def _recognize(self, crop):
        cv2 = self._cv2
        np = self._np
        h, w = crop.shape[:2]
        scale = 48.0 / max(h, 1)
        nw = max(int(round(w * scale)), 1)
        resized = cv2.resize(crop, (nw, 48))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        rgb = (rgb - mean) / std
        blob = rgb.transpose(2, 0, 1)[np.newaxis, ...]
        pred = self.rec({self.rec_in.any_name: blob})[self.rec.outputs[0]]
        logits = np.squeeze(pred)
        if logits.ndim == 2 and logits.shape[0] != len(self.chars) and logits.shape[1] == len(self.chars):
            logits = logits.T
        preds = np.argmax(logits, axis=-1)
        chars = []
        prev = -1
        for p in preds:
            p = int(p)
            if p == 0:
                prev = p
                continue
            if p != prev and 1 <= p < len(self.chars):
                chars.append(self.chars[p])
            prev = p
        return "".join(chars)
