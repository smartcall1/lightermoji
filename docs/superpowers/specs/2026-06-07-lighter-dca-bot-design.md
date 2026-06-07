# Lighter DCA Bot — 설계 문서

## 개요

Lighter perpdex 전용 텔레그램 봇. 포지션 모니터링 + 종목별 일일 DCA 매수 기능을 단일 프로세스로 제공. 기존 `market_dashboard_bot`의 Lighter 기능을 분리·독립시키고, DCA 자동매수 기능을 추가.

---

## 아키텍처

```
lighter_dca_bot/
├── bot.py              # Telegram Application 진입점, 명령어 핸들러
├── config.py           # .env 로더, DCA 설정 파싱
├── lighter_client.py   # Lighter REST API 읽기 + SDK 주문 서명/전송
├── dca_engine.py       # DCA 실행 로직 (지정가 → 가격 추격 → 전량 체결)
├── monitor.py          # 포지션 조회 및 메시지 포맷
├── .env                # 실제 비밀값 (gitignore)
├── .env.example        # 설정 템플릿
├── requirements.txt
└── .gitignore
```

### 실행 구조

단일 `python bot.py` 프로세스. `python-telegram-bot`의 `job_queue`로 스케줄 관리.

```
bot.py
 ├── [job] monitor_broadcast  — AEST 지정 시각마다 포지션 현황 자동 발송
 ├── [job] dca_job            — 매일 AEST 지정 시각에 DCA 실행
 ├── /l   → monitor.py        — 온디맨드 포지션 조회
 ├── /dca → dca_engine.py     — 수동 DCA 즉시 실행
 └── /config                  — 현재 DCA 설정 조회
```

---

## 설정 (.env)

```env
# ── Telegram ─────────────────────────────────
TELEGRAM_BOT_TOKEN=         # 새 BotFather 봇 토큰
TELEGRAM_CHAT_ID=           # 알림 받을 chat_id

# ── Lighter 인증 ──────────────────────────────
LIGHTER_WALLET=0x...        # L1 지갑 주소 (포지션 조회용)
LIGHTER_ACCOUNT_INDEX=      # Lighter 내부 계정 인덱스 (비우면 자동 조회)
LIGHTER_API_KEY_INDEX=      # API 키 인덱스 (정수, 0~254)
LIGHTER_API_PRIVATE_KEY=    # Lighter UI에서 발급한 API 개인키

# ── DCA 설정 (종목별 USDC 매수 금액) ──────────
DCA_SKHYNIXUSD=50           # 하루에 $50어치 SK하이닉스 매수
DCA_NVDAUSD=30              # 하루에 $30어치 NVIDIA 매수
# DCA_TSLAUSD=20            # 비활성화: 주석처리

# ── 스케줄 ───────────────────────────────────
DCA_TIME_AEST=09:00         # DCA 실행 시각 (AEST)
MONITOR_HOURS_AEST=8,12,16,20  # 포지션 자동 발송 시각 (AEST)

# ── 안전장치 ──────────────────────────────────
MIN_LIQ_DISTANCE_PCT=5      # 청산가까지 X% 미만이면 DCA 스킵
MIN_AVAILABLE_BALANCE=30    # 가용 잔고 $X 미만이면 DCA 스킵

# ── 주문 실행 튜닝 ────────────────────────────
ORDER_RETRY_INTERVAL_SEC=30  # 미체결 후 재주문 대기 시간
ORDER_PRICE_STEP_PCT=0.05    # 재주문 시 호가 인상 폭 (%)
ORDER_MAX_RETRIES=20         # 최대 재시도 횟수 (=최대 10분 대기)
```

---

## 모듈별 설계

### `config.py`
- `.env` 로드
- `DCA_*` 패턴 파싱 → `{symbol: usdc_amount}` dict 반환
- `MONITOR_HOURS_AEST` 파싱 → `List[int]`
- `DCA_TIME_AEST` 파싱 → `(hour, minute)` tuple

### `lighter_client.py`

**읽기 (인증 불필요):**
```python
fetch_account(wallet) -> dict          # 포지션, 잔고, 마진 등
fetch_market_info(symbol) -> dict      # market_index, tick_size, step_size
fetch_best_ask(market_index) -> float  # 현재 최우선 매도호가
fetch_order_status(order_id) -> dict   # 체결 여부 확인
```

**쓰기 (API 키 서명):**
```python
place_limit_order(market_index, usdc_amount, price) -> order_id
cancel_order(order_id) -> bool
```

`SignerClient` 초기화: `url`, `api_private_keys={index: key}`, `account_index`

### `dca_engine.py` — 핵심 로직

```
execute_dca(symbol, usdc_target):
  1. fetch_account() → 안전장치 체크
     - 가용 잔고 < MIN_AVAILABLE_BALANCE → 스킵 + 경고
     - 청산가 거리 < MIN_LIQ_DISTANCE_PCT → 스킵 + 경고
  2. fetch_market_info(symbol) → market_index, step_size
  3. remaining_usdc = usdc_target
     retry = 0
  4. LOOP:
     a. ask = fetch_best_ask(market_index)
        limit_price = ask * (1 + retry * ORDER_PRICE_STEP_PCT / 100)
        base_amount = remaining_usdc / limit_price  (step_size 반올림)
     b. order_id = place_limit_order(market_index, base_amount, limit_price)
     c. sleep(ORDER_RETRY_INTERVAL_SEC)
     d. status = fetch_order_status(order_id)
        filled_usdc = status.filled_amount * limit_price
        remaining_usdc -= filled_usdc
     e. if remaining_usdc <= 0: DONE
     f. cancel_order(order_id)  # 잔량 취소
        retry += 1
        if retry >= ORDER_MAX_RETRIES: 경고 후 종료
        goto LOOP
  5. send_dca_notification(symbol, 체결결과, 포지션현황)
```

### `monitor.py`
- `lighter_monitor.py` + `lighter_status.py`의 포지션 파싱·포맷 로직 통합
- `format_position_message(account) -> str` 반환
- 펀딩레이트 포함 (기존 lighter_status.py 기능 유지)

### `bot.py`
- `/l` — `monitor.py` 호출, 포지션 현황 반환
- `/dca` — `dca_engine.execute_dca()` 수동 트리거 (owner 전용)
- `/config` — 현재 DCA 종목·금액·시각 출력
- `job: monitor_broadcast` — MONITOR_HOURS_AEST 시각마다 자동 발송
- `job: dca_job` — DCA_TIME_AEST에 모든 활성 종목 순차 실행

---

## 텔레그램 알림 포맷

**DCA 체결 완료:**
```
⚡ DCA 완료 — SK하이닉스 L10x
─────────────────
💰 매수: $50.00 → 0.082주 @ $612.50
📊 총 포지션: 0.482주 ($295.30)
⚠️ 청산가: $430.20 (30.1% 여유)
📈 미실현 PnL: +$12.30 (+4.3%)
```

**안전장치 스킵:**
```
⚠️ DCA 스킵 — SK하이닉스
이유: 청산가까지 3.2% (최소 5% 필요)
현재 포지션: 0.400주 / 청산가 $590.10
```

---

## market_dashboard_bot 변경 사항

- `lighter_status.py` 삭제
- `bot.py`에서 `lighter_status` import, `/l` 핸들러, `lighter_scheduled` 잡 제거
- `requirements.txt`에서 `lighter` 패키지 제거

---

## Termux 실행 방법

```bash
cd ~/lighter_dca_bot
pip install -r requirements.txt
cp .env.example .env
# .env 편집 후
python bot.py
```

`nohup python bot.py &` 또는 Termux 백그라운드 실행.
