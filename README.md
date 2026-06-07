# Lighter DCA & Monitoring Bot

Lighter 거래소의 포지션 모니터링 및 자동 분할 매수(DCA, Dollar-Cost Averaging) 기능을 수행하는 텔레그램 봇입니다. 

---

## 🌟 주요 기능
1. **정기적 계정/포지션 모니터링** (`monitor.py` & `lighter.py`)
   - 지정한 AEST(호주 동부 표준시) 기준 시각마다 Lighter의 자산 현황, 활성 포지션(진입가, 현재가, PnL, 레버리지, 청산가 위험도) 및 LP(Liquidity Provider) 예치 현황을 조회하여 텔레그램 채널로 자동 전송합니다.
2. **자동 DCA(분할 매수) 수행** (`dca_engine.py`)
   - 매일 설정된 시간에 사전 정의된 자산에 대하여 분할 매수를 실행하고 그 결과를 텔레그램으로 전송합니다.
   - 주문 실패 시 재시도 로직을 내장하고 있어 안정적인 매수를 지원합니다.
3. **텔레그램 대화형 명령어 지원**
   - `/l` : 현재 Lighter 자산 현황, 포지션, LP 예치 현황을 즉시 조회하여 전송합니다.
   - `/dca` : 일일 스케줄과 무관하게 지금 즉시 설정된 금액만큼 DCA 매수를 수동 실행합니다.
   - `/config` : 현재 봇의 설정 상태(매수 시각, 모니터링 시각, 대상 종목 및 금액)를 확인합니다.
   - `/start` / `/stop` : 봇 알림 수신을 등록하거나 중지합니다.

---

## 🛠️ 요구 조건 및 설치 방법

### 1. 사전 요구사항
- Python 3.10 이상 권장
- 텔레그램 봇 API 토큰 및 대상 대화방 ID (Chat ID)
- Lighter API 자격 증명 (지갑 주소 및 API Private Key)

### 2. 패키지 설치
프로젝트 루트 폴더에서 다음 명령어를 실행하여 필요한 패키지를 설치합니다.
```bash
pip install -r requirements.txt
```

---

## ⚙️ 환경 설정 (`.env`)

프로젝트 루트 폴더에 `.env` 파일을 생성하고 다음과 같이 설정합니다. (기존 `.env.example` 파일을 참고하세요.)

```env
# 텔레그램 봇 설정
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Lighter API 설정
LIGHTER_WALLET=your_wallet_address_here
LIGHTER_ACCOUNT_INDEX=0
LIGHTER_API_KEY_INDEX=0
LIGHTER_API_PRIVATE_KEY=your_private_key_here

# DCA 및 모니터링 설정
# 예: DCA_토큰이름=일일구매금액(USDC)
DCA_NVDAUSD=10
DCA_TSLAUSD=10

DCA_TIME_AEST=09:00
MONITOR_HOURS_AEST=8,12,16,20

# 기타 옵션 설정 (필요시 수정)
MIN_LIQ_DISTANCE_PCT=5
MIN_AVAILABLE_BALANCE=30
ORDER_RETRY_INTERVAL_SEC=30
ORDER_PRICE_STEP_PCT=0.05
ORDER_MAX_RETRIES=20
```

---

## 🚀 실행 방법

### 봇 백그라운드 상시 실행
아래 명령어를 통해 텔레그램 봇과 함께 자동 DCA 스케줄러를 가동합니다.
```bash
python lighter.py
```
*(참고: `lighter.py`가 봇의 진입점 스크립트입니다.)*
