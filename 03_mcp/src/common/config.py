"""환경 설정. 단계 폴더 또는 프로젝트 루트의 .env를 읽는다."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

for _name in ("httpx", "httpx2", "mcp.server.streamable_http_manager", "mcp.server.mcpserver.server"):
    logging.getLogger(_name).setLevel(logging.WARNING)

STAGE_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = STAGE_DIR.parent
DATA_DIR = STAGE_DIR / "data"

for env_file in (STAGE_DIR / ".env", ROOT_DIR / ".env"):
    if env_file.exists():
        load_dotenv(env_file, override=False)

HOST_PORT = int(os.getenv("HOST_PORT", "8300"))
DEV_PORT = int(os.getenv("DEV_PORT", "8301"))
QA_PORT = int(os.getenv("QA_PORT", "8302"))
DEV_URL = os.getenv("DEV_URL", f"http://127.0.0.1:{DEV_PORT}")
QA_URL = os.getenv("QA_URL", f"http://127.0.0.1:{QA_PORT}")
# Host가 접속할 MCP 서버 목록 (이름=URL). 도구는 이 서버들에서 자동 발견한다.
MCP_SERVERS = {"dev": f"{DEV_URL}/mcp", "qa": f"{QA_URL}/mcp"}
MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
STEP_DELAY = float(os.getenv("STEP_DELAY", "0.4"))
TEST_DELAY = float(os.getenv("TEST_DELAY", "0.1"))
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", "80"))


def llm_mode() -> str:
    requested = os.getenv("LLM_MODE", "auto").lower()
    if requested == "mock" or (requested == "auto" and not os.getenv("ANTHROPIC_API_KEY")):
        return "mock"
    return "llm"


def db_path(name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{name}.db"
