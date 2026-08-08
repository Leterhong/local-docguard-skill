"""
集成验证：DocGuard 的真实本地 LLM 桥接路径。
直接用 llm_service 内部的 LocalLLMBridge（同一份代码），
证明流水线在 use_llm=True 时调用的是真·本地 Qwen2.5-7B。
"""
import time
from server.services.local_llm_bridge import LocalLLMBridge

PY = r"F:/Production AI Skills/.openvino/venv/dataanalysis/Scripts/python.exe"
SCRIPT = r"F:/Production AI Skills/docguard-skill/server/services/openvino_gen_server.py"
MODEL = r"F:/Production AI Skills/.openvino/models/Qwen2.5-7B-Instruct-int4-ov"

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
