"""빌드/테스트 시뮬레이터. 순수 함수로만 구성된다."""

from . import scenario


def next_version(version: str) -> str:
    major, minor, _ = (int(x) for x in version.split("."))
    return f"{major}.{minor + 1}.0"


def initial_defects() -> list[str]:
    return list(scenario.INITIAL_DEFECTS)


def apply_fixes(defects: list[str], fixed_test_case_ids: list[str]) -> tuple[list[str], list[str]]:
    """수정된 테스트케이스에 영향을 주는 결함을 제거하고, 회귀 결함을 추가한다.

    Returns: (다음 빌드의 결함 목록, 새로 생긴 회귀 결함 목록)
    """
    removed = [d for d in defects if scenario.DEFECTS[d]["test_case_id"] in fixed_test_case_ids]
    remaining = [d for d in defects if d not in removed]
    introduced = [scenario.REGRESSIONS[d] for d in removed if d in scenario.REGRESSIONS]
    return remaining + [d for d in introduced if d not in remaining], introduced


def run_test(defects: list[str], test_case_id: str) -> tuple[bool, str]:
    for defect_id in defects:
        defect = scenario.DEFECTS[defect_id]
        if defect["test_case_id"] == test_case_id:
            return False, defect["log"]
    return True, "OK"
