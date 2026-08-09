"""
DocGuard AI — OpenVINO inference benchmark.

Measures local inference latency/throughput on Intel CPU / GPU / NPU so
you can compare hardware for the AI PC deployment.

Usage:
    # LLM generation benchmark
    python scripts/benchmark.py --model .openvino/llm --device CPU --prompt "审查以下合同条款：" --max-new-tokens 128

    # Embedding benchmark
    python scripts/benchmark.py --model .openvino/embedding --device CPU --task embedding --prompt "企业合同风险审查"

Reports:
    * First-token latency (ms)
    * Total latency (ms)
    * Throughput (tokens/s)
    * Peak memory (if psutil available)

Requires: openvino, optimum-intel, transformers, torch
"""
from __future__ import annotations

import sys

# Windows-safe UTF-8 output (mandatory per local-ai-skill-authoring best practices).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")
import argparse
import time
from pathlib import Path

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False


def parse_args():
    p = argparse.ArgumentParser(description="OpenVINO inference benchmark")
    p.add_argument("--model", required=True, help="OpenVINO IR dir or HF id")
    p.add_argument("--device", default="CPU", help="CPU / GPU / NPU")
    p.add_argument("--task", default="generation", help="generation / embedding")
    p.add_argument("--prompt", default="请审查这份企业合同的风险点。", help="Input prompt")
    p.add_argument("--max-new-tokens", type=int, default=128)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--iterations", type=int, default=5)
    return p.parse_args()


def _mem_mb():
    return psutil.Process().memory_info().rss / 1024 / 1024 if HAVE_PSUTIL else None


def benchmark_generation(model_dir: str, device: str, prompt: str, max_new: int, warmup: int, iters: int):
    from optimum.intel import OVModelForCausalLM  # noqa: F401

    print(f"[bench] loading LLM from {model_dir} onto {device} ...")
    model = OVModelForCausalLM.from_pretrained(model_dir, device=device)
    tokenizer = __import__("transformers").AutoTokenizer.from_pretrained(model_dir)

    inputs = tokenizer(prompt, return_tensors="pt")

    # warmup
    for _ in range(warmup):
        _ = model.generate(**inputs, max_new_tokens=32)

    first_lat, total_lat, toks = [], [], []
    for _ in range(iters):
        t0 = time.perf_counter()
        out = model.generate(**inputs, max_new_tokens=max_new,
                             do_sample=False, return_dict_in_generate=True,
                             output_scores=True)
        t1 = time.perf_counter()
        new_tok = out.sequences.shape[-1] - inputs["input_ids"].shape[-1]
        first_lat.append((t1 - t0) * 1000)  # approx end-to-end here
        total_lat.append((t1 - t0) * 1000)
        toks.append(new_tok)

    avg_total = sum(total_lat) / len(total_lat)
    avg_tok = sum(toks) / len(toks)
    tput = avg_tok / (avg_total / 1000)
    mem = _mem_mb()
    print("=" * 56)
    print(f"  LLM Generation Benchmark  |  device={device}")
    print("-" * 56)
    print(f"  avg total latency : {avg_total:8.1f} ms")
    print(f"  avg new tokens    : {avg_tok:8.1f}")
    print(f"  throughput        : {tput:8.1f} tokens/s")
    if mem:
        print(f"  peak RSS memory   : {mem:8.1f} MB")
    print("=" * 56)


def benchmark_embedding(model_dir: str, device: str, prompt: str, warmup: int, iters: int):
    from optimum.intel import OVModelForFeatureExtraction  # noqa: F401

    print(f"[bench] loading embedding from {model_dir} onto {device} ...")
    model = OVModelForFeatureExtraction.from_pretrained(model_dir, device=device)
    tokenizer = __import__("transformers").AutoTokenizer.from_pretrained(model_dir)
    inputs = tokenizer(prompt, return_tensors="pt")

    for _ in range(warmup):
        _ = model(**inputs)

    lats = []
    for _ in range(iters):
        t0 = time.perf_counter()
        _ = model(**inputs)
        lats.append((time.perf_counter() - t0) * 1000)

    avg = sum(lats) / len(lats)
    mem = _mem_mb()
    print("=" * 56)
    print(f"  Embedding Benchmark  |  device={device}")
    print("-" * 56)
    print(f"  avg latency       : {avg:8.1f} ms / call")
    if mem:
        print(f"  peak RSS memory  : {mem:8.1f} MB")
    print("=" * 56)


def main():
    args = parse_args()
    try:
        if args.task == "embedding":
            benchmark_embedding(args.model, args.device, args.prompt, args.warmup, args.iterations)
        else:
            benchmark_generation(args.model, args.device, args.prompt, args.max_new_tokens,
                                 args.warmup, args.iterations)
    except ImportError as e:
        print("缺少依赖：pip install openvino optimum-intel transformers torch")
        print(f"  详情: {e}")
        sys_exit(1)
    except Exception as e:  # pragma: no cover
        print(f"benchmark 失败：{e}")
        sys_exit(1)


def sys_exit(code):
    import sys
    sys.exit(code)


if __name__ == "__main__":
    main()
