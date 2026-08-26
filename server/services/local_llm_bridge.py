"""
本地大模型子进程桥接管理器。

DocGuard 主服务（运行在自身 venv，可能未安装 openvino-genai）通过本桥接
惰性拉起一个「已装好 openvino-genai 的 Python 子进程」(openvino_gen_server.py)，
并以行协议请求本地推理。这样既复用了宿主准备好的 OpenVINO 运行时，
又保证了「模型纯本地、文档不出机」的可验证性。

用法：
    bridge = LocalLLMBridge(python_exe, server_script, model_dir, device="CPU")
    text = bridge.generate(user="...", system="...", max_new_tokens=256)

生命周期说明：
    - stderr 由后台守护线程持续排空，防止管道写满导致子进程阻塞；
    - 生成超时后主动回收子进程，下次调用重新拉起，避免「响应串位」
      （上一个请求的陈旧响应被下一个请求读到）；
    - 服务退出时必须调用 stop() 回收子进程，否则会残留数 GB 的孤儿进程。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from typing import Optional


class LocalLLMBridge:
    def __init__(
        self,
        python_exe: str,
        server_script: str,
        model_dir: str,
        device: str = "CPU",
        start_timeout: int = 180,
        gen_timeout: int = 300,
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
        self._stderr_tail = deque(maxlen=20)
        self._stderr_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def _drain_stderr(self) -> None:
        """后台线程：持续消费子进程 stderr，防止管道缓冲写满后子进程写阻塞。"""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, ""):
                line = line.strip()
                if line:
                    self._stderr_tail.append(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _kill_locked(self) -> None:
        """终止并回收子进程。调用方必须已持有 self._lock。"""
        proc = self._proc
        self._proc = None
        self._started = False
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

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
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                start = time.time()
                banner = ""
                while time.time() - start < self.start_timeout:
                    if self._proc.poll() is not None:
                        self.last_error = (self._proc.stderr.read() or "")[:500]
                        self._kill_locked()
                        return False
                    line = self._proc.stdout.readline()
                    if line.startswith("READY"):
                        self._started = True
                        self._stderr_thread = threading.Thread(
                            target=self._drain_stderr,
                            daemon=True,
                            name="dg-llm-stderr",
                        )
                        self._stderr_thread.start()
                        return True
                    if line:
                        banner = line.strip()
                self.last_error = "timeout waiting READY (last=%s)" % banner
                # 启动超时同样要回收，否则残留半初始化的孤儿进程
                self._kill_locked()
                return False
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self._kill_locked()
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
                        # 子进程中途死亡：重置状态，下次调用重新拉起
                        self.last_error = "llm subprocess exited unexpectedly"
                        self._kill_locked()
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
                # 生成超时：回收子进程，防止陈旧响应串位到下一个请求
                self.last_error = "generation timeout (subprocess recycled)"
                self._kill_locked()
                return ""
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self._kill_locked()
                return ""

    # ------------------------------------------------------------------
    def stderr_tail(self, n: int = 5) -> str:
        """返回最近 n 条子进程 stderr 输出，用于诊断。"""
        return " / ".join(list(self._stderr_tail)[-n:])

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """回收子进程（服务退出时必须调用，否则残留数 GB 孤儿进程）。"""
        with self._lock:
            self._kill_locked()
        t = self._stderr_thread
        if t is not None and t.is_alive():
            t.join(timeout=2)
        self._stderr_thread = None
