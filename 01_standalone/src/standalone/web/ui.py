"""템플릿 필터: 상태별 배지 톤, 시간 표시 등."""

TONES = {
    # cycle
    "RELEASING": "live", "VERIFYING": "live", "TRANSFERRING": "live", "FIXING": "live",
    "APPROVED": "solid", "FAILED": "danger", "CANCELLED": "muted",
    # issue
    "OPEN": "solid", "REOPENED": "danger", "TRANSFERRED": "muted", "IN_PROGRESS": "live",
    "FIXED": "outline", "VERIFIED": "muted",
    # severity
    "Critical": "solid", "Major": "muted", "Minor": "outline",
    # run
    "RUNNING": "live", "DONE": "outline", "PASS": "outline", "FAIL": "solid",
}

STATUS_LABELS = {
    "RELEASING": "발행 중", "VERIFYING": "검증 중", "TRANSFERRING": "이관 중", "FIXING": "수정 중",
    "APPROVED": "승인", "FAILED": "실패", "CANCELLED": "취소",
}


def tone(value) -> str:
    return TONES.get(str(value), "outline")


def hhmmss(ts: str | None) -> str:
    return ts[11:19] if ts and len(ts) >= 19 else (ts or "")


def ymdhm(ts: str | None) -> str:
    return ts[:16].replace("T", " ") if ts else "-"


def pct(passed, total) -> int:
    return round((passed or 0) * 100 / total) if total else 0


def register_filters(env) -> None:
    env.filters["tone"] = tone
    env.filters["hhmmss"] = hhmmss
    env.filters["ymdhm"] = ymdhm
    env.filters["status_label"] = lambda s: STATUS_LABELS.get(s, s)
    env.globals["pct"] = pct
