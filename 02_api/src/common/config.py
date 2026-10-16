"""환경 설정. 단계 폴더 또는 프로젝트 루트의 .env를 읽는다."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logging.getLogger("httpx").setLevel(logging.WARNING)

STAGE_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = STAGE_DIR.parent
DATA_DIR = STAGE_DIR / "data"

for env_file in (STAGE_DIR / ".env", ROOT_DIR / ".env"):
    if env_file.exists():
        load_dotenv(env_file, override=False)

DEV_URL = os.getenv("DEV_URL", "http://127.0.0.1:8201")
QA_URL = os.getenv("QA_URL", "http://127.0.0.1:8202")
HUB_PORT = int(os.getenv("HUB_PORT", "8200"))
PORTAL_PORT = HUB_PORT  # 이전 이름 호환
DEV_PORT = int(os.getenv("DEV_PORT", "8201"))
QA_PORT = int(os.getenv("QA_PORT", "8202"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
STEP_DELAY = float(os.getenv("STEP_DELAY", "0.4"))
TEST_DELAY = float(os.getenv("TEST_DELAY", "0.1"))


def llm_mode() -> str:
    requested = os.getenv("LLM_MODE", "auto").lower()
    if requested == "mock" or (requested == "auto" and not os.getenv("ANTHROPIC_API_KEY")):
        return "mock"
    return "llm"


def db_path(name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{name}.db"
