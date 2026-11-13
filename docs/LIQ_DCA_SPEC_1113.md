# 청산 전 방어 투입(Liq Guard) 구현 명세 - 2026-11-13 (싸장님 17289)

대상 구현자: 부시터키. 읽기 전용 조사 결과 기반 명세이며 코드는 아직 없음. 앞 문서: docs/LIQ_GUARD_REVIEW_1113.md.
표기: [사실]=코드·SDK·공식문서에서 확인, [가정]=확인 못 한 값을 둔 것, [확인 못 함].

## 0. 사장 결정(반영)
- 1회 투입 = 마진 기준 $100 (명목 $100 x 레버리지가 아니라, 계정에서 나가는 돈이 $100).
- 일·횟수 상한 없음. 버그 방지용만 남김: (1) 한 번 확인당 종목별 1회 (2) 투입 후 가용 잔고 >= MIN_AVAILABLE_BALANCE(config.py:67) (3) .env 킬 스위치 기본 OFF.
- 이 명세가 추가로 넣자고 하는 것 1개: 재발동 조건(5.3 REARM). 한도가 아니라 "같은 가격에서 매 분 $100씩 새는 것"을 막는 버그 방지장치이므로 사장 확인 필요.

## 1. 선 계산 공식 (isolated, 롱)

