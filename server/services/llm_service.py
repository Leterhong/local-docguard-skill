"""
LLM reasoning service with local/cloud hybrid support.

Default backend: OpenVINO-optimized local LLM (Qwen3 / Qwen2.5 <= 35B).
Fallback: HuggingFace transformers.
Optional cloud backend: any OpenAI-compatible chat endpoint
(e.g. DashScope, DeepSeek, OpenAI).

The service is intentionally resilient: if no model can be loaded and cloud
is disabled, `available` is False and the analysis engine falls back to
deterministic rule-based analysis. The LLM enriches summaries and explanations
but is NOT required for the product to function.

Switching providers:
  - UI / Agent sends `use_cloud=true`.
  - The engine calls `llm.set_provider("cloud")` for that request.
  - Cloud is rejected when `security.local_only=true` or cloud.enabled=false.
  - API keys are read from the environment variable configured in
    `providers.cloud.api_key_env` and are never logged.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from server.config import Settings
from server.services.security import get_logger, redact_text
from server.services.local_llm_bridge import LocalLLMBridge

logger = get_logger("llm")


class LLMService:
    def __init__(self, settings: Settings):
        self.settings = settings
        cfg = settings.model
        self.name: str = cfg.get("name", "Qwen3-8B")
        self.path: str = cfg.get("path", "")
        self.runtime: str = cfg.get("runtime", "openvino")
        self.device: str = cfg.get("device", "AUTO")
        self.max_new_tokens: int = int(cfg.get("max_new_tokens", 2048))
        self.temperature: float = float(cfg.get("temperature", 0.1))
        self.top_p: float = float(cfg.get("top_p", 0.9))
        self.context_window: int = int(cfg.get("context_window", 32768))

        self._pipe = None
        self._backend = None
        self._openvino_bridge = None
        self.available = False
        self.loaded_name = ""

        # Provider configuration (local/cloud hybrid) — MUST be set before
        # _load_model(), since backend selection reads providers.local.python.
        self._providers_cfg = settings.raw_config.get("providers", {})
        self._security = settings.raw_config.get("security", {})
        self._current_provider = "local"
        self._cloud_available = self._check_cloud_config()

        self._load_model()

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """回收本地 OpenVINO 子进程（服务退出时调用，防止数 GB 孤儿进程残留）。"""
        bridge = self._openvino_bridge
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------
    def list_providers(self) -> List[Dict[str, Any]]:
        """Return the list of configured providers and availability."""
        local_cfg = self._providers_cfg.get("local", {})
        cloud_cfg = self._providers_cfg.get("cloud", {})
        local_only = self._security.get("local_only", True)
        return [
            {
                "id": "local",
                "name": local_cfg.get("display_name", "本地 OpenVINO"),
                "description": local_cfg.get("description", "本地大模型"),
                "enabled": local_cfg.get("enabled", True),
                "available": self.available,
                "active": self._current_provider == "local",
                "model": self.loaded_name or self.name,
                "device": self.device,
                "backend": self._backend,
            },
            {
                "id": "cloud",
                "name": cloud_cfg.get("display_name", "云端大模型"),
                "description": cloud_cfg.get("description", "OpenAI 兼容接口"),
                "enabled": cloud_cfg.get("enabled", False) and not local_only,
                "available": self._cloud_available and not local_only,
                "active": self._current_provider == "cloud",
                "model": cloud_cfg.get("model", ""),
                "endpoint": cloud_cfg.get("endpoint", ""),
                "data_scope": cloud_cfg.get("data_scope", "text_summary"),
            },
        ]

    def set_provider(self, provider_id: str) -> bool:
        """Switch active provider. Returns True if the switch succeeded."""
        provider_id = (provider_id or "local").lower()
        if provider_id == self._current_provider:
            return True

        local_only = self._security.get("local_only", True)
        if provider_id == "cloud":
            cloud_cfg = self._providers_cfg.get("cloud", {})
            if local_only:
                logger.warning("Cloud provider rejected: security.local_only=true")
                return False
            if not cloud_cfg.get("enabled", False):
                logger.warning("Cloud provider rejected: providers.cloud.enabled=false")
                return False
            if not self._cloud_available:
                logger.warning("Cloud provider rejected: missing API key or config")
                return False
            self._current_provider = "cloud"
            logger.info("Switched LLM provider to cloud (%s)", cloud_cfg.get("model", ""))
            return True

        if provider_id == "local":
            self._current_provider = "local"
            logger.info("Switched LLM provider to local")
            return True

        logger.warning("Unknown provider: %s", provider_id)
        return False

    def current_provider(self) -> str:
        return self._current_provider

    def _check_cloud_config(self) -> bool:
        cloud_cfg = self._providers_cfg.get("cloud", {})
        if not cloud_cfg.get("enabled", False):
            return False
        endpoint = cloud_cfg.get("endpoint", "").strip()
        model = cloud_cfg.get("model", "").strip()
        api_key_env = cloud_cfg.get("api_key_env", "DOCGUARD_CLOUD_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        return bool(endpoint and model and api_key)

    # ------------------------------------------------------------------
    # Local model loading
    # ------------------------------------------------------------------
    def _load_model(self) -> None:
        """Resolve candidate model dirs, then try backends in priority order.

        Priority: OpenVINO GenAI (subprocess, uses the prepared runtime) ->
        optimum-intel OpenVINO -> transformers. The first that succeeds wins.
        If none loads, the engine falls back to deterministic rule analysis.
        """
        candidates: List[Any] = []
        primary = self.settings.resolve_model_path(self.path) if self.path else None
        if primary:
            candidates.append(primary)
        fb = self.settings.model.get("fallback", {})
        fb_path = fb.get("path", "")
        if fb_path:
            fb_resolved = self.settings.resolve_model_path(fb_path)
            if fb_resolved not in candidates:
                candidates.append(fb_resolved)

        # 1) OpenVINO GenAI via the prepared runtime (real local INT4 inference)
        for c in candidates:
            if c and getattr(c, "exists", lambda: False)() and self._try_openvino_genai_subprocess(str(c)):
                return

        # 2) optimum-intel OpenVINO pipeline (needs optimum + transformers)
        for c in candidates:
            if c and getattr(c, "exists", lambda: False)() and self._try_openvino(c):
                return

        # 3) HuggingFace model id (will attempt download; not expected offline)
        if self._try_openvino(self.name):
            return
        if self._try_transformers(self.name):
            return

        logger.warning(
            "No local LLM could be loaded. Rule-based analysis will be used. "
            "Convert a model with scripts/convert_model.py or point providers.local.python "
            "to a Python that has openvino-genai installed."
        )
        self.available = False

    def _try_openvino_genai_subprocess(self, model_dir: str) -> bool:
        """Use the OpenVINO GenAI runtime prepared in another Python env.

        The host DocGuard venv may not have openvino-genai; instead we spawn a
        subprocess running openvino_gen_server.py under a Python that does, and
        communicate via a JSONL line protocol. This keeps the model 100% local.
        """
        local_cfg = self._providers_cfg.get("local", {})
        # Env var overrides the shipped config so operators can point to a
        # different openvino-genai runtime without editing model_config.yaml.
        # Falls back to the configured python when the env var is unset/empty.
        env_python = os.environ.get("DOCGUARD_OPENVINO_PYTHON", "").strip()
        cfg_python = local_cfg.get("python", "").strip()
        python_exe = env_python or cfg_python
        if not python_exe or not os.path.isfile(python_exe):
            logger.info("OpenVINO GenAI backend skipped: providers.local.python not set/invalid")
            return False
        if not os.path.isdir(model_dir):
            logger.warning("OpenVINO GenAI backend skipped: model dir missing %s", model_dir)
            return False
        # Validate the runtime can import openvino_genai (no weight load yet)
        try:
            probe = subprocess.run(
                [python_exe, "-c", "import openvino_genai, sys; print(openvino_genai.__version__)"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if probe.returncode != 0:
                logger.warning("openvino_genai import failed in %s: %s", python_exe, probe.stderr.strip())
                return False
            ov_ver = probe.stdout.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("python probe failed: %s", exc)
            return False

        server_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "openvino_gen_server.py"
        )
        self._openvino_bridge = LocalLLMBridge(
            python_exe=python_exe,
            server_script=server_script,
            model_dir=model_dir,
            device=self.device,
        )
        self._backend = "openvino-genai"
        self.available = True
        self.loaded_name = os.path.basename(model_dir)
        logger.info(
            "Local LLM backend = openvino-genai (%s) | runtime %s | model %s",
            python_exe, ov_ver, model_dir,
        )
        return True

    def _try_openvino(self, source: Any) -> bool:
        """Try loading via optimum-intel OpenVINO pipeline."""
        try:
            from optimum.intel.openvino import OVModelForCausalLM  # type: ignore
            from transformers import AutoTokenizer

            source_str = str(source)
            logger.info("Loading LLM via OpenVINO: %s (device=%s)", source_str, self.device)
            tokenizer = AutoTokenizer.from_pretrained(source_str, trust_remote_code=True)
            model = OVModelForCausalLM.from_pretrained(
                source_str,
                device=self.device,
                trust_remote_code=True,
            )
            from transformers import pipeline as hf_pipeline

            self._pipe = hf_pipeline(
                "text-generation",
                model=model,
                tokenizer=tokenizer,
                max_new_tokens=self.max_new_tokens,
                temperature=max(self.temperature, 1e-3),
                top_p=self.top_p,
                do_sample=self.temperature > 0,
                repetition_penalty=1.05,
                device=-1,  # OV handles device itself
            )
            self._backend = "openvino"
            self.available = True
            self.loaded_name = source_str
            logger.info("LLM loaded via OpenVINO: %s", source_str)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenVINO load failed for %s: %s", source, exc)
            return False

    def _try_transformers(self, model_id: str) -> bool:
        try:
            from transformers import pipeline as hf_pipeline  # type: ignore

            logger.info("Loading LLM via transformers: %s", model_id)
            self._pipe = hf_pipeline(
                "text-generation",
                model=model_id,
                max_new_tokens=self.max_new_tokens,
                temperature=max(self.temperature, 1e-3),
                top_p=self.top_p,
                do_sample=self.temperature > 0,
                repetition_penalty=1.05,
                trust_remote_code=True,
            )
            self._backend = "transformers"
            self.available = True
            self.loaded_name = model_id
            logger.info("LLM loaded via transformers: %s", model_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Transformers load failed for %s: %s", model_id, exc)
            return False

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
    ) -> str:
        if self._current_provider == "cloud":
            return self._generate_cloud(prompt, system=system, max_new_tokens=max_new_tokens)
        return self._generate_local(prompt, system=system, max_new_tokens=max_new_tokens)

    def _generate_local(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
    ) -> str:
        if not self.available:
            return ""
        # Route real local OpenVINO GenAI inference through the subprocess bridge.
        if self._backend == "openvino-genai" and self._openvino_bridge is not None:
            text = self._openvino_bridge.generate(
                user=prompt,
                system=system,
                max_new_tokens=int(max_new_tokens or self.max_new_tokens),
            )
            return text.strip() if text else ""
        if self._pipe is None:
            return ""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Apply chat template when available (Qwen models use ChatML).
        tokenizer = getattr(self._pipe, "tokenizer", None)
        try:
            if tokenizer is not None and hasattr(tokenizer, "apply_chat_template"):
                input_text = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                input_text = self._fallback_format(messages)
        except Exception:  # noqa: BLE001
            input_text = self._fallback_format(messages)

        try:
            output = self._pipe(
                input_text,
                max_new_tokens=max_new_tokens or self.max_new_tokens,
                return_full_text=False,
            )
            text = output[0]["generated_text"] if isinstance(output, list) else str(output)
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM generation failed: %s", redact_text(str(exc)))
            return ""

    def _generate_cloud(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
    ) -> str:
        """Call an OpenAI-compatible chat endpoint."""
        cloud_cfg = self._providers_cfg.get("cloud", {})
        endpoint = cloud_cfg.get("endpoint", "").rstrip("/")
        model = cloud_cfg.get("model", "")
        api_key_env = cloud_cfg.get("api_key_env", "DOCGUARD_CLOUD_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        timeout = int(cloud_cfg.get("timeout", 60))
        max_tokens = int(max_new_tokens or cloud_cfg.get("max_tokens", 2048))
        temperature = float(cloud_cfg.get("temperature", 0.1))
        top_p = float(cloud_cfg.get("top_p", 0.9))

        if not endpoint or not model or not api_key:
            logger.error("Cloud provider not configured correctly")
            return ""

        try:
            import requests
        except ImportError as exc:
            logger.error("Cloud provider requires 'requests': %s", exc)
            return ""

        # 数据安全：向云端发送前对文本做 PII 脱敏（身份证/手机号/银行卡/邮箱等），
        # 原始文件字节永不出机，仅脱敏后的文本摘要/片段外发。
        prompt = redact_text(prompt)
        if system:
            system = redact_text(system)

        url = f"{endpoint}/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            logger.info("Calling cloud LLM: %s model=%s", endpoint, model)
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices and "message" in choices[0]:
                return choices[0]["message"].get("content", "").strip()
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.error("Cloud LLM call failed: %s", redact_text(str(exc)))
            return ""

    @staticmethod
    def _fallback_format(messages: List[Dict[str, str]]) -> str:
        parts = []
        for m in messages:
            role = m["role"]
            if role == "system":
                parts.append(f"<|im_start|>system\n{m['content']}<|im_end|>")
            elif role == "user":
                parts.append(f"<|im_start|>user\n{m['content']}<|im_end|>")
            parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Structured helpers
    # ------------------------------------------------------------------
    def generate_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
    ) -> Optional[Any]:
        """Generate and parse a JSON object/array. Returns None on failure."""
        text = self.generate(prompt, system=system, max_new_tokens=max_new_tokens)
        if not text:
            return None
        return self._extract_json(text)

    @staticmethod
    def _extract_json(text: str) -> Optional[Any]:
        # Strip <|im_end|> style tags and code fences.
        text = re.sub(r"<\|im_end\|>", "", text)
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        # Find first {...} or [...].
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end != -1 and end > start:
                candidate = text[start : end + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
        return None

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------
    def info(self) -> Dict[str, Any]:
        return {
            "available": self.available or self._current_provider == "cloud",
            "loaded_name": self.loaded_name,
            "configured_name": self.name,
            "backend": self._backend if self._current_provider == "local" else "cloud",
            "device": self.device if self._current_provider == "local" else "cloud",
            "runtime": self.runtime if self._current_provider == "local" else "openai-compatible",
            "provider": self._current_provider,
            "cloud_available": self._cloud_available,
            "cloud_enabled": self._providers_cfg.get("cloud", {}).get("enabled", False),
            "local_only": self._security.get("local_only", True),
        }
