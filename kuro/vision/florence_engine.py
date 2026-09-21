from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from PIL import Image

logger = logging.getLogger(__name__)

TASK_CAPTION = "<CAPTION>"
TASK_DETAILED_CAPTION = "<DETAILED_CAPTION>"
TASK_MORE_DETAILED = "<MORE_DETAILED_CAPTION>"
TASK_OCR = "<OCR>"
TASK_OCR_WITH_REGION = "<OCR_WITH_REGION>"
TASK_OD = "<OD>"
TASK_DENSE_CAPTION = "<DENSE_REGION_CAPTION>"
TASK_GROUNDING = "<CAPTION_TO_PHRASE_GROUNDING>"


class FlorenceEngine:
    _instance: Optional["FlorenceEngine"] = None
    _instance_lock = threading.Lock()

    MODEL_ID = "microsoft/Florence-2-base"

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._device: Optional[str] = None
        self._loaded = False
        self._load_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "FlorenceEngine":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._load_model()

    def _load_model(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        from core.config import FLORENCE_DEVICE, FLORENCE_MODEL, FLORENCE_QUANTIZE

        t0 = time.perf_counter()
        self.MODEL_ID = FLORENCE_MODEL or self.MODEL_ID

        if FLORENCE_DEVICE == "cpu":
            self._device = "cpu"
        elif FLORENCE_DEVICE == "cuda":
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"

        if self._device == "cuda":
            free_mb = torch.cuda.mem_get_info()[0] // (1024 * 1024)
            if free_mb < 600:
                logger.warning("[Florence] Low VRAM (%sMB), forcing CPU", free_mb)
                self._device = "cpu"

        try:
            # Florence remote processor currently works more reliably with the slow path.
            self._processor = AutoProcessor.from_pretrained(
                self.MODEL_ID,
                trust_remote_code=True,
                use_fast=False,
            )
        except Exception as e:
            logger.warning("[Florence] AutoProcessor load failed (%s), using explicit Florence2Processor", e)
            from transformers import AutoImageProcessor, BartTokenizerFast
            from transformers.dynamic_module_utils import get_class_from_dynamic_module

            Florence2Processor = get_class_from_dynamic_module(
                "processing_florence2.Florence2Processor",
                self.MODEL_ID,
                trust_remote_code=True,
            )
            image_processor = AutoImageProcessor.from_pretrained(
                self.MODEL_ID,
                trust_remote_code=True,
                use_fast=False,
            )
            tokenizer = BartTokenizerFast.from_pretrained(self.MODEL_ID)
            if not hasattr(tokenizer, "additional_special_tokens"):
                tokenizer.additional_special_tokens = []
            self._processor = Florence2Processor(
                image_processor=image_processor,
                tokenizer=tokenizer,
            )

        if self._device == "cuda":
            loaded = False
            if FLORENCE_QUANTIZE == "int8":
                try:
                    from transformers import BitsAndBytesConfig

                    quant_config = BitsAndBytesConfig(load_in_8bit=True, llm_int8_threshold=6.0)
                    self._model = AutoModelForCausalLM.from_pretrained(
                        self.MODEL_ID,
                        quantization_config=quant_config,
                        device_map="auto",
                        trust_remote_code=True,
                    )
                    loaded = True
                    logger.info("[Florence] Loaded with INT8")
                except Exception as e:
                    logger.info("[Florence] INT8 load failed (%s), trying FP16", e)
            elif FLORENCE_QUANTIZE == "fp16":
                logger.info("[Florence] Quantization policy set to FP16")
            elif FLORENCE_QUANTIZE == "fp32":
                logger.info("[Florence] Quantization policy set to FP32")

            if not loaded:
                dtype = torch.float16 if FLORENCE_QUANTIZE != "fp32" else torch.float32
                self._model = AutoModelForCausalLM.from_pretrained(
                    self.MODEL_ID,
                    torch_dtype=dtype,
                    trust_remote_code=True,
                ).to(self._device)
        else:
            self._model = AutoModelForCausalLM.from_pretrained(
                self.MODEL_ID,
                torch_dtype=torch.float32,
                trust_remote_code=True,
            )

        self._model.eval()

        try:
            if self._device == "cuda" and hasattr(torch, "compile"):
                self._model = torch.compile(self._model, mode="reduce-overhead")
        except Exception as e:
            logger.debug("[Florence] torch.compile skipped: %s", e)

        self._loaded = True
        logger.info(
            "[Florence] Ready in %.1fs on %s (model=%s, quant=%s)",
            time.perf_counter() - t0,
            self._device,
            self.MODEL_ID,
            FLORENCE_QUANTIZE,
        )

    def _run_task(self, image: Image.Image, task: str, text_input: str = "", max_new_tokens: int = 512) -> Dict[str, Any]:
        import torch

        self.ensure_loaded()

        prompt = task if not text_input else f"{task}{text_input}"
        inputs = self._processor(text=prompt, images=image, return_tensors="pt")

        if self._device == "cuda":
            inputs = {k: (v.cuda() if isinstance(v, torch.Tensor) else v) for k, v in inputs.items()}

        with torch.inference_mode():
            generated_ids = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs.get("pixel_values"),
                max_new_tokens=max_new_tokens,
                early_stopping=False,
                do_sample=False,
                num_beams=1,
            )

        generated_text = self._processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        return self._processor.post_process_generation(
            generated_text,
            task=task,
            image_size=(image.width, image.height),
        )

    def caption(self, image: Image.Image, detail: str = "normal") -> str:
        task = {
            "brief": TASK_CAPTION,
            "normal": TASK_DETAILED_CAPTION,
            "detailed": TASK_MORE_DETAILED,
        }.get(detail, TASK_DETAILED_CAPTION)
        return self._run_task(image, task).get(task, "")

    def ocr(self, image: Image.Image) -> List[Dict[str, Any]]:
        result = self._run_task(image, TASK_OCR_WITH_REGION)
        data = result.get(TASK_OCR_WITH_REGION, {})
        labels = data.get("labels", [])
        quad_boxes = data.get("quad_boxes", [])

        items: List[Dict[str, Any]] = []
        for i, label in enumerate(labels):
            q = quad_boxes[i] if i < len(quad_boxes) else []
            if len(q) >= 8:
                xs = [q[j] for j in range(0, 8, 2)]
                ys = [q[j] for j in range(1, 8, 2)]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
            else:
                bbox = q
            items.append({"text": label, "bbox": bbox})
        return items

    def ocr_plain(self, image: Image.Image) -> str:
        return self._run_task(image, TASK_OCR).get(TASK_OCR, "")

    def ground(self, image: Image.Image, phrase: str) -> List[Dict[str, Any]]:
        result = self._run_task(image, TASK_GROUNDING, text_input=phrase)
        data = result.get(TASK_GROUNDING, {})
        labels = data.get("labels", [])
        bboxes = data.get("bboxes", [])

        out: List[Dict[str, Any]] = []
        for i, label in enumerate(labels):
            out.append({"label": label, "bbox": bboxes[i] if i < len(bboxes) else []})
        return out

    def analyze(self, image: Image.Image) -> Dict[str, Any]:
        t0 = time.perf_counter()
        caption = self.caption(image, detail="normal")
        ocr_elements = self.ocr(image)
        ocr_text = " ".join(e["text"] for e in ocr_elements)
        return {
            "caption": caption,
            "ocr_text": ocr_text,
            "ocr_elements": ocr_elements,
            "timestamp": time.time(),
            "analysis_ms": (time.perf_counter() - t0) * 1000,
        }

    def find_element(self, image: Image.Image, description: str, fallback_ocr: bool = True) -> Optional[Dict[str, Any]]:
        grounded = self.ground(image, description)
        if grounded:
            bbox = grounded[0].get("bbox", [])
            if len(bbox) >= 4:
                cx = int((bbox[0] + bbox[2]) / 2)
                cy = int((bbox[1] + bbox[3]) / 2)
                return {
                    "bbox": bbox,
                    "center": (cx, cy),
                    "method": "grounding",
                    "label": grounded[0].get("label", description),
                    "confidence": 0.9,
                }

        if not fallback_ocr:
            return None

        import difflib

        description_lower = description.lower()
        best_match = None
        best_ratio = 0.0
        for elem in self.ocr(image):
            text = elem.get("text", "")
            text_lower = text.lower()
            if description_lower in text_lower or text_lower in description_lower:
                bbox = elem.get("bbox", [])
                if len(bbox) >= 4:
                    cx = int((bbox[0] + bbox[2]) / 2)
                    cy = int((bbox[1] + bbox[3]) / 2)
                    return {
                        "bbox": bbox,
                        "center": (cx, cy),
                        "method": "ocr",
                        "label": text,
                        "confidence": 0.85,
                    }
            ratio = difflib.SequenceMatcher(None, description_lower, text_lower).ratio()
            if ratio > best_ratio and ratio > 0.6:
                best_ratio = ratio
                best_match = elem

        if best_match:
            bbox = best_match.get("bbox", [])
            if len(bbox) >= 4:
                cx = int((bbox[0] + bbox[2]) / 2)
                cy = int((bbox[1] + bbox[3]) / 2)
                return {
                    "bbox": bbox,
                    "center": (cx, cy),
                    "method": "ocr_fuzzy",
                    "label": best_match.get("text", description),
                    "confidence": best_ratio,
                }

        return None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def device(self) -> str:
        return self._device or "not_loaded"
