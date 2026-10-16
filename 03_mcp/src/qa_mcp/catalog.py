"""검증부서가 관리하는 테스트케이스와 판정 규칙."""

TEST_CASES = [
    {"id": "TC-01", "feature": "login", "name": "정상 로그인"},
    {"id": "TC-02", "feature": "login", "name": "세션 유지"},
    {"id": "TC-03", "feature": "login", "name": "비밀번호 오류 처리"},
    {"id": "TC-04", "feature": "search", "name": "키워드 검색"},
    {"id": "TC-05", "feature": "search", "name": "페이지네이션"},
    {"id": "TC-06", "feature": "search", "name": "필터 조합"},
    {"id": "TC-07", "feature": "payment", "name": "카드 결제"},
    {"id": "TC-08", "feature": "payment", "name": "환불 금액 계산"},
    {"id": "TC-09", "feature": "payment", "name": "결제 취소"},
    {"id": "TC-10", "feature": "report", "name": "일별 리포트"},
    {"id": "TC-11", "feature": "report", "name": "결제 합계 리포트"},
    {"id": "TC-12", "feature": "report", "name": "CSV 내보내기"},
]
SEVERITIES = ["Critical", "Major", "Minor"]


def run_test(build: dict, test_case_id: str) -> tuple[bool, str]:
    for behavior in build.get("behaviors", []):
        if behavior["breaks"] == test_case_id:
            return False, behavior["log"]
    return True, "OK"
