#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DocGuard AI 最小 HTTP 客户端示例。

这是一个独立的开发者演示脚本，展示如何直接用 requests 调用 DocGuard
本地 HTTP 服务。它不属于生产力级 Agent 的 Tool 入口——真实场景下，
Qoder / WorkBuddy / TRAE Work 通过 tools/ 目录下的 Tool 脚本调用服务。

运行前请确保服务已启动：
    python -m server.main
服务默认监听 127.0.0.1:8765（可在 model_config.yaml 中修改 server.port）。
"""

import requests

BASE = "http://127.0.0.1:8765"  # 与 model_config.yaml 中 server.port 保持一致


def main():
    # 1. 上传示例文档
    with open("examples/contract_sample.txt", "rb") as f:
        up = requests.post(f"{BASE}/api/upload", files={"file": f}).json()
    path = up["data"]["file_path"]

    # 2. 请求本地分析（启用本地 LLM 增强，原始文件字节不出机）
    r = requests.post(
        f"{BASE}/api/analyze",
        json={"file_path": path, "use_llm": True, "use_cloud": False},
    ).json()
    d = r["data"]

    # 3. 打印关键结果
    print("doc_type=", d["summary"]["doc_type"])
    print("pages=", d["page_count"], "chunks=", d["chunk_count"], "chars=", d["char_count"])
    print("overall_risk_level=", d["overall_risk_level"])
    print("risk_count_by_level=", d["risk_count_by_level"])
    print("llm_used=", d["llm_used"])
    for risk in d.get("risks", []):
        print(f" - {risk['id']} {risk['level']} | {risk['category']} | {risk['detail']}")


if __name__ == "__main__":
    main()
