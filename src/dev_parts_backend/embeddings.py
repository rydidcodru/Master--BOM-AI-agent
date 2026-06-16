import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from .config import load_env_file
from .loader import text


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

QUERY_EMBEDDING_FIELDS = [
    "changing_reason_embedding",
    "changing_point_embedding",
    "part_name_embedding",
    "combined_embedding",
]


def embedding_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def embedding_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(text(item) for item in value if text(item))
    return text(value)


def change_embedding_texts(change: dict[str, Any]) -> dict[str, str]:
    changing_reason = text(change.get("change_reason") or change.get("changing_reason"))
    changing_point = text(change.get("change_point") or change.get("changing_point"))
    description = text(change.get("description"))
    change_type = text(change.get("change_type"))
    target_value = text(change.get("target_value"))
    intent_keywords = embedding_text(change.get("change_intent_keywords"))
    module_name = text(change.get("module_name"))
    part_name = text(change.get("part_name")) or module_name
    combined_parts = []
    if description:
        combined_parts.append(f"설명: {description}")
    if module_name:
        combined_parts.append(f"모듈명: {module_name}")
    if changing_reason:
        combined_parts.append(f"변경내역: {changing_reason}")
    if changing_point:
        combined_parts.append(f"변경점: {changing_point}")
    if change_type:
        combined_parts.append(f"변경 유형: {change_type}")
    if target_value:
        combined_parts.append(f"목표 값: {target_value}")
    if intent_keywords:
        combined_parts.append(f"변경 의도 키워드: {intent_keywords}")
    if part_name:
        combined_parts.append(f"부품명: {part_name}")
    return {
        "changing_reason_embedding": changing_reason,
        "changing_point_embedding": changing_point,
        "part_name_embedding": part_name,
        "combined_embedding": "\n".join(combined_parts),
    }


def openai_embedding_enabled() -> bool:
    load_env_file()
    return bool(os.environ.get("OPENAI_API_KEY"))


def embed_texts(texts: list[str], *, model: str = DEFAULT_EMBEDDING_MODEL) -> list[list[float]]:
    load_env_file()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl executable was not found")

    payload_text = json.dumps({"model": model, "input": texts}, ensure_ascii=False)
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
                "https://api.openai.com/v1/embeddings",
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
            timeout=180,
            check=False,
        )
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"OpenAI embeddings request failed: {message}")

    payload = json.loads(result.stdout)
    if "error" in payload:
        raise RuntimeError(f"OpenAI embeddings request failed: {payload['error']}")
    data = sorted(payload["data"], key=lambda item: item["index"])
    return [item["embedding"] for item in data]


def enrich_changes_with_embeddings(
    changes: list[dict[str, Any]],
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    enabled: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    enriched = [dict(change) for change in changes]
    if not enabled:
        return enriched, {"status": "disabled", "model": model, "embedded_fields": 0}
    if not openai_embedding_enabled():
        return enriched, {"status": "skipped_no_openai_api_key", "model": model, "embedded_fields": 0}

    jobs: list[tuple[int, str, str]] = []
    for index, change in enumerate(enriched):
        for field, source in change_embedding_texts(change).items():
            if source and not change.get(field):
                jobs.append((index, field, source))

    if not jobs:
        return enriched, {"status": "no_embedding_jobs", "model": model, "embedded_fields": 0}

    try:
        vectors = embed_texts([source for _index, _field, source in jobs], model=model)
    except Exception as exc:
        return enriched, {
            "status": "failed",
            "model": model,
            "embedded_fields": 0,
            "error": str(exc),
        }
    for (index, field, _source), vector in zip(jobs, vectors):
        enriched[index][field] = vector

    return enriched, {"status": "embedded", "model": model, "embedded_fields": len(jobs)}
