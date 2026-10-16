"""Stage 3 - MCP 진입점. Dev MCP Server / QA MCP Server / Host 3개 프로세스를 함께 기동한다.

    uv run run.py            # Host http://localhost:8300 (HOST_PORT / DEV_PORT / QA_PORT로 변경)
    uv run run.py --mock     # LLM 없이 규칙 기반 플래너로 실행
    uv run run.py --only dev_mcp
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))
from common import config  # noqa: E402  표시용 포트를 각 서비스와 같은 설정(.env, 환경변수)에서 읽는다

SERVICES = [("dev_mcp", "Dev MCP", config.DEV_PORT), ("qa_mcp", "QA MCP", config.QA_PORT), ("host", "Host", config.HOST_PORT)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3 - MCP")
    parser.add_argument("--mock", action="store_true", help="LLM 대신 규칙 기반(mock) 플래너로 실행")
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
        print(f"  {name:<8} http://localhost:{port}" + ("/mcp" if module != "host" else ""), flush=True)
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
