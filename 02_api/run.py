"""Stage 2 - API 진입점. Hub / Dev Server / QA Server 3개 웹 서비스를 함께 기동한다.

    uv run run.py            # Hub http://localhost:8200, Dev http://localhost:8201, QA http://localhost:8202
    uv run run.py --mock     # LLM 없이 실행

각 서비스 단독 실행:
    uv run python -m dev_server   (src 폴더를 PYTHONPATH에 두고 실행, run.py가 자동 설정)
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
SERVICES = [("dev_server", "Dev 웹", 8201), ("qa_server", "QA 웹", 8202), ("hub", "Hub 웹", 8200)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 - API")
    parser.add_argument("--mock", action="store_true", help="LLM 대신 규칙 기반(mock) 모드로 실행")
    parser.add_argument("--only", choices=[s[0] for s in SERVICES], help="지정한 서비스만 실행")
    args = parser.parse_args()

    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONUNBUFFERED": "1"}
    if args.mock:
        env["LLM_MODE"] = "mock"

    procs: list[subprocess.Popen] = []
    for module, name, port in SERVICES:
        if args.only and module != args.only:
            continue
        procs.append(subprocess.Popen([sys.executable, "-m", module], env=env))
        print(f"  {name:<7} http://localhost:{port}", flush=True)
        time.sleep(0.5)
    print("Ctrl+C로 전체 종료", flush=True)

    try:
        while any(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
