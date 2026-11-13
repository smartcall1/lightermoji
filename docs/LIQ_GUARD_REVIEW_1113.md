# Lighter DCA 봇 - 청산 전 감지 물타기 검토 (2026-11-13, 읽기 전용)

범위: 봇 기능 설계와 위험만. 종목 매수/매도 판단은 쓰지 않음. 코드 수정·실행·API 호출 없음.
표기: [사실]=코드/SDK 소스에서 직접 확인, [가정]=확인 못 한 값을 둔 것, [확인 못 함].

## 1. 현황 (파일:줄)

### 포지션마다 보는 값
- [사실] monitor.py:127-170 parse_positions 가 포지션별로 읽는 값: 수량 position(130), 평단 avg_entry_price(134), 가치 position_value(135), 미실현손익(136), liquidation_price(137), allocated_margin(138), initial_margin_fraction(139), 펀딩(140), 방향 sign(141).
- [사실] 마크가는 포지션에서 따로 안 받고 `value/size`로 역산(144). 레버리지는 `round(100/imf)`(147). 마진 대비 손익률 pnl_pct(146).
- [사실] 청산 거리 liq_dist = (liq-현재가)/현재가*100 (150). liq<=0 이면 999.0(= 해당 없음, 안전 취급), 반환은 abs(...)(164). 즉 cross 포지션(liq=0)은 거리 999로 보임. 148-149줄 주석이 이 의도를 명시.
- [사실] 포지션의 margin_mode 필드(SDK models/account_position.py:43)가 API 응답에 있으나 봇은 읽지 않음(monitor.py 어디에도 없음). 포지션이 isolated 인지 cross 인지 코드로 구분하지 않음.
- [확인 못 함] 폰에서 실제 포지션이 isolated 인지 cross 인지. 코드상 봇이 지정하는 건 set_leverage 의 isolated 뿐(lighter_client.py:94-111, 98-103 `ISOLATED_MARGIN_MODE`). 메모대로 cross 포지션이 존재하면 Lighter 앱에서 따로 만든 것.

### 언제 사는가
- [사실] 하루 1회 job_dca (lighter_bot.py:33-36 run_daily, 기본 AEST 09:00 config.py:61). 종목·금액은 .env 의 `DCA_<심볼>=금액`(config.py:39-53). `/addcoin` `/removecoin`으로 런타임 수정(config.py:91-103). 수동 `/dca`(lighter_bot.py:321-374)는 확인 버튼 후 같은 종목 전부 실행.
- [사실] 매수는 지정가 매수만(lighter_client.py:149-198, is_ask=False, reduce_only=False). 가격을 retry 마다 +0.05%씩 올리며(config.py:69, dca_engine.py:59-60) 최대 20회, 30초 간격(config.py:68,70), 매 회 잔량 취소 확정 후 다음 주문(dca_engine.py:178-214).
- [사실] 매수 전용이라 **Short 포지션에 DCA 가 걸리면 포지션을 줄이는 방향**(execute_dca 에 방향 검사 없음, dca_engine.py:96-243). 현재 DCA_MARKETS 종목에 Short 가 있는지는 [확인 못 함].

### 청산 근접 알림·안전 검사
- [사실] 청산 근접 **알림은 없음**. 모니터 잡(lighter_bot.py:404-410)은 get_full_status 문자열을 그대로 보낼 뿐이고, 임계값 비교가 없다. 청산가는 메시지 한 줄 `⚠️$liq`로만 표시(monitor.py:219).
- [사실] 모니터 시각: 기본 AEST 8~23시 매 정시 16회(config.py:62-64), 00~07시는 없음(8시간 공백). 스케줄러가 폰 부하로 7~58분 늦게 깬 전력이 있어 grace 30분 적용(lighter_bot.py:21-31).
- [사실] 유일한 청산 관련 검사 = `_check_safety`(dca_engine.py:45-56): 잔고 < MIN_AVAILABLE_BALANCE(기본 30, config.py:67) 이면 스킵, **liq_dist < MIN_LIQ_DISTANCE_PCT(기본 5, config.py:66) 이면 DCA 스킵**. 매수 시작 전(119)과 retry 사이(226)에 검사. 즉 지금 봇은 청산이 가까울수록 **오히려 안 산다**(물타기와 반대 동작). 스킵 시 알림 문구만 나감(dca_engine.py:334-341).
- [사실] 이 검사는 cross(liq=0→999)는 항상 통과시킨다.

