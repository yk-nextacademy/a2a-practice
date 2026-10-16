"""개발부서가 관리하는 제품/빌드 정보와 결함 시뮬레이션 규칙."""

PRODUCT = "Commerce Web"
INITIAL_VERSION = "1.0.0"
FEATURES = ["login", "search", "payment", "report"]

DEFECTS = {
    "D-101": {"breaks": "TC-02", "log": "AssertionError: session expired after 5m (expected >= 30m)\n  at auth/session.py:88 refresh_token()"},
    "D-102": {"breaks": "TC-05", "log": "AssertionError: page 2 returned 0 items (expected 20)\n  at search/paginate.py:41 offset computed as page*size+size"},
    "D-103": {"breaks": "TC-08", "log": "AssertionError: refund amount 9999 != 10000\n  at payment/refund.py:57 int(amount * 0.9999) truncation"},
    "D-201": {"breaks": "TC-11", "log": "AssertionError: report total 1,250,000 != payment ledger 1,249,990\n  at report/summary.py:73 refund rounding mismatch"},
}
INITIAL_DEFECTS = ["D-101", "D-102", "D-103"]
REGRESSIONS = {"D-103": "D-201"}


def next_version(version: str) -> str:
    major, minor, _ = (int(x) for x in version.split("."))
    return f"{major}.{minor + 1}.0"


def apply_fixes(defects: list[str], fixed_test_case_ids: list[str]) -> tuple[list[str], list[str]]:
    removed = [d for d in defects if DEFECTS[d]["breaks"] in fixed_test_case_ids]
    remaining = [d for d in defects if d not in removed]
    introduced = [REGRESSIONS[d] for d in removed if d in REGRESSIONS]
    return remaining + [d for d in introduced if d not in remaining], introduced


def build_manifest(version: str, defects: list[str]) -> dict:
    return {
        "product": PRODUCT, "version": version, "features": FEATURES,
        "behaviors": [{"id": d, "breaks": DEFECTS[d]["breaks"], "log": DEFECTS[d]["log"]} for d in defects],
    }
