"""환경 설정. 단계 폴더 또는 프로젝트 루트의 .env를 읽는다."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

for _name in ("httpx", "httpx2", "a2a.client.card_resolver"):
    logging.getLogger(_name).setLevel(logging.WARNING)

STAGE_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = STAGE_DIR.parent
DATA_DIR = STAGE_DIR / "data"

for env_file in (STAGE_DIR / ".env", ROOT_DIR / ".env"):
    if env_file.exists():
        load_dotenv(env_file, override=False)

HUB_PORT = int(os.getenv("HUB_PORT", "8400"))
DEV_PORT = int(os.getenv("DEV_PORT", "8401"))
QA_PORT = int(os.getenv("QA_PORT", "8402"))
HUB_URL = os.getenv("HUB_URL", f"http://127.0.0.1:{HUB_PORT}")
DEV_URL = os.getenv("DEV_URL", f"http://127.0.0.1:{DEV_PORT}")
QA_URL = os.getenv("QA_URL", f"http://127.0.0.1:{QA_PORT}")
# Hub가 Agent Card를 수집할 에이전트 주소 목록. Hub는 이 주소 외에 상대에 대해 아무것도 모른다.
AGENT_URLS = [u.strip() for u in os.getenv("AGENT_URLS", f"{DEV_URL},{QA_URL}").split(",") if u.strip()]
MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
STEP_DELAY = float(os.getenv("STEP_DELAY", "0.4"))
TEST_DELAY = float(os.getenv("TEST_DELAY", "0.1"))
MAX_ITERATIONS = 5


def llm_mode() -> str:
    requested = os.getenv("LLM_MODE", "auto").lower()
    if requested == "mock" or (requested == "auto" and not os.getenv("ANTHROPIC_API_KEY")):
        return "mock"
    return "llm"


def db_path(name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{name}.db"
