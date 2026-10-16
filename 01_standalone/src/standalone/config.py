"""환경 설정. 단계 폴더 또는 프로젝트 루트의 .env를 읽는다."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

STAGE_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = STAGE_DIR.parent

for env_file in (STAGE_DIR / ".env", ROOT_DIR / ".env"):
    if env_file.exists():
        load_dotenv(env_file, override=False)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    llm_mode: str  # "llm" | "mock"
    model: str
    step_delay: float
    test_delay: float


def load_settings() -> Settings:
    requested = os.getenv("LLM_MODE", "auto").lower()
    has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    if requested == "mock" or (requested == "auto" and not has_key):
        mode = "mock"
    else:
        mode = "llm"
    data_dir = STAGE_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        db_path=data_dir / "app.db",
        llm_mode=mode,
        model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
        step_delay=float(os.getenv("STEP_DELAY", "0.4")),
        test_delay=float(os.getenv("TEST_DELAY", "0.1")),
    )
