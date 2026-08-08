"""
本地大模型子进程桥接管理器。

DocGuard 主服务（运行在自身 venv，可能未安装 openvino-genai）通过本桥接
惰性拉起一个「已装好 openvino-genai 的 Python 子进程」(openvino_gen_server.py)，
并以行协议请求本地推理。这样既复用了宿主准备好的 OpenVINO 运行时，
又保证了「模型纯本地、文档不出机」的可验证性。

用法：
    bridge = LocalLLMBridge(python_exe, server_script, model_dir, device="CPU")
    text = bridge.generate(user="...", system="...", max_new_tokens=256)
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Optional


class LocalLLMBridge:
    def __init__(
        self,
        python_exe: str,
        server_script: str,
        model_dir: str,
        device: str = "CPU",
        start_timeout: int = 120,
        gen_timeout: int = 180,
    ):
        self.python_exe = python_exe
        self.server_script = server_script
        self.model_dir = model_dir
        self.device = device
        self.start_timeout = start_timeout
        self.gen_timeout = gen_timeout
        self._proc = None
        self._lock = threading.Lock()
        self._started = False
        self.last_error = ""

    # ------------------------------------------------------------------
    def ensure_started(self) -> bool:
        """惰性启动子进程并等待 READY。已启动则直接返回。"""
        if self._proc is not None and self._proc.poll() is None:
            return True
        with self._lock:
            # 二次检查（避免竞态）
            if self._proc is not None and self._proc.poll() is None:
                return True
            try:
                env = os.environ.copy()
                env["OV_DEVICE"] = self.device
                self._proc = subprocess.Popen(
                    [self.python_exe, self.server_script, self.model_dir],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=True,
                    bufsize=1,
                )
                start = time.time()
                banner = ""
                while time.time() - start < self.start_timeout:
                    if self._proc.poll() is not None:
                        self.last_error = (self._proc.stderr.read() or "")[:500]
                        return False
                    line = self._proc.stdout.readline()
                    if line.startswith("READY"):
                        self._started = True
                        return True
                    if line:
                        banner = line.strip()
                self.last_error = "timeout waiting READY (last=%s)" % banner
                return False
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                return False

    # ------------------------------------------------------------------
    def generate(
        self,
        user: str,
        system: Optional[str] = None,
        max_new_tokens: int = 256,
    ) -> str:
        if not self.ensure_started():
            return ""
        req = json.dumps(
            {"user": user, "system": system, "max_new_tokens": int(max_new_tokens)}
        )
        with self._lock:
            try:
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
                start = time.time()
                while time.time() - start < self.gen_timeout:
                    if self._proc.poll() is not None:
                        return ""
                    line = self._proc.stdout.readline()
                    if not line:
                        continue
                    try:
                        resp = json.loads(line)
                    except Exception:
                        continue
                    if "ok" in resp:
                        if resp["ok"]:
                            return resp.get("text", "")
                        self.last_error = resp.get("error", "unknown")
                        return ""
                self.last_error = "generation timeout"
                return ""
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                return ""

    # ------------------------------------------------------------------
    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
                self._started = False
