"""환경변수 로드 및 Neo4j/OpenAI 클라이언트 설정 관리."""
import os
from pathlib import Path
from dotenv import load_dotenv

# agent/ 디렉토리의 .env 파일을 최우선적으로 로드
AGENT_DIR = Path(__file__).parent
load_dotenv(AGENT_DIR / ".env")

# Neo4j 설정
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "lgbom-poc-2026")
NEO4J_DB = os.getenv("NEO4J_DB", "neo4j")

# LLM 설정
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure")  # 'azure', 'openai', 'ollama', 'gemini'

# Standard OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Azure OpenAI
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_DEPLOYMENT_GPT_4O_MINI = os.getenv("AZURE_DEPLOYMENT_GPT_4O_MINI", "gpt-4o-mini")

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "llama3.1:8b")

# Gemini (Google AI Studio OpenAI-compatible API)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")


def get_neo4j_driver():
    """Neo4j 드라이버 인스턴스 생성. 드라이버는 생성 후 사용을 마치고 close 해야 합니다."""
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def get_llm_client():
    """LLM 프로바이더별 클라이언트 인스턴스 및 호출 설정 반환."""
    if LLM_PROVIDER == "azure":
        from openai import AzureOpenAI
        if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
            raise ValueError("Azure OpenAI 환경변수가 설정되지 않았습니다 (AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT)")
        client = AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
        )
        return client, {"model": AZURE_DEPLOYMENT_GPT_4O_MINI}
    elif LLM_PROVIDER == "openai":
        from openai import OpenAI
        if not OPENAI_API_KEY:
            raise ValueError("OpenAI 환경변수가 설정되지 않았습니다 (OPENAI_API_KEY)")
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client, {"model": OPENAI_MODEL}
    elif LLM_PROVIDER == "ollama":
        from openai import OpenAI
        # Ollama는 OpenAI 호환 API 엔드포인트를 제공함
        client = OpenAI(
            base_url=f"{OLLAMA_BASE_URL.rstrip('/')}/v1",
            api_key="ollama",  # 더미 키
        )
        return client, {"model": OLLAMA_LLM_MODEL}
    elif LLM_PROVIDER == "gemini":
        from openai import OpenAI
        if not GEMINI_API_KEY:
            raise ValueError("Gemini API 키가 설정되지 않았습니다 (GEMINI_API_KEY)")
        client = OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        return client, {"model": GEMINI_MODEL}
    else:
        raise ValueError(f"지원하지 않는 LLM_PROVIDER입니다: {LLM_PROVIDER}")
