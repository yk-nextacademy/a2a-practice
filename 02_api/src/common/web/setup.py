"""서비스별 웹 화면 설정: 공통 정적 자산 + 공통/서비스 템플릿."""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from .ui import register_filters

COMMON_WEB = Path(__file__).resolve().parent


def setup_web(app: FastAPI, service_templates: Path) -> Jinja2Templates:
    app.mount("/static", StaticFiles(directory=COMMON_WEB / "static"), name="static")
    templates = Jinja2Templates(directory=service_templates)
    templates.env.loader = ChoiceLoader([FileSystemLoader(service_templates), FileSystemLoader(COMMON_WEB / "templates")])
    register_filters(templates.env)
    templates.env.filters["pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=1)
    return templates
