import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from .config import load_env_file


DEFAULT_LLM_MODEL = "gpt-4.1-mini"


def llm_model_name(model: str | None = None) -> str:
    load_env_file()
    return model or os.environ.get("DEV_PARTS_LLM_MODEL") or DEFAULT_LLM_MODEL


def openai_llm_enabled() -> bool:
    load_env_file()
    return bool(os.environ.get("OPENAI_API_KEY"))


def parse_json_object(value: str) -> dict[str, Any]:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    timeout: int = 240,
) -> dict[str, Any]:
    load_env_file()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl executable was not found")

    payload_text = json.dumps(
        {
            "model": llm_model_name(model),
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": messages,
        },
        ensure_ascii=False,
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
        f.write(payload_text)
        payload_path = f.name
    try:
        result = subprocess.run(
            [
                curl,
                "--silent",
                "--show-error",
                "--fail-with-body",
                "--request",
                "POST",
                "https://api.openai.com/v1/chat/completions",
                "--header",
                f"Authorization: Bearer {api_key}",
                "--header",
                "Content-Type: application/json",
                "--data-binary",
                f"@{payload_path}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"OpenAI chat request failed: {message}")

    payload = json.loads(result.stdout)
    if "error" in payload:
        raise RuntimeError(f"OpenAI chat request failed: {payload['error']}")
    content = payload["choices"][0]["message"]["content"]
    return parse_json_object(content)
