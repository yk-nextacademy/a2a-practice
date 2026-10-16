import uvicorn

from common import config
from dev_server.app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=config.DEV_PORT, log_level="warning")
