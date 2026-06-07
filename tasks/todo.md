# 작업 계획서 (lighter_dca_bot — send_tx 오류 수정)

DCA 실행 시 `send_tx() got an unexpected keyword argument 'body'` 오류가 발생하는 문제를 해결하기 위한 계획이오.

## 태스크 목록
- [x] 1. `lighter_client.py` 의 `send_tx` 호출 방식 수정
  - [x] `body={...}` 대신 `tx_type=tx_type, tx_info=tx_info` 로 직접 전달하도록 수정 (주문 생성 및 주문 취소 모두 적용)
- [x] 2. 로컬 테스트 및 검증
  - [x] `python -m pytest` 실행하여 테스트 문제 없는지 확인
  - [x] 로컬 DCA 테스트 스크립트(`run_dca_test.py`)를 실행하여 트랜잭션 전송이 성공하는지 검증
- [x] 3. 원격 저장소 푸쉬
  - [x] 변경사항 커밋 및 푸쉬 진행

## 완료 리뷰
- `TransactionApi.send_tx` 호출 시 `body` 키워드 인자 대신 `tx_type`과 `tx_info`를 개별 인자로 직접 제공하고, 누락되어 있던 `await` 키워드를 추가하여 비동기 트랜잭션 전송 흐름을 완벽히 교정하였소.
- 로컬 환경의 실제 `.env` 지갑 설정을 통해 HYPEUSD, LITUSD 두 종목에 대해 각각 $45 상당의 수동 DCA 테스트 실행을 완수하였소. 실시간으로 각각 $45.07 및 $45.00 의 지정가 매수 트랜잭션이 전송되고 체결 완료되는 것을 검증하였소.
- 검증이 완료된 수정본을 `lightermoji` 원격 저장소에 최종 푸시 완료하였소. 대감께서는 원격 서버(또는 Termux 환경)에서 `git pull`을 당기신 후 즉시 봇을 실행하여 DCA 수동/자동 실행을 하실 수 있소.
