"""Stage 1 - Standalone 진입점.

    uv run run.py            # http://localhost:8100
    uv run run.py --mock     # LLM 없이 실행
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1 - Standalone")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--mock", action="store_true", help="LLM 대신 규칙 기반(mock) 모드로 실행")
    args = parser.parse_args()
    if args.mock:
        os.environ["LLM_MODE"] = "mock"

    import uvicorn

    from standalone.web.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