### 주문 금액·한도 검사 위치
- [사실] "일일 한도" 전용 코드는 없다(한도/daily/MAX 검색 결과: 스케줄 이름과 ORDER_MAX_RETRIES 뿐). 한도는 구조로만 보장됨: ① 종목당 하루 1회 × target_usdc, ② 한 호출 안에서 remaining_usdc = target - 누적체결(dca_engine.py:134,203), ③ 프로세스 내 동시 중복 방지 _active_symbols(23, 97-105), ④ PID 락으로 봇 중복 실행 방지(lighter_bot.py:58-104), ⑤ 취소 미확정·체결량 미확정이면 중단(194-214).
- [사실] 구멍: 수동 `/dca`는 횟수 제한이 없어 여러 번 누르면 그만큼 산다(동시 호출만 _active_symbols 가 막음). 재시작하면 _active_symbols 도 초기화.
- [사실] 테스트: tests/test_dca_engine.py(liq 관련 10곳), test_monitor.py(6), test_lighter_bot.py(스케줄 등록 확인, 265-270). 청산 근접 감지·알림·마진 추가 테스트는 없음.

## 2. 물타기 두 가지 비교

### SDK 확인 (B 가능 여부)
- [사실] 설치된 lighter-sdk 1.0.9 (이 PC, pip show) 의 signer_client.py 에 마진 추가/제거가 **있다**: `update_margin(market_index, usdc_amount: float, direction, ...)` (1341-1351), 서명 함수 sign_update_margin(623), 상수 `ISOLATED_MARGIN_REMOVE_COLLATERAL=0`, `ISOLATED_MARGIN_ADD_COLLATERAL=1`(282-283), `USDC_TICKER_SCALE=1e6`(249, 금액 6자리 정수화). 반환은 update_leverage 와 같은 (tx_info, api_response, err).
- [확인 못 함] 폰에 설치된 SDK 버전(requirements.txt:4 는 `lighter-sdk` 무핀). 폰에서 같은 메서드가 있는지, 실제 마진 추가 거래가 체결/거부되는 조건(최소 금액, cross 포지션에 호출하면 어떻게 되는지)은 호출해 보지 않았으므로 모름.
- [사실] 유지증거금률 값: SDK 모델에 필드만 있음(perps_order_book_detail.py:48-51 maintenance_margin_fraction 등, risk_parameters.py maintenance_margin_req). **실제 숫자와 단위는 코드/SDK 에 없음** → 아래 예시는 가정.

### 숫자 예 (가정 명시)
가정: Long, isolated, 마크가에서 청산가까지 5%(=현재 d). 유지증거금률 mmr = 0.5% [가정, 실제 종목별 값은 API maintenance_margin_fraction 로 읽어야 함]. 거리 d = (자기자본/명목가 - mmr) 로 근사(수수료·펀딩 무시). 현재 명목가 N, 따라서 자기자본/N = 5.5%. 추가 매수는 신규분에 초기증거금 1/L 가 붙고 마크가 체결이라고 [가정].

