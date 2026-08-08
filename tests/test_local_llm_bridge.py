"""
集成验证：DocGuard 的真实本地 LLM 桥接路径。
直接用 llm_service 内部的 LocalLLMBridge（同一份代码），
证明流水线在 use_llm=True 时调用的是真·本地大模型。

注意：需在已安装 openvino-genai 的 Python 环境中运行。
- PY：OpenVINO 推理解释器，优先读环境变量 DOCGUARD_OPENVINO_PYTHON，
      其次读 model_config.yaml 的 providers.local.python，最后回退到相对默认路径。
- SCRIPT：始终为本 skill 内的 openvino_gen_server.py（相对路径，跨机器可用）。
- MODEL：同 test_local_llm_openvino.py 的解析逻辑（环境变量 / model_config.yaml / 相对默认）。
"""
import os
import time
from pathlib import Path

from server.services.local_llm_bridge import LocalLLMBridge

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = str(ROOT / "server" / "services" / "openvino_gen_server.py")


def _resolve_python() -> str:
    env = os.environ.get("DOCGUARD_OPENVINO_PYTHON")
    if env:
        return env
    cfg_path = ROOT / "model_config.yaml"
    if cfg_path.exists():
        in_local = False
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("local:"):
                in_local = True
                continue
            if in_local:
                if s.startswith("python:"):
                    p = s.split(":", 1)[1].strip().strip('"').strip("'")
                    return p  # 绝对路径，用户自行配置
                if s and not s.startswith("#") and not s.startswith(" ") and ":" in s:
                    in_local = False
    return str(ROOT / ".openvino" / "venv" / "dataanalysis" / "Scripts" / "python.exe")


def _resolve_model_path() -> str:
    env = os.environ.get("DOCGUARD_LLM_MODEL")
    if env:
        return env
    cfg_path = ROOT / "model_config.yaml"
    if cfg_path.exists():
        in_model = False
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("model:"):
                in_model = True
                continue
            if in_model:
                if s.startswith("path:"):
                    p = s.split(":", 1)[1].strip().strip('"').strip("'")
                    return str(ROOT / p) if not os.path.isabs(p) else p
                if s and not s.startswith("#") and not s.startswith(" ") and ":" in s and not s.startswith("model"):
                    in_model = False
    return str(ROOT / ".openvino" / "models" / "Qwen2.5-7B-Instruct-int4-ov")


PY = _resolve_python()
MODEL = _resolve_model_path()

print("=" * 64)
print("DocGuard 本地 LLM 桥接集成测试 (openvino-genai 子进程)")
print("=" * 64)
t0 = time.time()
bridge = LocalLLMBridge(PY, SCRIPT, MODEL, device="CPU")
print("[启动] ensure_started() 中（首次会加载 4.4GB 权重）...")

# 第一次生成触发惰性启动 + 加载
system = "你是企业合同风控助手，输出专业、可执行。"
user = "请判断以下条款的风险并指出依据：'甲方应在项目验收合格后90日内支付全部款项。'"

ok = bridge.ensure_started()
print("ensure_started ->", ok, "| 启动+就绪耗时 %.1fs" % (time.time() - t0))

t1 = time.time()
out = bridge.generate(user=user, system=system, max_new_tokens=160)
print("-" * 64)
print("本地模型真实输出：")
print(out)
print("-" * 64)
print("生成耗时 %.1fs | 字符数 %d" % (time.time() - t1, len(out)))
print("BRIDGE_TEST_OK" if out else "BRIDGE_TEST_EMPTY")