### 1.1 확인한 사실
- 공식 문서 https://docs.lighter.xyz/trading/liquidations-and-llp-insurance-fund.md : 계정가치 < IMR 이고 >= MMR 이면 pre-liquidation(포지션 늘리는 주문 불가 = "차단선"). 계정가치 < MMR 이고 >= CMR 이면 부분 청산("부분청산선"). 격리 포지션은 "별도 계정", 담보 이름은 AllocatedMargin(문서: 계산 흐름은 동일, 별도 IMR/MMR 식은 없음).
- 같은 문서: 시장의 초기증거금 비율은 `max(사용자가 정한 비율, 시장 최소 비율)` -> 포지션의 imf(레버리지)가 IMR 에 쓰이되 시장 최소가 하한. 판정: 사용자 레버리지 imf 사용(시장 최소보다 크면). [문서가 "건강도 판정"이라고 직접 쓴 건 아님 - 약간 확인 못 함]
- MMR/CMR 은 시장 고정값(계약 사양 표 https://docs.lighter.xyz/trading/contract-specifications.md : MMR=IMR_시장x0.6, CMR=IMR_시장x0.4, IMR_시장=1/시장 최대 레버리지). **사용자가 낮은 레버리지를 골라도 MMR 은 안 변함**(예: 20x 시장에서 3x 로 써도 MMR 3%). [표를 읽어 낸 규칙, 문서가 규칙으로 말하진 않음]
- 마크가로 판정(https://docs.lighter.xyz/trading/fair-price-marking.md).
- 포지션 필드(SDK models/account_position.py): initial_margin_fraction(31), margin_mode(43; signer_client.py:279-280 에서 CROSS=0, ISOLATED=1), allocated_margin(44), liquidation_price(41), unrealized_pnl, position_value, sign, position. monitor.py:129-141 이 이미 읽음(margin_mode 는 아직 안 읽음).
- imf 단위: 봇 코드가 `round(100/imf)`(monitor.py:147)로 레버리지를 구하므로 **퍼센트 값**(예: 25 = 25%) [코드 간접 추정; 실제 응답 문자열은 확인 못 함 -> 그림자 모드 첫 로그에서 확인].

### 1.2 공식 (가격 단위 무관, N=포지션 명목가(마크 기준), Q=수량)
```
IMF = imf/100                       # 포지션 레버리지의 초기증거금 비율
MMF = 시장 maintenance_margin_fraction (아래 1.3)   # 부분청산선
E   = allocated_margin + unrealized_pnl             # 계정가치 [가정: allocated_margin 에 미실현손익이 안 들어 있음]
N   = abs(position_value)
a   = E / N                                         # 증거금 비율(계정가치/명목가)
health_block   = E / (IMF * N)      # 1.0 미만 = 이미 차단 구간
health_partial = E / (MMF * N)      # 1.0 미만 = 이미 부분청산 구간
차단선까지 가격 하락 %   x_block   = (a - IMF)/(1 - IMF) * 100      # 음수면 이미 차단
부분청산선까지 가격 하락 % x_part  = (a - MMF)/(1 - MMF) * 100
부분청산 가격(롱)  P_part = (N - E) / (Q * (1 - MMF))
```
(유도: 마크가가 x 만큼 내리면 E 는 N*x 만큼 줄고 N 은 (1-x)배 -> 위 식. 수수료·펀딩 무시.)
검증 방법(그림자 모드 1일차): 위 P_part 와 CMR 판 `P_cmr`(MMF 대신 0.4x시장IMF)을 API liquidation_price(monitor.py:137)와 비교해 **API 값이 어느 선인지 확정**(앞 문서에서 확인 못 한 항목). 불일치가 크면 E 정의를 바꿔 재검증(E = allocated_margin 만 / + 미실현손익).

### 1.3 MMF 출처
- 1순위: orderBookDetails 의 maintenance_margin_fraction (SDK models/perps_order_book_detail.py:50). 단위 [확인 못 함] - 정수이므로 10,000분율(bps)일 가능성이 높지만(update_leverage 가 `10_000/leverage` 사용, signer_client.py:1329) 첫 로그에서 값을 눈으로 확인한 뒤 단위 확정. 기존 fetch_mark_price(lighter_client.py:71-87)가 같은 엔드포인트를 이미 호출 -> 같은 응답에서 같이 읽는 함수 추가.
- 보수적 대체(필드 못 읽거나 단위 불확실): MMF = 0.6 x IMF. 이유: 실제 MMF 는 시장 최대 레버리지 기준이라 사용자 레버리지(IMF)보다 작거나 같다 -> 0.6xIMF 는 선을 더 가깝게 잡는 쪽(안전). 필드를 읽었는데 0.6xIMF 보다 크면 읽은 값 사용(더 보수적).

### 1.4 cross(liq=0) / 숏 / 조회 실패
- margin_mode==0(cross) 또는 liquidation_price<=0: **계산·투입하지 않고 알림만**("cross 포지션 - 감시 제외"). cross 건강도(계정 전체 TAV/MMR)를 라이브 필드로 받을 수 있는지 [확인 못 함].
- sign!=1(숏): 알림만 (봇 매수는 숏을 줄이는 방향 - 앞 문서 1절).
- 계정 조회 실패·필드 파싱 실패: "알 수 없음" 알림 1회(쿨다운) + 투입 없음. 오류를 안전으로 간주 금지(lighter_client.py:279-283 원칙).

## 2. 핵심 우려 수치화

### 2.1 가장 중요한 발견 (우려의 전제)
**할당 마진이 딱 IMR(= 명목가/레버리지)인 포지션은, 평단 아래로 조금만 내려가도 이미 차단선 아래다.** (a = (IMF-d)/(1-d) < IMF for d>0; 개시 직후 x_block = 0.) 봇이 쌓은 isolated 포지션은 이 모양일 가능성이 높다. 그러면 "차단되기 바로 전"은 가격 구간이 아니라 **초기 순간**이다. 그래서 차단선 거리가 음수일 때도 동작해야 하고, 의미 있는 판단은 "차단선 위로 여유(완충)를 얼마 두느냐"다. 차단 구간에서 그냥 매수하면 문서상 포지션을 늘리는 주문이 거부될 수 있다(실제 거부 여부 [확인 못 함] - 현재 봇은 손실 포지션에도 DCA 매수가 되는 것으로 알려졌는데 이와 충돌하면 위 가정 재검토).

### 2.2 계산 모델 ($100 투입 후)
```
총 투입 B=$100 = 담보 C(update_margin) + 매수 마진 m(주문 명목 = m*L, 레버리지 L=1/IMF)
어떤 비율로 나눠도 투입 후 E2 = E + 100  (매수 마진도 격리 마진으로 묶임 [가정: 신규 주문 증거금이 자동 할당])
N2 = N + m*L,  Q2 = Q + m*L/M,  평단2 = (Q*평단 + (m*L/M)*M)/Q2
```
MMF = 0.6 x IMF (사장 지정 가정)로 계산. 실제 MMF 가 이보다 작은 시장이면 (b) 열은 더 좋음.

### 2.3 표 A - 차단선 위 +2% 지점(트리거 직전)에서 $100 투입 (마크가=100, 평단=103.09 [가정: 평단이 마크보다 3% 위])
표시: (a) 차단선 거리 % / (b) 부분청산선 거리 % (부분청산 가격) / (c) 평단. 투입 전 모든 경우: (a)=2.00.

| 명목 N | 레버 | 투입 전 (b) | 전액 매수($100 마진) | 담보50 + 매수50 | 전액 담보 |
|---|---|---|---|---|---|
| $1,000 | 3x | 18.33% (81.67) | a1.54 / b17.95% (82.05) / 평단102.38 | a8.26 / b23.55% (76.45) / 102.69 | a17.00 / b30.83% (69.17) / 103.09 |
| $1,000 | 4x | 13.53% (86.47) | a1.43 / b13.03% (86.97) / 102.21 | a7.22 / b18.14% (81.86) / 102.58 | a15.33 / b25.29% (74.71) / 103.09 |
| $1,000 | 5x | 10.91% (89.09) | a1.33 / b10.30% (89.70) / 102.06 | a6.60 / b15.09% (84.91) / 102.47 | a14.50 / b22.27% (77.73) / 103.09 |
| $3,000 | 3x | 18.33% (81.67) | a1.82 / b18.18% (81.82) / 102.81 | a4.29 / b20.24% (79.76) / 102.95 | a7.00 / b22.50% (77.50) / 103.09 |
| $3,000 | 4x | 13.53% (86.47) | a1.76 / b13.32% (86.68) / 102.73 | a3.96 / b15.26% (84.74) / 102.90 | a6.44 / b17.45% (82.55) / 103.09 |
| $3,000 | 5x | 10.91% (89.09) | a1.71 / b10.65% (89.35) / 102.65 | a3.77 / b12.52% (87.48) / 102.86 | a6.17 / b14.70% (85.30) / 103.09 |

(계산: scratchpad calc2.py, 위 공식 그대로. 경계 조건 가정 포함.)

### 2.4 표 B - 이미 차단선 아래(평단 대비 -3%, 개시 마진=IMR 포지션)
| N | 레버 | 투입 전 a / b | 전액 매수 | 담보50+매수50 | 전액 담보 |
|---|---|---|---|---|---|
| $1,000 | 3x | -3.09 / 14.09% (85.91) | a-2.38 / b14.68% (85.32) | a3.83 / b19.86% (80.14) | a11.91 / b26.59% (73.41) |
| $1,000 | 4x | -3.09 / 9.04% (90.96) | a-2.21 / b9.82% (90.18) | a2.98 / b14.39% (85.61) | a10.24 / b20.80% (79.20) |
| $1,000 | 5x | -3.09 / 6.28% (93.72) | a-2.06 / b7.22% (92.78) | a2.53 / b11.39% (88.61) | a9.41 / b17.64% (82.36) |
| $3,000 | 3x | -3.09 / 14.09% | a-2.81 / b14.32% | a-0.56 / b16.20% | a1.91 / b18.26% |
| $3,000 | 4x | -3.09 / 9.04% | a-2.73 / b9.36% | a-0.82 / b11.04% | a1.35 / b12.96% |
| $3,000 | 5x | -3.09 / 6.28% | a-2.65 / b6.68% | a-0.93 / b8.24% | a1.07 / b10.07% |
차단 부족액(투입 전 E 가 IMR 에 모자란 금액): N=$1,000 이면 $21~25, N=$3,000 이면 $62~74.

### 2.5 읽는 법 (사실 계산)
1. **전액 매수는 차단선 거리를 줄인다**(표 A: 2.0% -> 1.3~1.8%). 사고 나면 다음 확인에서 바로 차단선 근처/아래 -> 사실상 매 하락마다 같은 상황 반복. 표 B 에선 전액 매수가 차단을 못 푼다(여전히 음수).
2. **부분청산 가격도 전액 매수는 거의 안 내려간다**(표 A 3x $1,000: 81.67 -> 82.05, 오히려 +0.4 올라감; 평단만 103.09 -> 102.38). 싸장님 목표(청산가 내리기)와 반대 방향.
3. 담보를 넣으면 같은 $100 로 부분청산 가격이 5~13 포인트 내려간다(3x $1,000: 81.67 -> 69.17). 평단은 안 변함.
4. N 이 크면(3,000) $100 의 효과가 작다(차단 부족액이 $62~74 라 담보 $100 대부분이 차단 해소에 소진).

### 2.6 추천 방식 (계산식)
**담보 먼저 -> 목표 완충까지 채운 뒤 남는 금액만 매수.** 주문 직전 update_margin 으로 담보 C 를 넣고(확인 후) 남은 m 으로 매수.
```
XT = 투입 후 목표 차단선 거리(예 4%)
m  = ( E + B - IMF*N - XT*(1-IMF)*N ) / ( 1 + XT*(1-IMF)*L )      # 매수 마진
m  = clamp(m, 0, B*(1 - COLLATERAL_MIN_SHARE))                     # 담보 최소 비중 보장
C  = B - m                                                          # 먼저 넣을 담보
```
유도: 주문 명목이 늘면 요구 IMR 이 정확히 매수 마진 m 만큼 늘고(m*L*IMF=m), 나머지 완충을 XT 로 맞춤. 표 C(트리거 +2% -> 투입 후 4%):

| N | 레버 | 담보 C | 매수 마진 m | 투입 후 (b) / 부분청산 가격 | 평단 |
|---|---|---|---|---|---|
| $1,000 | 3x | 19.8 | 80.2 | 20.00% / 80.00 (투입 전 81.67) | 102.49 |
| $1,000 | 4x | 24.1 | 75.9 | 15.29% / 84.71 (86.47) | 102.37 |
| $1,000 | 5x | 27.6 | 72.4 | 12.73% / 87.27 (89.09) | 102.27 |
| $3,000 | 3x | 44.4 | 55.6 | 20.00% / 80.00 (81.67) | 102.93 |
| $3,000 | 4x | 50.9 | 49.1 | 15.29% / 84.71 (86.47) | 102.90 |
| $3,000 | 5x | 55.2 | 44.8 | 12.73% / 87.27 (89.09) | 102.88 |
(트리거 2% 에서 완충 4% 로 가는 정도라 부분청산 가격은 1.7~1.8 포인트만 내려간다.) **싸장님 목표(청산가를 크게 내림)가 우선이면** COLLATERAL_MIN_SHARE 를 올려라: 3x $1,000 기준 담보 비중 50% -> 가격 76.45(5.2 포인트), 100% -> 69.17(12.5 포인트). 대신 평단 하락은 0 -> 매수를 줄이는 만큼 "평단 낮추기"는 포기. 기본값 추천: **COLLATERAL_MIN_SHARE=0.5** (둘 다 얻되 청산가 쪽에 무게; 사장 확정 필요 - 1단계 라이브는 1.0(담보만)로 시작해도 됨).
예산 부족(차단 부족액이 B 이상): C=B(전액 담보), m=0, 알림에 "차단 미해소 - 수동 확인" 표시, 다음 확인에서 REARM 조건(5.3) 아니면 재투입 안 함.

## 3. 트리거 규칙

### 3.1 기본값 (모두 [가정] - 실측 데이터 없음, 그림자 모드에서 보정)
- 확인 주기: **60초**(LIQ_GUARD_INTERVAL_SEC). 이유: fetch_account 1회(timeout 15초, monitor.py:37-54)라 폰에서 1분이 현실적, 2분이면 급락 대응 폭이 커짐. 단 스케줄러가 폰 부하로 7~58분 지연한 전력(lighter_bot.py:26) -> 지연 시 알림에 "마지막 확인 N초 전" 표시.
- 트리거 X(`LIQ_GUARD_TRIGGER_PCT`) = **차단선까지 2.0%** 이하(음수=이미 차단 포함). 근거: 1분 안에 마크가가 1% 안팎 움직이는 건 흔하다는 [가정](종목 변동성은 미조사); 확인 1회분 변동 + 주문 체결 대기(최대 30초 x 여러 회, ORDER_RETRY_INTERVAL_SEC=30, dca_engine.py:176) 동안의 변동을 합쳐 약 2배 여유. 너무 크면(예 5% 이상) 투입이 일찍 일어나 평단·청산가 효과 작고 자본이 일찍 묶임; 너무 작으면(<1%) 한 번 확인 사이 이미 차단.
- 투입 후 목표 `LIQ_GUARD_TARGET_PCT` = **4.0%** (트리거의 2배). 이유: 투입 직후 곧 다시 트리거되지 않게 하되 과도하지 않게. 표 C 참고.
- 그림자 모드에서 며칠 쌓을 보정 데이터: 종목별 "1분 최대 역행 %"(마크 기록), 트리거 도달 횟수, 계산 청산가 vs API liquidation_price 차이.

### 3.2 이미 차단 구간(x_block < 0)일 때
- 롱·isolated·잔고 충분: **담보 먼저(update_margin) -> 담보가 반영된 것을 확인 -> 남는 m 으로 매수.** m 계산은 2.6 식(자동으로 0 이 될 수 있음).
- 담보 반영 확인 실패, 롱이 아님, 잔고 부족: 알림만.
- 주의: 부분청산선 아래(health_partial < 1)에서는 이미 청산 엔진이 동작 중일 수 있음(미체결 주문도 취소됨). 이때도 담보 투입은 허용(청산 회피 효과 있음)하되 매수는 하지 않고 "부분청산 구간 - 수동 확인" 알림 [정책 권고, 사장 확인].

## 4. 구현 지점

### 4.1 파일별
- `config.py` (현재 마지막 설정 줄 78 다음, 80줄 BASE_URL 앞): 새 키 로딩. **키 이름은 `DCA_`로 시작하면 안 됨** - config.py:41 `_parse_dca_markets` 가 DCA_ 접두 키를 종목으로 오인.
- `monitor.py` parse_positions (152-169 dict): `margin_mode`(p.get("margin_mode")), `sign`(1/-1) 키 추가. 단위 확인 전까지 raw `imf_raw` 도 함께 보존.
- 새 파일 `liq_guard.py` (권장; dca_engine 비대화 방지): 순수 함수 `compute_levels(position, mmf)`, `plan_injection(levels, budget, ...)`, 그리고 `async def guard_tick(bot)`.
- `lighter_client.py` set_leverage(94-111) 바로 아래에 `add_isolated_margin(market_id, usdc, account_index)`: set_leverage 와 같은 모양(_make_signer, `signer.update_margin(market_index=, usdc_amount=, direction=signer.ISOLATED_MARGIN_ADD_COLLATERAL, api_key_index=)`, finally _close_clients). SDK: update_margin signer_client.py:1341-1351, 상수 282-283, 금액 스케일 1e6(249). 반환 (tx_info, resp, err) -> err 면 문자열 반환.
- `lighter_client.py` 71-87 근처: `fetch_market_margin(market_id)` (orderBookDetails 에서 maintenance_margin_fraction·min_initial_margin_fraction 읽기).
- `lighter_bot.py` register_jobs (24-41): `jq.run_repeating(job_liq_guard, interval=LIQ_GUARD_INTERVAL_SEC, first=30, name="liq_guard", job_kwargs={"misfire_grace_time": 120, "coalesce": True, "max_instances": 1})`. `job_monitor`(404-410) 옆에 `job_liq_guard`. run_repeating 사용은 이 저장소에 아직 없음 -> PTB 21 JobQueue 지원 여부 구현 시 확인 [확인 못 함]. 킬 스위치 OFF 면 잡 자체를 등록하지 않음.
- `dca_engine.py`: execute_dca/_execute_dca_inner(96-243) 재사용 가능 범위 = 지정가 매수 루프·취소 확정·체결량 확정(149-218). 주의 3가지: (1) 119·226줄의 `_check_safety` 가 liq_dist<5% 면 스킵 -> 가드 호출 땐 우회하도록 keyword-only 인자(예 `guard=True`)를 추가해 liq 검사만 생략하고 **잔고 검사는 유지**. (2) target_usdc 는 **명목**이므로 호출 값 = m x L. (3) 지정가가 retry 마다 +0.05%(config.py:69, 최대 20회 -> 최대 +1%)라 마지막에 마크보다 약 1% 위 가격으로 체결될 수 있음 - 가드 매수는 `ORDER_MAX_RETRIES` 를 낮춘 별도 값 권장(LIQ_GUARD_MAX_RETRIES).
- 동시 실행: execute_dca 가 `_active_symbols`(dca_engine.py:23, 97-105)로 종목 중복을 막으므로, 가드도 execute_dca 를 거치면 같은 종목의 정기 DCA 와 겹치지 않음. 담보 투입 단계도 같은 잠금 안에서 해야 함 -> `guard_tick` 에서 `_active_symbols` 를 직접 쓰지 말고 dca_engine 에 `async def execute_guard(symbol, collateral_usdc, buy_notional)` 를 두어 같은 try/finally 패턴(97-105)으로 감쌈. 잠금 중이면 그 확인은 건너뜀(알림 없음, 로그만).
- client_order_index: 현재 `int(time.time()) % 1_000_000` + retry(dca_engine.py:137,157) -> 가드와 정기 DCA 가 같은 초에 시작하면 값이 겹치고 retry 로 20 범위가 서로 겹칠 수 있음. 가드에서는 **공용 카운터**(모듈 전역, 호출마다 증가) 또는 시작 오프셋 `1_000_000 + (time%1_000_000)` 사용. 상한 한계 [확인 못 함] -> 첫 라이브 때 작은 값부터. 어차피 종목별 잠금으로 같은 종목은 직렬이고, 조회는 market_id 로 좁혀져 있음(lighter_client.py:307-316).

### 4.2 순서(한 번 확인당 종목별 1회)
1. fetch_account -> parse_positions. 2. 종목별: 롱·isolated·정상 필드만 대상. 3. levels 계산. 4. x_block <= TRIGGER 이고 REARM 조건 만족이면 plan. 5. (live) 잔고 검사: available - B >= MIN_AVAILABLE_BALANCE 아니면 알림만. 6. 담보 투입 -> **fetch_account 로 allocated_margin 이 약 C 만큼 늘었는지 확인**(최대 3회 x 3초 재조회) -> 실패 시 매수 없이 중단+알림. 7. m>0 이면 execute_guard 로 매수. 8. 사후 fetch_account -> 사후 levels 로 알림. 한 확인(tick)당 종목 1회, 에러 한 종목이 다른 종목을 막지 않게 종목별 try/except.

### 4.3 알림 문구 (copy-guide 적용 필요 - 문구 확정은 사장)
```
[방어 투입] {종목} 롱 {레버}x
투입 전: 차단선까지 {x_block:.1f}% / 부분청산선까지 {x_part:.1f}% (청산가 {liq})
투입: 담보 ${C:.0f} + 매수 마진 ${m:.0f} (명목 ${m*L:.0f})
투입 후: 차단선까지 {x_block2:.1f}% / 부분청산선까지 {x_part2:.1f}% (청산가 {liq2})
평단 {entry} -> {entry2}  |  가용 잔고 ${balance2:.0f}
```
그림자 모드: 제목 `[방어 그림자 - 주문 없음]` + "만약 실행했다면" 숫자. 알림만 모드(숏/cross/잔고 부족/부분청산 구간): 사유 1줄 + 현재 거리. 같은 상태 반복 알림은 10분 쿨다운.

### 4.4 .env 새 키 이름 (값은 쓰지 않음, 앞 접두 `DCA_` 금지)
`LIQ_GUARD_ENABLED`(킬 스위치, 기본 OFF), `LIQ_GUARD_SHADOW`(기본 켬 = 주문 없이 계산·알림만), `LIQ_GUARD_INTERVAL_SEC`, `LIQ_GUARD_TRIGGER_PCT`, `LIQ_GUARD_TARGET_PCT`, `LIQ_GUARD_BUDGET_USD`(1회 마진 기준 투입액), `LIQ_GUARD_COLLATERAL_MIN_SHARE`, `LIQ_GUARD_REARM_PCT`, `LIQ_GUARD_MAX_RETRIES`, `LIQ_GUARD_MMF_RATIO`(대체 MMF = ratio x IMF), `LIQ_GUARD_SYMBOLS`(선택, 비면 전 롱 isolated 포지션). 기존 `MIN_AVAILABLE_BALANCE`(config.py:67) 재사용.

### 4.5 단계
1단계 그림자(주문 없음, 1~수일): 위 공식 계산·로그·텔레 + API liquidation_price 와 대조 + imf/MMF 단위 확정 + 1분 변동 통계. 2단계 담보만 소액(라이브, 예산 낮춰서 1회). 3단계 담보+매수.

## 5. 위험·버그 방지

### 5.1 반복 투입 폭주
한도가 없으므로 같은 가격 부근에서 "투입 후에도 트리거 상태"가 지속되면 매 분 $100 이 나감(예: 예산이 차단 부족액보다 작을 때, 표 2.4 의 N=$3,000). **REARM 필수**.
### 5.2 연속 하락
투입마다 N 이 커질 수 있고(매수분) 잔고가 줄어 MIN_AVAILABLE_BALANCE 에서 자연 정지. 정지 시 "방어 불가 - 잔고 하한" 알림(반복 쿨다운).
### 5.3 REARM (재발동 조건, 사장 확인 필요)
종목별로 직전 투입 때의 마크가를 저장(메모리 + 파일, 재시작 후에도 유지). 다음 투입은 **마크가가 직전 투입가보다 `LIQ_GUARD_REARM_PCT`(기본 1.0%) 이상 더 내려갔을 때만**. 횟수·금액 상한이 아니라 같은 가격에서의 중복만 막음. 포지션이 사라지거나 가격이 투입가 위로 회복하면 기록 초기화.
### 5.4 기타
- 담보 투입은 취소 불가 거래(update_margin 의 REMOVE 로 되돌리는 규칙 [확인 못 함]) -> 라이브 첫 주문은 소액 + 수동 관찰.
- 투입 중 가격 급변: 담보 반영 확인 후 매수 직전에 포지션을 다시 계산(m 재산정).
- 지정가 매수 미체결(dca 루프 최대 10분): 그 사이 상태 변화 -> 매수 후 사후 계산으로 알림 보정.
- 마켓 정지·거부 응답: 모든 단계에서 err 이면 중단+알림, 재시도 금지(체결 불확실이면 멈춤은 기존 원칙, dca_engine.py:189,196).

## 6. 시험 목록 (모의 객체, 실주문 없음)
tests/test_liq_guard.py (신규) + test_lighter_client_close.py 방식으로 SDK 모의.
1. 공식: 알려진 입력(표 A·B 의 한 줄씩)에서 x_block·x_part·P_part·plan 결과가 일치.
2. 트리거 경계: x_block = TRIGGER 바로 위/같음/아래; 정확히 같을 때 발동(<=) 고정.
3. 차단 구간(x_block<0): 담보 먼저 호출되고 매수는 그 다음; 담보 반영 확인 실패 시 매수 없음.
4. 예산 부족(차단 부족액 >= B): 전액 담보, m=0, "차단 미해소" 알림.
5. 숏 / cross(margin_mode==0, liq=0) / 필드 누락 / 계정 조회 실패: 주문 0건, 알림만(조회 실패는 "알 수 없음").
6. 잔고 하한: available - B < MIN_AVAILABLE_BALANCE 이면 투입 0건 + 알림.
7. 확인당 1회: 같은 tick 에서 같은 종목 2번 처리되지 않음; `_active_symbols` 가 잠겨 있으면 건너뜀(정기 DCA 와 동시).
8. REARM: 직전 투입가 위/아래 +-REARM 경계; 재시작(파일 로드) 뒤에도 유지.
9. 주문 실패: update_margin err -> 매수 없음; 지정가 실패/취소 미확정(dca_engine.py:209-214 경로) -> 중단 알림, 추가 주문 없음.
10. 그림자 모드: 모든 위 경로에서 update_margin·place_limit_buy 호출 0회.
11. 킬 스위치 OFF: register_jobs 에 liq_guard 미등록(test_lighter_bot.py:265-270 방식).
12. 설정 파싱: `LIQ_GUARD_*` 키가 DCA_MARKETS 에 안 섞임(test_config.py 방식).
13. client_order_index: 가드와 정기 DCA 가 같은 초에 시작해도 값이 겹치지 않음.

## 7. 확인 못 한 것
- 문서가 IMR 에 포지션 레버리지 imf 를 쓴다고 직접 "건강도"로 쓴 문장 없음(위 `max(사용자, 시장 최소)` 문장으로 추정).
- imf·maintenance_margin_fraction 의 단위, allocated_margin 에 미실현손익 포함 여부, API liquidation_price 가 MMR 선인지 CMR 선인지(그림자 모드에서 확정).
- 차단 구간에서 실제 주문 거부 여부(현재 봇 DCA 가 손실 포지션에서 되는 것과의 충돌 여부).
- 신규 주문 증거금 자동 할당, 마진 추가 최소 금액·반영 지연, cross 포지션에 update_margin 호출 결과.
- 종목별 1분 변동 폭(트리거 2%는 가정), 폰의 SDK 버전, PTB run_repeating 사용 가능 여부, client_order_index 상한.

## 8. 추가 조건 (싸장님 17291): 정기 DCA 중단 + 물타기만으로 운영

### 8.1 정기 DCA 끄는 스위치
- 키 이름: **`REGULAR_DCA_ENABLED`** (기본 켜짐=1, 키가 없으면 켜짐 -> 기존 동작 불변). **`DCA_` 접두를 쓰면 안 됨**: config.py:41-49 `_parse_dca_markets` 가 DCA_ 로 시작하는 키(예외는 DCA_TIME_AEST 하나, 42줄)를 종목으로 읽어 `DCA_ENABLED=1` 이 "ENABLED 종목 $1 DCA"로 등록돼 버린다.
- config.py 에 `REGULAR_DCA_ENABLED: bool` 추가(73~78줄 설정 블록 근처) + 메모리 갱신 함수 `set_regular_dca(enabled)`: add_dca_market(config.py:91-95)과 같은 패턴(`env_editor.upsert_key` 로 .env 기록 + 메모리 값 즉시 변경) -> **재시작 없이** 폰에서 끌 수 있음. 이때 소비 쪽은 `import config` 후 `config.REGULAR_DCA_ENABLED` 를 실행 시점에 읽어야 함(lighter_bot.py:14-18 처럼 `from config import X` 로 복사하면 변경이 안 보임).
- 폰에서 끄는 방법: 텔레그램 명령 `/dcaoff`, `/dcaon`, `/dcastatus`(신규, lighter_bot.py 에 cmd 추가 + _run_bot_locked 474-480 핸들러 등록 + post_init 437-446 명령 목록 + cmd_start 도움말 113-122). 소유자 필터 OWNER_FILTER 필수(56줄). 대안: .env 직접 편집 + 재시작.
- 막을 곳(둘 다 막는 것을 권장):
  1. 정기 잡: `job_dca`(lighter_bot.py:413-422) 맨 앞에서 `if not config.REGULAR_DCA_ENABLED: log.info(...); return`. 등록(register_jobs 33-36)을 건너뛰면 재시작 전엔 /dcaon 이 안 먹으므로 **등록은 유지하고 실행 시점에 검사**.
  2. 수동 `/dca`: `cmd_dca`(321-356) 맨 앞, `cb_dca_confirm`(359-374), `_execute_manual_dca`(308-318) 중 안쪽인 `_execute_manual_dca` 에 검사를 두면 `/dca confirm` 인자 경로(329-335)와 버튼 경로를 모두 막음. 안내 문구 "정기 DCA 꺼짐 - /dcaon 으로 켜기".
  - 수동 /dca 까지 막는 이유: "매수는 물타기로만 버틴다"는 지시와 일치, 실수 방지. 수동만 허용하고 싶으면 사장 결정(그 경우 1번만 막음).
- 물타기(Liq Guard)는 이 스위치와 **무관하게** 동작(별도 킬 스위치 LIQ_GUARD_ENABLED).
- 테스트 추가: 스위치 OFF 에서 job_dca·/dca·dca_confirm 이 execute_dca 를 0회 호출, ON(기본) 에서 기존 test_lighter_bot.py 동작 그대로, `REGULAR_DCA_ENABLED` 가 DCA_MARKETS 에 섞이지 않음.

### 8.2 물타기 대상 = 열린 롱 포지션 전부 (DCA_MARKETS 무관)
- 대상 선택: 새 함수 `select_guard_targets(account) -> list[dict]` (liq_guard.py). 입력은 `fetch_account()`(monitor.py:37-54) 결과를 `parse_positions()`(monitor.py:127-170)에 넣은 목록. 열린 포지션은 parse_positions 가 size==0 을 이미 제외(130-132). 필터: `side == "Long"`(monitor.py:141, 반환 키 "side" 156줄), `margin_mode`==1 isolated(추가할 키, 4.1절; SDK account_position.py:43), liquidation_price>0(`liq` 키, 163줄). 나머지(숏·cross·필드 이상)는 알림만 목록으로 분리.
- **DCA_MARKETS 를 읽지 않는다.** guard_tick·select_guard_targets 에서 `config.DCA_MARKETS` 임포트 금지(테스트로 고정: DCA_MARKETS 비어 있어도 롱 포지션이 대상).
- 시장 정보: 포지션의 `symbol`(monitor.py:155 키)로 `fetch_market_info(symbol)`(lighter_client.py:43-68; 내부에서 USD 접미를 떼고 오더북 symbol 과 대조, 캐시) 호출. 포지션 symbol 이 오더북 symbol 과 어떤 형태로 맞는지는 실제 응답 확인 못 함 -> 그림자 모드 첫 로그에서 `fetch_market_info` 성공 여부를 종목별로 확인(실패 종목은 알림 "시장 정보 없음"). market_id 는 포지션에도 있음(monitor.py:153).
- 매수 실행: `execute_dca(symbol, 명목)`은 DCA_MARKETS 를 쓰지 않음(dca_engine.py:96-243이 인자로만 받음) -> 목록과 무관하게 호출 가능. 신규 종목 아님(기존 포지션 있음) 경로(126행 position["current"])를 탐.
- `_find_position`(dca_engine.py:63-69)은 USD/USDC 접미를 뗀 base 이름으로 매칭 -> 동일 base 가 둘이면(예 가상 NVDAUSD/NVDAUSDC) 첫 항목. 현실성은 낮으나 guard 는 `market_id` 로 포지션을 다시 찾도록(사후 재조회) 권장.

### 8.3 /removecoin 과의 충돌
없음. `remove_dca_market`(config.py:98-103)은 .env 의 `DCA_<심볼>` 키 삭제 + 메모리 DCA_MARKETS pop 뿐이고, 물타기는 DCA_MARKETS 도 `DCA_` 키도 읽지 않아 목록을 비워도 계속 돈다. 단, 같은 .env 를 쓰는 `env_editor` 동시 쓰기 가능성(/removecoin 과 /dcaoff 를 동시에 누름)은 확인 못 함 - 사용자 한 명 수동 조작이라 위험 낮음.
