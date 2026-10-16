"""업무 시나리오 정의 (전 단계 공통 규칙).

빌드는 '숨은 결함' 목록을 가지며, 결함이 영향을 주는 테스트케이스는 실패한다.
값이 고정되어 있어 어느 단계에서 실행해도 같은 결과가 나온다.
"""

PRODUCT = "Commerce Web"
INITIAL_VERSION = "1.0.0"
MAX_ITERATIONS = 5

FEATURES = ["login", "search", "payment", "report"]

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

DEFECTS = {
    "D-101": {
        "test_case_id": "TC-02",
        "log": "AssertionError: session expired after 5m (expected >= 30m)\n  at auth/session.py:88 refresh_token()",
    },
    "D-102": {
        "test_case_id": "TC-05",
        "log": "AssertionError: page 2 returned 0 items (expected 20)\n  at search/paginate.py:41 offset computed as page*size+size",
    },
    "D-103": {
        "test_case_id": "TC-08",
        "log": "AssertionError: refund amount 9999 != 10000\n  at payment/refund.py:57 int(amount * 0.9999) truncation",
    },
    "D-201": {
        "test_case_id": "TC-11",
        "log": "AssertionError: report total 1,250,000 != payment ledger 1,249,990\n  at report/summary.py:73 refund rounding mismatch",
    },
}

INITIAL_DEFECTS = ["D-101", "D-102", "D-103"]

# 결함 수정 시 발생하는 회귀 결함 (수정한 결함 -> 새로 생기는 결함)
REGRESSIONS = {"D-103": "D-201"}

# mock 모드 심각도 규칙
FEATURE_SEVERITY = {"payment": "Critical", "login": "Major", "report": "Major", "search": "Minor"}

SEVERITIES = ["Critical", "Major", "Minor"]

# 실습 모드 부서원 (가상 인물). 검증부서원은 담당 기능의 테스트케이스를 맡는다.
TEAM = [
    {"name": "김검증", "dept": "QA", "role": "로그인·검색 담당", "features": ["login", "search"]},
    {"name": "이검증", "dept": "QA", "role": "결제 담당", "features": ["payment"]},
    {"name": "박검증", "dept": "QA", "role": "리포트 담당", "features": ["report"]},
    {"name": "최개발", "dept": "Dev", "role": "백엔드 개발", "features": []},
    {"name": "정개발", "dept": "Dev", "role": "프론트엔드 개발", "features": []},
]


def test_case(test_case_id: str) -> dict:
    return next(tc for tc in TEST_CASES if tc["id"] == test_case_id)
