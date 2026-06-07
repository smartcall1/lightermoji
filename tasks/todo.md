# 작업 계획서 (lighter_dca_bot — DCA 오류 수정)

DCA 실행 시 "가격 조회 불가 (포지션 없음)" 오류가 발생하는 문제를 해결하기 위한 계획이오.

## 태스크 목록
- [x] 1. `dca_engine.py` 의 `_find_position` 수정
  - [x] 비교 로직을 base symbol(예: `HYPE` vs `HYPEUSD`) 기준으로 유연하게 일치하도록 수정
- [x] 2. 테스트 코드(`tests/test_dca_engine.py`)에 테스트 케이스 추가 및 검증
  - [x] `_find_position` 이 다양한 포맷의 심볼명(예: `HYPE`, `HYPEUSD`)을 올바르게 매칭하는지 유닛 테스트 작성
  - [x] `pytest` 테스트를 실행하여 전체 테스트 통과 확인
- [x] 3. 원격 저장소 푸쉬
  - [x] 변경사항 커밋 및 푸쉬 진행

## 완료 리뷰
- 대감의 수동 DCA 실행 중 발생한 "가격 조회 불가 (포지션 없음)" 오류의 근본 원인을 진단하였소. 원인은 `.env`에서 파싱된 DCA 대상 심볼(예: `HYPE`)과 계정 조회 API로부터 가져온 포지션의 실제 심볼(예: `HYPEUSD`)이 단순 문자열 비교 시 일치하지 않아 포지션 유무를 판별하지 못했던 것이었소.
- 이를 해결하기 위해 `dca_engine.py` 의 `_find_position` 함수에서 두 심볼을 비교할 때 `"USD"` 및 `"USDC"`를 제거하고 대문자로 치환한 base symbol 기준으로 유연하게 비교하도록 로직을 개선하였소. 이로써 대감께서 `.env`에 `DCA_HYPE`와 `DCA_HYPEUSD` 어떤 포맷으로 입력하셨든 완벽히 매칭을 수행할 수 있게 되었소.
- 해당 개선 사항의 정합성을 검증하기 위해 `tests/test_dca_engine.py`에 유닛 테스트 케이스(`test_find_position_flexible_matching`)를 추가하였고, `pytest`를 수행하여 20개의 테스트를 모두 무사히 통과 완료시켰소.
- 모든 변경사항을 원격 저장소 `lightermoji` 의 `main` 브랜치에 안전하게 푸시하였으니, 대감께서는 원격 서버(또는 Termux 환경)에서 `git pull`을 진행하신 후 다시 DCA 수동 실행을 테스트해 주시길 청하오.


