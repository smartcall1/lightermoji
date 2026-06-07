# 작업 계획서 (lighter_dca_bot — DCA 오류 수정)

DCA 실행 시 "가격 조회 불가 (포지션 없음)" 오류가 발생하는 문제를 해결하기 위한 계획이오.

## 태스크 목록
- [x] 1. `dca_engine.py` 의 `_find_position` 수정
  - [x] 비교 로직을 base symbol(예: `HYPE` vs `HYPEUSD`) 기준으로 유연하게 일치하도록 수정
- [x] 2. 테스트 코드(`tests/test_dca_engine.py`)에 테스트 케이스 추가 및 검증
  - [x] `_find_position` 이 다양한 포맷의 심볼명(예: `HYPE`, `HYPEUSD`)을 올바르게 매칭하는지 유닛 테스트 작성
  - [x] `pytest` 테스트를 실행하여 전체 테스트 통과 확인
- [ ] 3. 원격 저장소 푸쉬
  - [ ] 변경사항 커밋 및 푸쉬 진행

## 완료 리뷰
- (작업 완료 후 작성 예정)


