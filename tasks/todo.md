# 작업 계획서 (lighter_dca_bot)

Lighter DCA Bot에 `lighter_monitor` 기능(최종 텔레그램 메시지 포맷)이 유기적으로 통합되어 작동하는지 검증하고, 테스트 코드를 수정한 후 원격 저장소에 최종 푸쉬를 완료하는 계획이오.

## 태스크 목록
- [x] 1. 테스트 코드(`tests/test_monitor.py`) 수정 및 검증
  - [x] `format_position_message` 호출 시 `funding_rates` 매개변수 누락 오류 해결
  - [x] `pytest` 테스트를 실행하여 전체 테스트가 무사히 통과하는지 확인
- [ ] 2. 원격 저장소 푸쉬
  - [ ] 로컬 Git 상태 점검 및 최종 커밋 작성
  - [ ] `origin/main` 브랜치로 안전하게 푸쉬 완료

## 완료 리뷰
- (작업 완료 후 작성 예정)

