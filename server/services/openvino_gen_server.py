"""
OpenVINO GenAI 本地大模型子进程服务。

该脚本必须运行在已安装 `openvino-genai` 的 Python 环境中
（由 model_config.yaml 的 providers.local.python 指定，可用环境变量 DOCGUARD_OPENVINO_PYTHON 覆盖）。
它通过 stdin/stdout 以 JSONL 行协议与 DocGuard 主服务通信：

  请求: {"system": str, "user": str, "max_new_tokens": int}
  响应: {"ok": true, "text": str} | {"ok": false, "error": str}

模型只在启动时加载一次（约 5s，CPU），后续请求复用，避免重复加载 4.4GB 权重。
这是「纯本地、文档不出机」约束下的真实本地推理后端。
"""
from __future__ import annotations

import json
import os
import sys

import openvino_genai as ov_genai


def main() -> None:
    model_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OV_MODEL_DIR", "")
    device = os.environ.get("OV_DEVICE", "CPU")

    if not model_dir or not os.path.isdir(model_dir):
        sys.stderr.write("MODEL_DIR_INVALID:%s\n" % model_dir)
        sys.stderr.flush()
        return

    # 真实加载本地 INT4 OpenVINO IR 模型
    pipe = ov_genai.LLMPipeline(model_dir, device=device)
    tok = pipe.get_tokenizer()

    sys.stdout.write("READY\n")
    sys.stdout.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception:
            sys.stdout.write(json.dumps({"ok": False, "error": "bad json"}) + "\n")
            sys.stdout.flush()
            continue
        try:
            messages = []
            sys_p = req.get("system")
            if sys_p:
                messages.append({"role": "system", "content": sys_p})
            messages.append({"role": "user", "content": req.get("user", "")})
            # 套用 Qwen2.5 ChatML 模板（openvino_genai 的接口固定返回字符串）
            prompt = tok.apply_chat_template(messages, add_generation_prompt=True)

            cfg = ov_genai.GenerationConfig()
            cfg.max_new_tokens = int(req.get("max_new_tokens", 256))
            cfg.do_sample = False  # 法律审查追求确定性

            text = pipe.generate(prompt, config=cfg)
            sys.stdout.write(json.dumps({"ok": True, "text": text}) + "\n")
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
