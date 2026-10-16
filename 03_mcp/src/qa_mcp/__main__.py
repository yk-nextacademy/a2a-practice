import uvicorn

from common import config
from qa_mcp.server import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=config.QA_PORT, log_level="warning")
