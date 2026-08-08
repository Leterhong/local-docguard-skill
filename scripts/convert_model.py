"""
DocGuard AI — OpenVINO model conversion.

Converts a HuggingFace model into OpenVINO IR (.xml + .bin) for fast,
local inference on Intel CPU / GPU / NPU.

Pipeline:
    PyTorch / HF weights  ->  OpenVINO IR  ->  OpenVINO Runtime

Usage:
    # LLM (Qwen3 / Qwen2.5), INT4 weight-compression
    python scripts/convert_model.py --model Qwen/Qwen3-8B --out .openvino/llm --int4

    # Embedding model
    python scripts/convert_model.py --model BAAI/bge-small-zh --out .openvino/embedding

    # From a local directory instead of HF hub id
    python scripts/convert_model.py --model ./my-qwen --out .openvino/llm --int4

Notes:
    * Requires: openvino, optimum-intel, transformers, torch
    * --int4 uses NNCF weight-compression (recommended for <=35B on AI PC)
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="Convert HF model to OpenVINO IR")
    p.add_argument("--model", required=True, help="HF model id or local path")
    p.add_argument("--out", required=True, help="Output directory for IR")
    p.add_argument("--int4", action="store_true",
                   help="Apply INT4 weight compression (LLM only)")
    p.add_argument("--task", default="text-generation",
                   help="optimum task, e.g. text-generation / feature-extraction")
    p.add_argument("--device", default="CPU", help="Target device hint (CPU/GPU/NPU)")
    return p.parse_args()


def convert_int4_llm(model_id: str, out_dir: str):
    """Convert a causal-LM to INT4 OpenVINO IR using optimum-intel + NNCF."""
    from optimum.intel import OVModelForCausalLM  # noqa: F401
    from nncf import QuantizationPreset  # noqa: F401

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[convert] LLM -> INT4 -> {out}")
    model = OVModelForCausalLM.from_pretrained(
        model_id,
        export=True,
        load_in_8bit=False,
        # weight compression to INT4 via NNCF
        quantization_config={"mode": "int4", "group_size": 128, "ratio": 1.0},
        device="CPU",
    )
    model.save_pretrained(str(out))
    print(f"[convert] saved INT4 IR to {out}")


def convert_fp16_llm(model_id: str, out_dir: str):
    from optimum.intel import OVModelForCausalLM  # noqa: F401

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[convert] LLM -> FP16 -> {out}")
    model = OVModelForCausalLM.from_pretrained(model_id, export=True, device="CPU")
    model.save_pretrained(str(out))
    print(f"[convert] saved FP16 IR to {out}")


def convert_embedding(model_id: str, out_dir: str):
    from optimum.intel import OVModelForFeatureExtraction  # noqa: F401

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[convert] Embedding -> {out}")
    model = OVModelForFeatureExtraction.from_pretrained(model_id, export=True)
    model.save_pretrained(str(out))
    print(f"[convert] saved embedding IR to {out}")


def main():
    args = parse_args()
    try:
        if args.task == "feature-extraction":
            convert_embedding(args.model, args.out)
        elif args.int4:
            convert_int4_llm(args.model, args.out)
        else:
            convert_fp16_llm(args.model, args.out)
    except ImportError as e:
        print("缺少依赖，请先安装：pip install openvino optimum-intel nncf torch transformers")
        print(f"  详情: {e}")
        sys.exit(1)
    except Exception as e:  # pragma: no cover
        print(f"转换失败：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
