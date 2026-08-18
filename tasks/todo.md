# DCA 수동 실행 확인 단계 추가 작업

## 체크리스트
- [x] `tasks/todo.md` 작업 계획 수립 <!-- id: 0 -->
- [x] `lighter_bot.py` 수정 <!-- id: 1 -->
  - [x] `/dca` 명령어에 인라인 키보드 확인 단계 추가 (`dca_confirm`, `dca_cancel`)
  - [x] `/dca confirm` 등 인자 입력 시 즉시 실행 분기 유지
  - [x] `cb_dca_confirm`, `cb_dca_cancel` 콜백 핸들러 및 권한 체크 구현
  - [x] `Application` 핸들러 등록 및 메뉴/도움말 텍스트 업데이트
- [x] 단위 테스트 작성 및 기존 테스트 검증 <!-- id: 2 -->
- [x] 완료 리뷰 및 문서화 <!-- id: 3 -->

## 변경 내용 요약 (Review)
- **`lighter_bot.py`**:
  - `/dca` 명령어 수신 시 실행 대상 종목 목록과 총 금액을 안내하고 인라인 키보드(`⚡ 예, DCA 실행 ($...)` / `❌ 취소`) 버튼 표시.
  - 직접 실행 파라미터(`/dca confirm`, `/dca yes`, `/dca y`, `/dca go`) 지원.
  - 인라인 버튼 클릭 처리를 위한 `cb_dca_confirm` 및 `cb_dca_cancel` 콜백 핸들러 등록.
  - 실행 권한 검사(`_is_owner`) 적용.
  - 봇 메뉴 설명 및 `/start` 안내 텍스트 업데이트.
- **`tests/test_lighter_bot.py`**:
  - 종목 미설정, 확인 키보드 렌더링, 직접 confirm 인자 처리, 콜백 confirm/cancel(권한 체크 포함) 전체 7개 케이스 단위 테스트 작성 및 통과(35 passed).
