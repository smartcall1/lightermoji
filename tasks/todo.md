# 작업 계획서 (lighter_dca_bot — send_tx 오류 수정)

DCA 실행 시 `send_tx() got an unexpected keyword argument 'body'` 오류가 발생하는 문제를 해결하기 위한 계획이오.

## 태스크 목록
- [ ] 1. `lighter_client.py` 의 `send_tx` 호출 방식 수정
  - [ ] `body={...}` 대신 `tx_type=tx_type, tx_info=tx_info` 로 직접 전달하도록 수정 (주문 생성 및 주문 취소 모두 적용)
- [ ] 2. 로컬 테스트 및 검증
  - [ ] `python -m pytest` 실행하여 테스트 문제 없는지 확인
  - [ ] 로컬 DCA 테스트 스크립트(`run_dca_test.py`)를 실행하여 트랜잭션 전송이 성공하는지 검증
- [ ] 3. 원격 저장소 푸쉬
  - [ ] 변경사항 커밋 및 푸쉬 진행

## 완료 리뷰
- (작업 완료 후 작성 예정)
