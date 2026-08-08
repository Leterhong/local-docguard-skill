"""
真实本地大模型推理测试（OpenVINO GenAI）。
目的：证明 DocGuard AI 在纯本地（CPU / OpenVINO INT4）条件下，
能够真正加载 Qwen2.5-7B-Instruct-int4-ov 并完成推理，而非降级绕过大模型。

运行环境：F:/Production AI Skills/.openvino/venv/dataanalysis/Scripts/python.exe
模型路径：F:/Production AI Skills/.openvino/models/Qwen2.5-7B-Instruct-int4-ov
"""
import time
import openvino_genai as ov_genai

MODEL = r"F:/Production AI Skills/.openvino/models/Qwen2.5-7B-Instruct-int4-ov"

print("=" * 64)
print("REAL LOCAL LLM TEST  |  openvino_genai =", ov_genai.__version__)
print("MODEL =", MODEL)
print("=" * 64)

t0 = time.time()
print("[1/3] 加载模型 (device=CPU, INT4 OpenVINO IR) ...")
pipe = ov_genai.LLMPipeline(MODEL, device="CPU")
print("      模型加载完成，耗时 %.1f s" % (time.time() - t0))

# 用 Qwen2.5 的 chat 模板构造一个「合同风险分析」真实业务提示
tok = pipe.get_tokenizer()
messages = [
    {"role": "system", "content": "你是企业合同风控助手，回答简洁、专业、可执行。"},
    {"role": "user", "content": "在采购合同中，'付款周期超过 60 天'这一条款可能带来哪些风险？请列举 3 条。"},
]
prompt = tok.apply_chat_template(messages, add_generation_prompt=True)
print("[2/3] 输入提示（已套用 Qwen2.5 chat 模板）:")
print("      ", prompt[:120].replace("\n", "\\n"), "...")
print("[3/3] 本地生成中 (max_new_tokens=160) ...")

cfg = ov_genai.GenerationConfig()
cfg.max_new_tokens = 160
cfg.do_sample = False

t1 = time.time()
result = pipe.generate(prompt, config=cfg)
gen_time = time.time() - t1

print("-" * 64)
print("模型输出（本地推理，无任何云端调用）:")
print(result)
print("-" * 64)
print("首字+生成总耗时: %.1f s" % gen_time)
print("输出字符数: %d" % len(result))
print("TEST_OK")
