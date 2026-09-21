"""One-time Florence-2 setup and smoke test."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    print("=" * 60)
    print("KURO VISION - Florence-2 Setup")
    print("=" * 60)

    deps = {
        "torch": "pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121",
        "transformers": "pip install transformers>=4.46,<5",
        "PIL": "pip install pillow",
        "mss": "pip install mss",
        "timm": "pip install timm",
        "einops": "pip install einops",
    }

    print("\n[1/4] Checking dependencies...")
    missing = []
    for pkg, cmd in deps.items():
        try:
            __import__(pkg)
            print(f"  OK {pkg}")
        except ImportError:
            print(f"  MISSING {pkg} - {cmd}")
            missing.append(pkg)

    if missing:
        print("\nInstall missing dependencies and re-run.")
        return

    print("\n[2/4] Checking CUDA...")
    import torch

    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory // (1024**2)
        free = torch.cuda.mem_get_info()[0] // (1024**2)
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {free}MB free / {total}MB total")
    else:
        print("  CUDA not detected, model will run on CPU")

    print("\n[3/4] Downloading/loading model...")
    t0 = time.time()

    from transformers import AutoModelForCausalLM, AutoProcessor

    model_id = "microsoft/Florence-2-base"
    try:
        processor = AutoProcessor.from_pretrained(
            model_id,
            trust_remote_code=True,
            use_fast=False,
        )
    except Exception as e:
        print(f"  AutoProcessor load failed ({e}), using explicit Florence2Processor")
        from transformers import AutoImageProcessor, BartTokenizerFast
        from transformers.dynamic_module_utils import get_class_from_dynamic_module

        Florence2Processor = get_class_from_dynamic_module(
            "processing_florence2.Florence2Processor",
            model_id,
            trust_remote_code=True,
        )
        image_processor = AutoImageProcessor.from_pretrained(
            model_id,
            trust_remote_code=True,
            use_fast=False,
        )
        tokenizer = BartTokenizerFast.from_pretrained(model_id)
        if not hasattr(tokenizer, "additional_special_tokens"):
            tokenizer.additional_special_tokens = []
        processor = Florence2Processor(image_processor=image_processor, tokenizer=tokenizer)

    if torch.cuda.is_available():
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.float16,
            trust_remote_code=True,
        ).to("cuda")
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.float32,
            trust_remote_code=True,
        )

    print(f"  Model ready in {time.time() - t0:.1f}s")

    print("\n[4/4] Running quick caption test...")
    from PIL import Image
    import mss

    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX").resize((768, 768), Image.LANCZOS)

    inputs = processor(text="<CAPTION>", images=img, return_tensors="pt")
    if torch.cuda.is_available():
        inputs = {k: (v.cuda() if isinstance(v, torch.Tensor) else v) for k, v in inputs.items()}

    with torch.inference_mode():
        out = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs.get("pixel_values"),
            max_new_tokens=64,
            num_beams=1,
        )

    text = processor.batch_decode(out, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(text, task="<CAPTION>", image_size=(768, 768))
    print("  Caption:", parsed.get("<CAPTION>", "")[:120])
    print("\nSetup complete.")


if __name__ == "__main__":
    main()
