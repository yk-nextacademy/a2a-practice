"""Stage 4 - A2A 진입점. Dev Agent / QA Agent / Hub 3개 프로세스를 함께 기동한다.

    uv run run.py              # Hub http://localhost:8400
    uv run run.py --mock       # LLM 없이 실행 (Hub 판단, Dev/QA 문서 작성 모두 규칙 기반)
    uv run run.py --only qa_agent
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
SERVICES = [("dev_agent", "Dev Agent", 8401), ("qa_agent", "QA Agent", 8402), ("hub", "Hub", 8400)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 4 - A2A")
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
        suffix = "/.well-known/agent-card.json" if module != "hub" else ""
        print(f"  {name:<9} http://localhost:{port}{suffix}", flush=True)
        time.sleep(0.8)
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