| 항목 | (A) 같은 방향 추가매수 ΔN=0.5N | (B) 담보만 추가 |
|---|---|---|
| 4x 결과 청산 거리 | (0.055N+0.125N)/1.5N - 0.005 = **11.5%** | 담보 C → 5% + C/N |
| 3x 결과 청산 거리 | (0.055N+0.1667N)/1.5N - 0.005 = **14.3%** | 레버리지와 무관 (같은 식) |
| 필요한 돈 (N=$1,000 예) | 4x: $125, 3x: $167 (증거금으로 묶임, 노출 +50%) | 10%로 만들려면 **$50**, 15%면 $100 |
| 같은 +6.5%p 를 얻는 데 | $125 (4x) | 약 $65 |
| 평단 | 내려감 (마크가 매수분만큼) | 변화 없음 |
| 포지션 크기/손실 민감도 | +50% 커짐 (같은 하락의 손실 1.5배) | 그대로 |
| 구현 난이도 | 기존 execute_dca 재사용 가능, 단 _check_safety 가 5% 미만에서 막으므로 우회 필요 | 신규 코드(update_margin 래퍼), SDK 검증 필요 |
| cross/liq=0 포지션 | 해당 없음 판정이라 트리거 자체가 없음 | isolated 전용 기능(상수 이름이 ISOLATED_*). cross 에는 쓸 수 없다고 봐야 함 [가정] |

해석: 청산 거리만 보면 B 가 같은 돈으로 더 멀리 민다. A 는 평단을 낮추지만 노출이 커져 다음 하락 때 더 빨리 다시 5%에 닿는다.

### 실패 모드
- 연속 하락, B: 가격이 5% 더 빠질 때마다 거리 유지에 약 5%N 이 다시 필요. 투입한 담보는 청산되면 같이 사라진다(isolated 라 그 포지션 담보 한도 내 손실). 잔고 바닥까지 반복 투입 가능 → 횟수·금액 상한 필수.
- 연속 하락, A: 노출이 매번 커짐(예: 0.5N씩 3번이면 N→3.4N). 잔고·종목 노출이 기하급수로 늘 수 있음. 기존 DCA(매일 target)와 겹치면 하루 투입이 target + 방어매수.
- 일일 한도: 위 1절대로 전용 한도 코드가 없어, 방어 투입은 DCA 와 합산 한도를 새로 만들어야 함. MONUSD 사고(9~10배)는 잔존 GTT·중복 실행이 원인이었고 지금 막는 장치(취소 확정, PID 락)는 방어 매수 경로에도 같은 방식으로 적용해야 함.
- API 지연: 확인 주기 사이 급락이면 늦음(아래 3). 조회 실패를 "안전"으로 삼키면 안 됨(lighter_client.py:279-283 주석과 같은 원칙).
- 거짓 신호: cross 포지션 liq=0 을 위험으로 오인(또는 그 반대로 아예 안 봄). 가격이 청산가 근처 일시 스파이크 후 복귀하면 불필요한 투입.

## 3. 감지 기준 후보
1. 청산가까지 남은 %: 기존 liq_dist(monitor.py:150) 재사용. 경고 단계(예: 10%)와 행동 단계(예: 6%)를 분리 [값은 가정, 정하는 건 싸장님]. 방향 보정 필요(Long 은 아래, Short 은 위. 현재 abs 로 뭉갬 monitor.py:164).
2. 마진 비율: 자기자본(allocated_margin+upnl) / 유지증거금. SDK risk_parameters(maintenance_margin_req, total_account_value)에 필드가 있으나 어느 엔드포인트가 주는지, 단위는 [확인 못 함].
3. liq=0(cross): 청산가 기준 감지 불가. 별도 기준(계정 전체 가용잔고 대비 포지션 가치 등)이 필요하거나 "감시 제외 + 알림에 cross 표시"로 명확히 처리. margin_mode 필드를 읽어 분기 권장.
4. 확인 주기: 현재 정시 1시간 단위·야간 8시간 공백·지연 최대 58분 전력. 청산 감지용이면 별도 반복 잡(PTB job_queue 의 반복 실행, 예: 1~5분)이 필요. 이 저장소에서 run_repeating 사용 여부는 [확인 못 함], 폰 부하 영향도 [확인 못 함]. 호출 부담: fetch_account 1회/주기(monitor.py:37-54, timeout 15초).
5. 지연: 1~5분 폴링이라도 급락 시 청산이 먼저일 수 있음. 따라서 임계값은 여유 있게, 그리고 물타기는 "방지"일 뿐 보장 아님.

