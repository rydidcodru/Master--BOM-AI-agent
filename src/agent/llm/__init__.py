"""로컬 LLM 어댑터 (Ollama, env 게이트). L1 의미 슬롯 / L4 설명 전용.

외부 API/키 금지. L3 영향도 판정에는 절대 사용하지 않는다(결정론).
"""

from src.agent.llm.client import LlmClient, OllamaClient, default_llm, llm_enabled

__all__ = ["LlmClient", "OllamaClient", "default_llm", "llm_enabled"]