## 4. 안전장치 필요 목록
- 1회 투입 상한(방어 1건당 $), 일 투입 상한(DCA + 방어 합산, 파일/DB 에 영속 저장 - 재시작해도 초기화되지 않게; 지금 _active_symbols 는 메모리뿐 dca_engine.py:23).
- 종목별 총 노출 상한(포지션 가치 또는 담보 합계 상한), 계정 전체 노출 상한.
- 같은 포지션 반복 투입 횟수 상한 + 투입 사이 최소 간격(쿨다운); 상한 도달 시 알림만.
- 잔고 최소선: 기존 MIN_AVAILABLE_BALANCE(30) 와 별개로 방어 전용 하한 유지(바닥까지 쓰지 않음).
- 기본값은 **알림만**, 자동 실행은 off. 수동 확인 모드: 텔레그램 인라인 버튼 "담보 $X 추가 / 취소"(기존 close_ask 패턴 lighter_bot.py:243-288, 소유자 검사 `_is_owner` 239). 자동 모드는 싸장님 명시 승인 후 별도 설정으로.
- 알림 중복 억제(같은 단계 재알림 쿨다운), 조회 실패는 "알 수 없음" 알림으로 처리하고 투입은 중단.
- 마진 추가 후 재조회로 liq 변화 확인(전/후 값 알림), 실패 시 재시도 금지(체결 불확실이면 중단 - dca_engine 원칙과 동일).
- 롱/숏 방향 확인, margin_mode 확인(isolated 만 B 허용), 종목 허용 목록(DCA_MARKETS 와 일치).
- 시험 계획(순서): ① 순수 함수(거리·단계 판정, 한도 계산) 단위 테스트: liq=0, Short, 경계값, 조회 실패. ② SDK 호출을 mock 한 update_margin 래퍼 테스트(기존 tests/test_lighter_client_close.py 방식). ③ 알림 전용 모드로 폰에서 며칠 관찰(주문 0건, 오탐률·지연 기록). ④ 실제 마진 추가는 소액($1~5 이하)·수동 확인 모드로 1회, 전/후 liquidation_price 비교. 최소 금액·거부 조건·SDK 폰 버전은 이 단계에서 확인.

## 5. 추천 설계 (한 줄)
1단계 "청산 근접 알림 전용"(방향 보정 liq_dist 반복 점검 + margin_mode 분기 + 텔레그램 버튼) → 2단계 isolated 한정 B(담보 추가)를 수동 확인 모드로, 상한 3종(1회·일·종목)과 횟수 상한 포함 → A(추가매수 자동)는 노출 증가 위험이 커서 마지막/보류. 현행 _check_safety 의 "5% 미만 DCA 스킵"은 그대로 두는 편이 안전.

## 6. 확인 못 한 것
- 폰의 SDK 버전과 update_margin 실동작, 마진 추가 최소 금액·거부 조건.
- 실제 종목별 유지증거금률(코드에 값 없음, 위 숫자는 0.5% 가정).
- 폰 포지션이 isolated 인지 cross 인지, Short 보유 여부.
- 신규 주문 시 isolated 증거금이 자동 할당되는지(예시 A 의 전제).
- 짧은 주기 반복 잡이 폰에서 얼마나 늦게 깨는지.
- 이 문서는 .env 를 읽지 않았으므로 현재 DCA 종목·금액·임계값 설정값은 모름.

---

## Lighter 청산 규칙(공식 문서) - 11-13 추가 조사 (싸장님 17283)

원문 출처 (WebFetch 로 확인, 요약 표현은 "paraphrase"):
- 청산·LLP: https://docs.lighter.xyz/trading/liquidations-and-llp-insurance-fund.md
- 증거금 수치표(계약 사양): https://docs.lighter.xyz/trading/contract-specifications.md
- 마크가: https://docs.lighter.xyz/trading/fair-price-marking.md
- 멀티에셋 마진(건강도 판정식): https://docs.lighter.xyz/trading/multi-asset-margin.md
- (docs.lighter.xyz/perpetual-futures/... 경로는 404 였음)

### 규칙 요지
1. 증거금 3단계: 청산마진(CMR) < 유지증거금(MMR) < 초기증거금(IMR). 레버리지 = 1/IMR. 예시 표(계약 사양 페이지): NVDA·XRP 20x IMR 5% / MMR 3% / CMR 2%, WIF·AVAX 10x 10/6/4%, TAO 5x 20/12/8%, ZORA 3x 33.33/20/13.33%. 표 안에서 MMR=IMR의 60%, CMR=IMR의 40% (이는 표를 보고 읽은 것이지 페이지가 규칙으로 말한 건 아님). 구간(티어)별 증거금 설명은 없음. 이 봇의 대상 종목(SK하이닉스 등) 값은 표에서 확인 못 함 -> 실제 값은 orderBookDetails 의 maintenance_margin_fraction 등으로 읽어야 함(SDK perps_order_book_detail.py:48-51).
2. 단계: 계정가치 < IMR 이고 >= MMR 이면 "pre-liquidation"(포지션 늘리는 주문·건강도 악화 주문 불가). MMR > 계정가치 >= CMR 이면 **부분 청산**: 청산엔진이 미체결 주문을 먼저 전부 취소하고 IoC 주문으로 포지션을 줄이며, 유지증거금 위로 회복되면 멈춤. 수수료 최대 1%를 LLP(보험기금)로 보냄. (포지션 줄이는 "양"의 정확한 규칙은 원문에서 확인 못 함.)
3. 계정가치 < CMR 이면 **전체 청산**: LLP 가 포지션을 인수(미실현손익 오름차순, 단 LLP 계정가치가 IMR 이상 유지될 때만). 계정가치가 음수이고 LLP 가 못 메우면 ADL(반대 포지션과 상쇄).
4. 판정 가격은 마크가(마크가 = 호가 충격가·지수+프리미엄 EMA·타 거래소 마크 중앙값, 지수는 오라클). 최근 체결가가 아님. 마크는 보정 가격이라 앱 표시가와 다를 수 있음(paraphrase).
5. 격리 vs 교차: 격리 포지션은 자기 담보(AllocatedMargin)를 가진 별도 계정처럼 취급. 교차는 계정 전체 가치로 판정. 건강도 판정식은 TALT 기준으로 MMR/CMR 비교(multi-asset-margin 페이지). 격리 전용 시장이 있음(market_margin_mode=1).

### 청산가 오기 전에 청산/부분청산이 나는 이유
- 부분 청산은 CMR 이 아니라 **MMR 아래로 내려가는 순간** 시작한다. 예: 5x 시장은 MMR 12% - 포지션 담보(포지션가치 대비 20%)가 12%로 줄면 이미 부분 청산 구간.
- API 의 liquidation_price 가 어느 단계 기준인지(MMR 선인지 CMR 선인지) **원문에서 확인 못 함**. 문서의 zero price 식(마크 x (1 - M x TALT/MMR))은 그것과 다른 개념으로 보임. 따라서 "표시된 청산가"를 부분 청산 시작점으로 믿으면 안 되고, 직접 계산한 MMR 선을 쓰는 게 안전.
- 봇의 현재가는 value/size 역산(monitor.py:144), 거래소는 마크가로 판정. 두 값이 같다는 확인 못 함 (position_value 가 마크 기준인지 문서에서 확인 못 함). 마크는 체결가와 차이 날 수 있고 이 차이가 느리게 갱신되는 정시 알림에서는 안 보임.
- 펀딩·수수료로 담보가 줄어 청산 기준이 움직이는지: 문서 원문에서 직접 확인 못 함(일반적으로 계정 가치에 반영되는 것으로 보이나 [가정]). 청산 수수료는 부분 청산 시 최대 1%로 확인됨.
- 미체결 주문은 청산 시 먼저 취소됨 - 봇의 지정가 DCA 주문이 남아 있다면 청산 시점에 취소될 수 있음.

### SDK 의 청산/건강도 조회 (설치 1.0.9)
- 있음: AccountApi.liquidations(account_index, limit<=100, auth, market_id, cursor) (account_api.py:3596) -> LiquidationInfos{liquidations[], next_cursor}. 각 항목 Liquidation{id, market_id, type, trade{price,size,fee,time}, info{positions, risk_info_before, risk_info_after, mark_prices}, executed_at} (models/liquidation*.py). type 값 목록은 확인 못 함. 인증 토큰 필요(봇의 get_auth_token 재사용 가능).
- risk_info = {cross_risk_parameters, isolated_risk_parameters[]}, 각각 {market_id, collateral, total_account_value, initial_margin_req, maintenance_margin_req, close_out_margin_req} (models/risk_info.py:30-31, risk_parameters.py:29-34). 즉 **health 계산 재료가 API 모델에 존재**. 다만 이 필드는 위 "청산 이력"에서만 쓰이고, 일반 계정 조회(/account)의 포지션 모델(account_position.py)에는 allocated_margin·imf·liquidation_price 만 있음. 라이브 계정 조회가 risk_info 를 주는지 = 확인 못 함.
- account_limits(account_api.py:373)는 이름상 한도 조회로, 건강도 용도는 확인 못 함.
- 결론: 증거금 비율(health)을 직접 받는 라이브 엔드포인트는 확인 못 함. 대신 봇이 계산 가능: 자기자본 = allocated_margin + 미실현손익, 필요액 = |포지션가치| x MMR(시장별 값). health = 자기자본 / (포지션가치 x MMR). 임계값은 1.0 이 부분 청산 시작점.

### 수정된 감지 기준·설계
- 감지 기준을 청산가 거리 %에서 **health 비율(자기자본 / 유지증거금)** 으로 바꾸는 것을 권장. 청산가 필드는 단계 기준이 불명확하고 cross 는 0. 거리 % 는 보조 표시로 둠. health 는 cross 포지션도 계정 단위로 계산 가능성이 있으나(계정 전체 TAV/MMR) 라이브 필드가 없어 [확인 못 함].
- 임계값은 부분 청산 시작점(health 1.0)보다 앞에: 경고 약 1.5, 행동(담보 제안) 약 1.25 [가정, 싸장님이 정함. 급락 폭과 감시 주기 지연을 감안해 넓게]. 예: 주기 지연 동안 마크가가 X% 빠질 수 있으면 health 여유는 최소 X% x 레버리지분 이상.
- 이전 5% 거리 기준은 5x 시장(MMR 12%)에서는 부분 청산이 이미 시작된 뒤일 수 있음: 문서 표 기준 유지증거금이 크기 때문. 현재 `_check_safety`의 5% 스킵값은 이 기준과 맞지 않으니 재검토 대상.
- B(담보 추가) 우선 유지: health 의 분자(자기자본)만 올리므로 포지션/MMR 요구는 그대로. 추가 필요 담보 = 목표 health x 포지션가치 x MMR - 현재 자기자본 (가정 아닌 계산식, MMR 값만 API 필요). A(추가매수)는 포지션가치가 늘어 MMR 요구도 같이 늘고, 문서상 pre-liquidation(계정가치 < IMR) 구간에서는 **포지션을 늘리는 주문이 거부**될 수 있어 위기 때 A 는 아예 안 먹힐 가능성이 있음. 따라서 위기 방어는 B, A 는 평상시 DCA 로만.
- 청산 시 미체결 주문이 취소되므로 방어 중에 지정가가 남아 있으면 상태가 바뀔 수 있음 - 방어 직전 해당 시장 미체결 확인.
- 시험: 위 4절 계획에 더해 liquidations() 로 과거 청산 이력을 읽기 전용으로 조회해 risk_info_before 의 비율을 실제 청산 시점 값과 대조(포지션 있는 계정에 대해, 폰에서 실행, 주문 없음). type 값(부분/전체)과 시작 health 를 확인하는 용도.

### 이 절에서 확인 못 한 것
- API liquidation_price 가 MMR 선인지 CMR 선인지, 부분 청산 1회당 줄이는 양, 펀딩 반영 방식, 봇 대상 종목들의 MMR 수치, 라이브 계정 응답의 risk_info 유무, position_value 가 마크 기준인지.
