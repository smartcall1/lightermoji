# Lighter DCA Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lighter.xyz 전용 텔레그램 봇 — 포지션 모니터링 + 종목별 일일 DCA 지정가 매수 (가격 추격 로직 포함)

**Architecture:** 단일 `python bot.py` 프로세스. python-telegram-bot job_queue로 모니터링(정시 발송)과 DCA(매일 1회)를 스케줄링. lighter-sdk로 주문 서명 후 REST API로 전송. 기존 market_dashboard_bot에서 Lighter 관련 코드를 분리 제거.

**Tech Stack:** Python 3.10+, python-telegram-bot[job-queue]>=21.0, httpx, lighter-sdk, python-dotenv

---

## 파일 구조

```
D:\Codes\lighter_dca_bot\
├── bot.py              # 진입점: Telegram Application + 명령어 핸들러 + 스케줄 잡
├── config.py           # .env 로더, DCA 설정 파싱
├── monitor.py          # Lighter REST 읽기 + 포지션 포맷 (lighter_monitor 로직 이식)
├── lighter_client.py   # 주문 서명/전송 (SignerClient + TransactionApi)
├── dca_engine.py       # DCA 실행 루프: 지정가 → 가격 추격 → 전량 체결
├── tests/
│   ├── test_config.py
│   ├── test_monitor.py
│   └── test_dca_engine.py
├── .env.example
├── requirements.txt
└── .gitignore

D:\Codes\market_dashboard_bot\  (수정)
├── bot.py              # lighter 관련 코드 제거
├── lighter_status.py   # 삭제
└── requirements.txt    # lighter-sdk 제거
```

---

### Task 1: 프로젝트 스캐폴드

**Files:**
- Create: `D:\Codes\lighter_dca_bot\requirements.txt`
- Create: `D:\Codes\lighter_dca_bot\.env.example`
- Create: `D:\Codes\lighter_dca_bot\.gitignore`

- [ ] **Step 1: requirements.txt 작성**

```
python-telegram-bot[job-queue]>=21.0
httpx>=0.27.0
python-dotenv>=1.0.0
lighter-sdk
```

- [ ] **Step 2: .env.example 작성**

```env
# ── Telegram ─────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_new_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# ── Lighter 인증 ──────────────────────────────────────────────
# lighter.xyz → Settings → API Keys에서 발급
LIGHTER_WALLET=0x0000000000000000000000000000000000000000
LIGHTER_ACCOUNT_INDEX=    # 비워두면 지갑주소로 자동 조회
LIGHTER_API_KEY_INDEX=0   # 정수 (0~254)
LIGHTER_API_PRIVATE_KEY=  # Lighter UI에서 발급한 API 개인키

# ── DCA 설정 (종목별 USDC 매수 금액, 주석처리=비활성화) ───────
DCA_SKHYNIXUSD=50
# DCA_NVDAUSD=30
# DCA_TSLAUSD=20

# ── 스케줄 (AEST = UTC+10) ────────────────────────────────────
DCA_TIME_AEST=09:00
MONITOR_HOURS_AEST=8,12,16,20

# ── 안전장치 ──────────────────────────────────────────────────
MIN_LIQ_DISTANCE_PCT=5     # 청산가 여유 최소 %
MIN_AVAILABLE_BALANCE=30   # 가용 잔고 최소 USDC

# ── 주문 실행 튜닝 ────────────────────────────────────────────
ORDER_RETRY_INTERVAL_SEC=30
ORDER_PRICE_STEP_PCT=0.05
ORDER_MAX_RETRIES=20
```

- [ ] **Step 3: .gitignore 작성**

```
.env
__pycache__/
*.pyc
*.pyo
.pytest_cache/
```

- [ ] **Step 4: tests 디렉토리 생성**

```bash
mkdir D:\Codes\lighter_dca_bot\tests
```

- [ ] **Step 5: 패키지 설치 확인**

```bash
cd D:\Codes\lighter_dca_bot
pip install -r requirements.txt
```

Expected: 모든 패키지 설치 성공

- [ ] **Step 6: Commit**

```bash
git init
git add requirements.txt .env.example .gitignore
git commit -m "feat: init lighter_dca_bot project scaffold"
```

---

### Task 2: config.py + 테스트

**Files:**
- Create: `D:\Codes\lighter_dca_bot\config.py`
- Create: `D:\Codes\lighter_dca_bot\tests/test_config.py`

- [ ] **Step 1: 테스트 먼저 작성 (test_config.py)**

```python
import os
import pytest
from unittest.mock import patch

# config.py가 존재하지 않으면 ImportError — 정상
def test_dca_config_parsing():
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "123",
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "2",
        "LIGHTER_API_PRIVATE_KEY": "privkey",
        "DCA_SKHYNIXUSD": "50",
        "DCA_NVDAUSD": "30",
        "DCA_TIME_AEST": "09:00",
        "MONITOR_HOURS_AEST": "8,12,16",
        "MIN_LIQ_DISTANCE_PCT": "5",
        "MIN_AVAILABLE_BALANCE": "30",
        "ORDER_RETRY_INTERVAL_SEC": "30",
        "ORDER_PRICE_STEP_PCT": "0.05",
        "ORDER_MAX_RETRIES": "20",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib, config
        importlib.reload(config)
        assert config.DCA_MARKETS == {"SKHYNIXUSD": 50.0, "NVDAUSD": 30.0}
        assert config.DCA_TIME_AEST == (9, 0)
        assert config.MONITOR_HOURS_AEST == [8, 12, 16]
        assert config.MIN_LIQ_DISTANCE_PCT == 5.0
        assert config.MIN_AVAILABLE_BALANCE == 30.0
        assert config.ORDER_RETRY_INTERVAL_SEC == 30
        assert config.ORDER_PRICE_STEP_PCT == 0.05
        assert config.ORDER_MAX_RETRIES == 20

def test_dca_config_no_markets():
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "123",
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "0",
        "LIGHTER_API_PRIVATE_KEY": "pk",
        "DCA_TIME_AEST": "09:00",
        "MONITOR_HOURS_AEST": "8,12",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib, config
        importlib.reload(config)
        assert config.DCA_MARKETS == {}
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd D:\Codes\lighter_dca_bot
python -m pytest tests/test_config.py -v
```

Expected: ModuleNotFoundError (config.py 없음)

- [ ] **Step 3: config.py 작성**

```python
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID: str = os.environ["TELEGRAM_CHAT_ID"]

LIGHTER_WALLET: str = os.environ["LIGHTER_WALLET"]
LIGHTER_ACCOUNT_INDEX: int | None = (
    int(os.environ["LIGHTER_ACCOUNT_INDEX"])
    if os.environ.get("LIGHTER_ACCOUNT_INDEX", "").strip()
    else None
)
LIGHTER_API_KEY_INDEX: int = int(os.environ["LIGHTER_API_KEY_INDEX"])
LIGHTER_API_PRIVATE_KEY: str = os.environ["LIGHTER_API_PRIVATE_KEY"]

def _parse_dca_markets() -> dict[str, float]:
    result = {}
    for key, val in os.environ.items():
        if key.startswith("DCA_") and key != "DCA_TIME_AEST":
            symbol = key[4:]  # "DCA_SKHYNIXUSD" → "SKHYNIXUSD"
            try:
                amount = float(val)
                if amount > 0:
                    result[symbol] = amount
            except ValueError:
                pass
    return result

DCA_MARKETS: dict[str, float] = _parse_dca_markets()

def _parse_time(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)

DCA_TIME_AEST: tuple[int, int] = _parse_time(os.getenv("DCA_TIME_AEST", "09:00"))
MONITOR_HOURS_AEST: list[int] = [
    int(h.strip()) for h in os.getenv("MONITOR_HOURS_AEST", "8,12,16,20").split(",")
]

MIN_LIQ_DISTANCE_PCT: float = float(os.getenv("MIN_LIQ_DISTANCE_PCT", "5"))
MIN_AVAILABLE_BALANCE: float = float(os.getenv("MIN_AVAILABLE_BALANCE", "30"))
ORDER_RETRY_INTERVAL_SEC: int = int(os.getenv("ORDER_RETRY_INTERVAL_SEC", "30"))
ORDER_PRICE_STEP_PCT: float = float(os.getenv("ORDER_PRICE_STEP_PCT", "0.05"))
ORDER_MAX_RETRIES: int = int(os.getenv("ORDER_MAX_RETRIES", "20"))

BASE_URL: str = "https://mainnet.zklighter.elliot.ai"
API_BASE: str = f"{BASE_URL}/api/v1"
AEST_OFFSET: int = 10  # UTC+10

HEADERS: dict[str, str] = {
    "Origin": "https://app.lighter.xyz",
    "Referer": "https://app.lighter.xyz/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/131.0.0.0",
}
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
python -m pytest tests/test_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: add config loader with DCA market parsing"
```

---

### Task 3: monitor.py + 테스트

**Files:**
- Create: `D:\Codes\lighter_dca_bot\monitor.py`
- Create: `D:\Codes\lighter_dca_bot\tests/test_monitor.py`

- [ ] **Step 1: 테스트 작성 (test_monitor.py)**

```python
from monitor import parse_positions, format_position_message, fmt_price

SAMPLE_ACCOUNT = {
    "available_balance": "1000.5",
    "total_asset_value": "5000.0",
    "positions": [
        {
            "symbol": "SKHYNIXUSD",
            "position": "0.5",
            "avg_entry_price": "600.0",
            "position_value": "320.0",
            "unrealized_pnl": "20.0",
            "liquidation_price": "400.0",
            "allocated_margin": "100.0",
            "initial_margin_fraction": "10",  # 10 = 10% = 10x (API 단위: %, formula: round(100/imf))
            "total_funding_paid_out": "1.5",
            "sign": 1,
            "open_order_count": 0,
        }
    ],
    "shares": [],
    "_pool_details": [],
}

def test_parse_positions_basic():
    positions = parse_positions(SAMPLE_ACCOUNT)
    assert len(positions) == 1
    p = positions[0]
    assert p["symbol"] == "SKHYNIXUSD"
    assert p["side"] == "Long"
    assert p["size"] == 0.5
    assert p["entry"] == 600.0
    assert p["upnl"] == 20.0
    assert p["leverage"] == 10

def test_parse_positions_skips_zero():
    account = {"positions": [{"position": "0", "symbol": "NVDAUSD"}], "shares": []}
    assert parse_positions(account) == []

def test_fmt_price():
    assert fmt_price(1234.5) == "$1,235"
    assert fmt_price(50.12) == "$50.1"
    assert fmt_price(5.678) == "$5.68"

def test_format_message_has_symbol():
    msg = format_position_message(SAMPLE_ACCOUNT, [parse_positions(SAMPLE_ACCOUNT)[0]])
    assert "SK하이닉스" in msg or "SKHYNIXUSD" in msg
    assert "Long" in msg or "L10x" in msg
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
python -m pytest tests/test_monitor.py -v
```

Expected: ModuleNotFoundError

- [ ] **Step 3: monitor.py 작성 (lighter_monitor.py 로직 이식)**

```python
"""Lighter 포지션 조회 및 포맷 — REST 읽기 전용 (인증 불필요)"""

import httpx
from datetime import datetime, timezone, timedelta
from config import API_BASE, LIGHTER_WALLET, AEST_OFFSET

AEST = timezone(timedelta(hours=AEST_OFFSET))

HEADERS = {
    "Origin": "https://app.lighter.xyz",
    "Referer": "https://app.lighter.xyz/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/131.0.0.0",
}

SYMBOL_NAMES: dict[str, str] = {
    "SKHYNIXUSD": "SK하이닉스",
    "SAMSUNGUSD": "삼성전자",
    "HYUNDAIUSD": "현대차",
    "NVDAUSD": "NVIDIA",
    "TSLAUSD": "Tesla",
    "GOOGLUSD": "Google",
    "MSFTUSD": "Microsoft",
    "AMZNUSD": "Amazon",
    "AAPLUSD": "Apple",
    "AMDUSD": "AMD",
    "METAUSD": "Meta",
    "COINUSD": "Coinbase",
}


async def fetch_account(client: httpx.AsyncClient | None = None) -> dict | None:
    async def _fetch(c: httpx.AsyncClient) -> dict | None:
        r = await c.get(
            f"{API_BASE}/account",
            params={"by": "l1_address", "value": LIGHTER_WALLET},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        accounts = r.json().get("accounts", [])
        return accounts[0] if accounts else None

    if client:
        return await _fetch(client)
    async with httpx.AsyncClient() as c:
        return await _fetch(c)


async def fetch_lit_price(client: httpx.AsyncClient) -> float | None:
    try:
        r = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "lighter", "vs_currencies": "usd"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("lighter", {}).get("usd")
    except Exception:
        pass
    return None


async def fetch_pool_meta(client: httpx.AsyncClient, pool_index: int) -> dict | None:
    try:
        r = await client.get(
            f"{API_BASE}/publicPoolsMetadata",
            params={"index": pool_index + 1, "limit": 1},
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        pools = r.json().get("public_pools", [])
        if pools and pools[0].get("account_index") == pool_index:
            return pools[0]
    except Exception:
        pass
    return None


def parse_positions(account: dict) -> list[dict]:
    results = []
    for p in account.get("positions", []):
        size = float(p.get("position", "0"))
        if size == 0:
            continue
        symbol = p.get("symbol", "?")
        entry = float(p.get("avg_entry_price", "0"))
        value = float(p.get("position_value", "0"))
        upnl = float(p.get("unrealized_pnl", "0"))
        liq = float(p.get("liquidation_price", "0"))
        margin = float(p.get("allocated_margin", "0"))
        imf = float(p.get("initial_margin_fraction", "0"))
        funding = float(p.get("total_funding_paid_out", "0") or "0")
        side = "Long" if p.get("sign", 1) == 1 else "Short"
        orders = int(p.get("open_order_count", 0))

        current = value / size if size else entry
        pnl_pct = (upnl / (entry * size) * 100) if entry * size else 0
        leverage = round(100 / imf) if imf > 0 else 1
        liq_dist = ((liq - current) / current * 100) if current and liq > 0 else 0

        results.append({
            "symbol": symbol,
            "name": SYMBOL_NAMES.get(symbol, symbol.replace("USD", "")),
            "side": side,
            "size": size,
            "entry": entry,
            "current": current,
            "value": value,
            "upnl": upnl,
            "pnl_pct": pnl_pct,
            "liq": liq,
            "liq_dist": abs(liq_dist),
            "margin": margin,
            "leverage": leverage,
            "funding": funding,
            "orders": orders,
        })
    return sorted(results, key=lambda x: abs(x["value"]), reverse=True)


def fmt_price(v: float) -> str:
    if abs(v) >= 1000:
        return f"${v:,.0f}"
    if abs(v) >= 100:
        return f"${v:,.1f}"
    return f"${v:,.2f}"


def format_position_message(account: dict, positions: list[dict]) -> str:
    now = datetime.now(AEST).strftime("%m/%d %H:%M")
    balance = float(account.get("available_balance", "0"))
    total_value = float(account.get("total_asset_value", "0"))
    total_upnl = sum(p["upnl"] for p in positions)
    total_margin = sum(p["margin"] for p in positions)

    lines = [f"⚡ Lighter — {now} AEST"]

    if not positions:
        lines += ["", "활성 포지션 없음"]
    else:
        for p in positions:
            pnl_e = "🟢" if p["upnl"] >= 0 else "🔴"
            d = "L" if p["side"] == "Long" else "S"
            order_tag = f" 📋{p['orders']}" if p["orders"] > 0 else ""
            lines += [
                "",
                f"{'📈' if d == 'L' else '📉'} {p['name']} {d}{p['leverage']}x{order_tag}",
                f"{fmt_price(p['entry'])}→{fmt_price(p['current'])} | {p['size']}주 {fmt_price(p['value'])}",
                f"{pnl_e} {p['upnl']:+,.1f} ({p['pnl_pct']:+.1f}%) ⚠️{fmt_price(p['liq'])}",
            ]

    lines.append("─────────────────")
    pnl_e = "🟢" if total_upnl >= 0 else "🔴"
    lines.append(f"{pnl_e} PnL ${total_upnl:+,.1f} | 마진 ${total_margin:,.0f}")
    lines.append(f"💰 가용 ${balance:,.0f} | 총 ${total_value:,.0f}")

    pool_details = account.get("_pool_details", [])
    if pool_details:
        lines.append("─────────────────")
        total_equity = sum(p["equity"] for p in pool_details)
        total_lp_pnl = sum(p["lp_pnl"] for p in pool_details)
        lp_e = "🟢" if total_lp_pnl >= 0 else "🔴"
        lines.append(f"🏦 LP ${total_equity:,.0f} ({lp_e}${total_lp_pnl:+,.0f})")
        for pd in pool_details:
            apy_str = f" {pd['apy']:+.1f}%" if pd.get("apy") is not None else ""
            pnl_str = f" ({pd['lp_pnl']:+,.0f})" if pd["lp_pnl"] != 0 else ""
            name = pd["name"].replace("Lighter Liquidity Provider (LLP)", "LLP")
            lines.append(f"  {name} ${pd['equity']:,.0f}{pnl_str}{apy_str}")

    return "\n".join(lines)


async def get_full_status() -> str:
    """포지션 + LP 풀 포함 전체 현황 메시지 반환."""
    async with httpx.AsyncClient() as client:
        account = await fetch_account(client)
        if not account:
            return "❌ Lighter 계정 조회 실패"

        lit_price = await fetch_lit_price(client)
        pool_details = []
        for s in account.get("shares", []):
            principal = float(s.get("principal_amount", "0"))
            if principal == 0:
                continue
            pool_idx = s.get("public_pool_index", 0)
            my_shares = int(s.get("shares_amount", 0))
            entry_usdc = s.get("entry_usdc", "0")
            meta = await fetch_pool_meta(client, pool_idx)
            name = (meta.get("name") or "$LIT Staking") if meta else "$LIT Staking"
            apy = float(meta["annual_percentage_yield"]) if meta and meta.get("annual_percentage_yield") else None
            tav = float(meta["total_asset_value"]) if meta and meta.get("total_asset_value") else 0
            total_shares = int(meta.get("total_shares", 0)) if meta else 0
            is_lit = entry_usdc == "0" and not meta
            equity = (principal * lit_price) if (is_lit and lit_price) else (
                (my_shares / total_shares) * tav if total_shares else principal
            )
            pool_details.append({
                "name": name,
                "principal": principal,
                "equity": equity,
                "lp_pnl": equity - principal,
                "apy": apy,
            })

        account["_pool_details"] = sorted(pool_details, key=lambda x: x["principal"], reverse=True)
        positions = parse_positions(account)
        return format_position_message(account, positions)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
python -m pytest tests/test_monitor.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor.py
git commit -m "feat: add position monitor (ported from lighter_monitor.py)"
```

---

### Task 4: lighter_client.py — 읽기 + 주문 API

**Files:**
- Create: `D:\Codes\lighter_dca_bot\lighter_client.py`

- [ ] **Step 1: lighter_client.py 작성**

```python
"""Lighter API 클라이언트 — 읽기(REST) + 쓰기(SDK 서명)"""

import time
import logging
import httpx
import lighter

from config import (
    BASE_URL, API_BASE, HEADERS,
    LIGHTER_WALLET, LIGHTER_ACCOUNT_INDEX,
    LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY,
)

log = logging.getLogger(__name__)

# ── 마켓 정보 캐시 (symbol → dict) ────────────────────────────
_market_cache: dict[str, dict] = {}


async def fetch_account_index() -> int:
    """지갑 주소로 Lighter account_index 자동 조회."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{API_BASE}/account",
            params={"by": "l1_address", "value": LIGHTER_WALLET},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        accounts = r.json().get("accounts", [])
        if not accounts:
            raise RuntimeError(f"지갑 {LIGHTER_WALLET}에 Lighter 계정 없음")
        return int(accounts[0]["account_index"])


async def get_account_index() -> int:
    if LIGHTER_ACCOUNT_INDEX is not None:
        return LIGHTER_ACCOUNT_INDEX
    return await fetch_account_index()


async def fetch_market_info(symbol: str) -> dict:
    """심볼 → {market_id, price_decimals, size_decimals, min_base_amount} 반환. 캐시됨."""
    if symbol in _market_cache:
        return _market_cache[symbol]

    base_symbol = symbol.replace("USD", "").replace("USDC", "")
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API_BASE}/orderBooks", headers=HEADERS, timeout=15)
        r.raise_for_status()
        books = r.json().get("order_books", [])

    for book in books:
        if book.get("symbol", "").upper() == base_symbol.upper():
            info = {
                "market_id": int(book["market_id"]),
                "price_decimals": int(book.get("supported_price_decimals", 2)),
                "size_decimals": int(book.get("supported_size_decimals", 4)),
                "min_base_amount": float(book.get("min_base_amount", "0.001")),
            }
            _market_cache[symbol] = info
            return info

    raise ValueError(f"마켓 '{symbol}' (base: '{base_symbol}') 를 찾을 수 없음. 사용 가능: {[b.get('symbol') for b in books]}")


def encode_price(price_float: float, price_decimals: int) -> int:
    return int(round(price_float * (10 ** price_decimals)))


def encode_amount(amount_float: float, size_decimals: int) -> int:
    return int(round(amount_float * (10 ** size_decimals)))


def decode_amount(amount_int: int, size_decimals: int) -> float:
    return amount_int / (10 ** size_decimals)


def _make_signer(account_index: int) -> lighter.SignerClient:
    return lighter.SignerClient(
        url=BASE_URL,
        api_private_keys={LIGHTER_API_KEY_INDEX: LIGHTER_API_PRIVATE_KEY},
        account_index=account_index,
    )


def _make_tx_api() -> lighter.TransactionApi:
    config = lighter.Configuration(host=BASE_URL)
    api_client = lighter.ApiClient(configuration=config)
    return lighter.TransactionApi(api_client)


async def place_limit_buy(
    market_id: int,
    base_amount_float: float,
    price_float: float,
    price_decimals: int,
    size_decimals: int,
    client_order_index: int,
    account_index: int,
) -> tuple[str | None, str | None]:
    """지정가 매수 주문 전송. (tx_hash, error_msg) 반환."""
    signer = _make_signer(account_index)
    tx_api = _make_tx_api()

    price_int = encode_price(price_float, price_decimals)
    amount_int = encode_amount(base_amount_float, size_decimals)

    tx_type, tx_info, tx_hash, err = signer.sign_create_order(
        market_index=market_id,
        client_order_index=client_order_index,
        base_amount=amount_int,
        price=price_int,
        is_ask=False,  # False = 매수(bid)
        order_type=signer.ORDER_TYPE_LIMIT,
        time_in_force=signer.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
        reduce_only=False,
        trigger_price=0,
        api_key_index=LIGHTER_API_KEY_INDEX,
    )

    if err:
        return None, f"서명 실패: {err}"

    try:
        resp = tx_api.send_tx(body={"tx_type": tx_type, "tx_info": tx_info})
        log.info("주문 전송 완료: tx_hash=%s", tx_hash)
        return tx_hash, None
    except Exception as e:
        return None, f"전송 실패: {e}"


async def fetch_tx_order_index(tx_hash: str) -> int | None:
    """tx_hash → Lighter order_index 조회 (취소 시 필요)."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{API_BASE}/tx",
                params={"tx_hash": tx_hash},
                headers=HEADERS,
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                # 응답 구조: {"tx": {"order_index": N, ...}} — 실제 필드명은 SDK 확인 필요
                tx_data = data.get("tx") or data.get("transaction") or {}
                order_index = tx_data.get("order_index") or tx_data.get("index")
                return int(order_index) if order_index is not None else None
        except Exception as e:
            log.warning("tx 조회 실패: %s", e)
    return None


async def cancel_order(
    market_id: int,
    order_index: int,
    account_index: int,
) -> bool:
    """지정 order_index 취소. 성공 여부 반환."""
    signer = _make_signer(account_index)
    tx_api = _make_tx_api()

    tx_type, tx_info, tx_hash, err = signer.sign_cancel_order(
        market_index=market_id,
        order_index=order_index,
        api_key_index=LIGHTER_API_KEY_INDEX,
    )
    if err:
        log.error("취소 서명 실패: %s", err)
        return False

    try:
        tx_api.send_tx(body={"tx_type": tx_type, "tx_info": tx_info})
        log.info("주문 취소 완료: order_index=%d", order_index)
        return True
    except Exception as e:
        log.error("취소 전송 실패: %s", e)
        return False
```

- [ ] **Step 2: 임포트 경로 확인**

`monitor.py`는 이미 `from config import API_BASE, LIGHTER_WALLET, AEST_OFFSET, HEADERS` 로 import하므로 별도 조치 불필요 (HEADERS는 config.py Task 2에서 정의됨).

- [ ] **Step 3: 임포트 검증**

```bash
cd D:\Codes\lighter_dca_bot
python -c "import lighter_client; print('OK')"
```

Expected: OK

- [ ] **Step 4: Commit**

```bash
git add config.py lighter_client.py monitor.py
git commit -m "feat: add lighter_client with order signing and market info"
```

---

### Task 5: dca_engine.py + 테스트

**Files:**
- Create: `D:\Codes\lighter_dca_bot\dca_engine.py`
- Create: `D:\Codes\lighter_dca_bot\tests/test_dca_engine.py`

- [ ] **Step 1: 테스트 작성 (test_dca_engine.py)**

```python
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from dca_engine import _check_safety, DCASkipReason, _calc_order_price

SAMPLE_POSITION = {
    "symbol": "SKHYNIXUSD",
    "side": "Long",
    "size": 0.5,
    "current": 600.0,
    "liq": 400.0,
    "liq_dist": 33.3,  # (600-400)/600 * 100
    "upnl": 20.0,
    "pnl_pct": 6.7,
    "leverage": 10,
    "value": 300.0,
    "entry": 564.0,
    "margin": 60.0,
    "funding": 0.0,
    "name": "SK하이닉스",
    "orders": 0,
}

SAMPLE_ACCOUNT = {
    "available_balance": "500.0",
    "total_asset_value": "5000.0",
    "positions": [],
    "_pool_details": [],
}

@pytest.mark.asyncio
async def test_safety_check_passes():
    reason = _check_safety(SAMPLE_ACCOUNT, SAMPLE_POSITION, min_liq_pct=5.0, min_balance=30.0)
    assert reason is None

@pytest.mark.asyncio
async def test_safety_check_low_balance():
    account = {**SAMPLE_ACCOUNT, "available_balance": "20.0"}
    reason = _check_safety(account, SAMPLE_POSITION, min_liq_pct=5.0, min_balance=30.0)
    assert reason == DCASkipReason.LOW_BALANCE

@pytest.mark.asyncio
async def test_safety_check_liq_too_close():
    pos = {**SAMPLE_POSITION, "liq_dist": 3.0}
    reason = _check_safety(SAMPLE_ACCOUNT, pos, min_liq_pct=5.0, min_balance=30.0)
    assert reason == DCASkipReason.LIQ_TOO_CLOSE

@pytest.mark.asyncio
async def test_safety_check_no_position():
    # 포지션 없을 때 청산가 체크 스킵
    reason = _check_safety(SAMPLE_ACCOUNT, None, min_liq_pct=5.0, min_balance=30.0)
    assert reason is None

def test_calc_order_price_retry_0():
    base = 600.0
    price = _calc_order_price(base_price=base, retry=0, step_pct=0.05)
    assert price == pytest.approx(600.3, rel=1e-3)  # 600 * 1.0005

def test_calc_order_price_retry_5():
    base = 600.0
    price = _calc_order_price(base_price=base, retry=5, step_pct=0.05)
    assert price == pytest.approx(601.5, rel=1e-3)  # 600 * 1.0025
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pip install pytest pytest-asyncio
python -m pytest tests/test_dca_engine.py -v
```

Expected: ModuleNotFoundError

- [ ] **Step 3: dca_engine.py 작성**

```python
"""DCA 실행 엔진 — 지정가 주문 + 가격 추격 루프"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum

from monitor import fetch_account, parse_positions, fmt_price, SYMBOL_NAMES
from lighter_client import (
    fetch_market_info, place_limit_buy,
    fetch_tx_order_index, cancel_order, get_account_index,
)
from config import (
    MIN_LIQ_DISTANCE_PCT, MIN_AVAILABLE_BALANCE,
    ORDER_RETRY_INTERVAL_SEC, ORDER_PRICE_STEP_PCT, ORDER_MAX_RETRIES,
)

log = logging.getLogger(__name__)


class DCASkipReason(Enum):
    LOW_BALANCE = "잔고 부족"
    LIQ_TOO_CLOSE = "청산가 근접"


@dataclass
class DCAResult:
    symbol: str
    filled_usdc: float
    target_usdc: float
    filled_amount: float
    avg_price: float
    skipped: bool = False
    skip_reason: DCASkipReason | None = None
    position_after: dict | None = None
    account_after: dict | None = None
    error: str | None = None


def _check_safety(
    account: dict,
    position: dict | None,
    min_liq_pct: float,
    min_balance: float,
) -> DCASkipReason | None:
    balance = float(account.get("available_balance", "0"))
    if balance < min_balance:
        return DCASkipReason.LOW_BALANCE
    if position and position.get("liq_dist", 999) < min_liq_pct:
        return DCASkipReason.LIQ_TOO_CLOSE
    return None


def _calc_order_price(base_price: float, retry: int, step_pct: float) -> float:
    return base_price * (1 + retry * step_pct / 100)


def _find_position(account: dict, symbol: str) -> dict | None:
    for p in parse_positions(account):
        if p["symbol"] == symbol:
            return p
    return None


def _get_position_size(account: dict, symbol: str) -> float:
    p = _find_position(account, symbol)
    return p["size"] if p else 0.0


async def execute_dca(symbol: str, target_usdc: float) -> DCAResult:
    log.info("[DCA] %s $%.2f 시작", symbol, target_usdc)
    account_index = await get_account_index()
    market = await fetch_market_info(symbol)

    account = await fetch_account()
    if not account:
        return DCAResult(symbol=symbol, filled_usdc=0, target_usdc=target_usdc,
                         filled_amount=0, avg_price=0, error="계정 조회 실패")

    position = _find_position(account, symbol)
    skip_reason = _check_safety(account, position, MIN_LIQ_DISTANCE_PCT, MIN_AVAILABLE_BALANCE)
    if skip_reason:
        log.warning("[DCA] %s 스킵: %s", symbol, skip_reason.value)
        return DCAResult(symbol=symbol, filled_usdc=0, target_usdc=target_usdc,
                         filled_amount=0, avg_price=0, skipped=True,
                         skip_reason=skip_reason, position_after=position, account_after=account)

    # 현재 가격 = mark price (포지션 있으면 포지션에서, 없으면 entry 사용)
    base_price = position["current"] if position else 0.0
    if base_price <= 0:
        return DCAResult(symbol=symbol, filled_usdc=0, target_usdc=target_usdc,
                         filled_amount=0, avg_price=0, error="가격 조회 불가 (포지션 없음)")

    remaining_usdc = target_usdc
    total_filled_amount = 0.0
    total_filled_usdc = 0.0
    client_order_index = int(time.time()) % 1_000_000  # 유니크 클라이언트 인덱스

    for retry in range(ORDER_MAX_RETRIES):
        limit_price = _calc_order_price(base_price, retry, ORDER_PRICE_STEP_PCT)
        base_amount = remaining_usdc / limit_price

        # 최소 주문 수량 체크
        if base_amount < market["min_base_amount"]:
            log.info("[DCA] %s 잔여 수량 최소 이하 — 완료 처리", symbol)
            break

        pre_size = _get_position_size(account, symbol)

        log.info("[DCA] %s retry=%d price=%.4f amount=%.4f",
                 symbol, retry, limit_price, base_amount)

        tx_hash, err = await place_limit_buy(
            market_id=market["market_id"],
            base_amount_float=base_amount,
            price_float=limit_price,
            price_decimals=market["price_decimals"],
            size_decimals=market["size_decimals"],
            client_order_index=client_order_index + retry,
            account_index=account_index,
        )

        if err:
            log.error("[DCA] %s 주문 실패: %s", symbol, err)
            return DCAResult(symbol=symbol, filled_usdc=total_filled_usdc,
                             target_usdc=target_usdc, filled_amount=total_filled_amount,
                             avg_price=total_filled_usdc / total_filled_amount if total_filled_amount else 0,
                             error=err)

        await asyncio.sleep(ORDER_RETRY_INTERVAL_SEC)

        # 체결량 확인: 포지션 크기 변화로 측정
        account = await fetch_account()
        if not account:
            log.error("[DCA] %s 체결 확인 중 계정 조회 실패", symbol)
            break

        post_size = _get_position_size(account, symbol)
        filled_this_round = max(0.0, post_size - pre_size)
        filled_usdc_this_round = filled_this_round * limit_price

        total_filled_amount += filled_this_round
        total_filled_usdc += filled_usdc_this_round
        remaining_usdc -= filled_usdc_this_round

        log.info("[DCA] %s 체결: %.4f주 ($%.2f), 잔여: $%.2f",
                 symbol, filled_this_round, filled_usdc_this_round, remaining_usdc)

        if remaining_usdc < market["min_base_amount"] * limit_price:
            log.info("[DCA] %s 전량 체결 완료!", symbol)
            break

        # 미체결 잔량 취소
        if tx_hash:
            order_index = await fetch_tx_order_index(tx_hash)
            if order_index is not None:
                await cancel_order(market["market_id"], order_index, account_index)

    position_after = _find_position(account, symbol) if account else None
    avg_price = total_filled_usdc / total_filled_amount if total_filled_amount else 0

    return DCAResult(
        symbol=symbol,
        filled_usdc=total_filled_usdc,
        target_usdc=target_usdc,
        filled_amount=total_filled_amount,
        avg_price=avg_price,
        position_after=position_after,
        account_after=account,
    )


def format_dca_notification(result: DCAResult) -> str:
    name = SYMBOL_NAMES.get(result.symbol, result.symbol.replace("USD", ""))

    if result.error:
        return f"❌ DCA 오류 — {name}\n{result.error}"

    if result.skipped:
        reason = result.skip_reason.value if result.skip_reason else "알 수 없음"
        lines = [f"⚠️ DCA 스킵 — {name}", f"이유: {reason}"]
        p = result.position_after
        if p:
            lines.append(f"현재 포지션: {p['size']}주 | 청산가 {fmt_price(p['liq'])} ({p['liq_dist']:.1f}% 여유)")
        return "\n".join(lines)

    p = result.position_after
    lines = [
        f"✅ DCA 완료 — {name}",
        "─────────────────",
        f"💰 매수: ${result.filled_usdc:.2f} → {result.filled_amount:.4f}주 @ {fmt_price(result.avg_price)}",
    ]
    if p:
        pnl_e = "🟢" if p["upnl"] >= 0 else "🔴"
        lines += [
            f"📊 총 포지션: {p['size']:.4f}주 ({fmt_price(p['value'])})",
            f"⚠️ 청산가: {fmt_price(p['liq'])} ({p['liq_dist']:.1f}% 여유)",
            f"{pnl_e} 미실현 PnL: {p['upnl']:+,.1f} ({p['pnl_pct']:+.1f}%)",
        ]
    if result.filled_usdc < result.target_usdc * 0.99:
        lines.append(f"⚠️ 미체결: ${result.target_usdc - result.filled_usdc:.2f}")
    return "\n".join(lines)
```

- [ ] **Step 4: pytest.ini에 asyncio 모드 설정 (tests 폴더에)**

```bash
# D:\Codes\lighter_dca_bot\pytest.ini 파일 작성
```

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
python -m pytest tests/test_dca_engine.py -v
```

Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add dca_engine.py tests/test_dca_engine.py pytest.ini
git commit -m "feat: add DCA engine with price-chasing limit order loop"
```

---

### Task 6: bot.py — Telegram 봇 진입점

**Files:**
- Create: `D:\Codes\lighter_dca_bot\bot.py`

- [ ] **Step 1: bot.py 작성**

```python
"""Lighter DCA Bot — Telegram 봇 진입점"""

import logging
from datetime import time, timezone, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    DCA_MARKETS, DCA_TIME_AEST, MONITOR_HOURS_AEST, AEST_OFFSET,
)
from monitor import get_full_status
from dca_engine import execute_dca, format_dca_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

UTC = timezone.utc
AEST = timezone(timedelta(hours=AEST_OFFSET))
OWNER_FILTER = filters.Chat(int(TELEGRAM_CHAT_ID)) if TELEGRAM_CHAT_ID else filters.ALL


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    markets_str = "\n".join(
        f"  • {sym}: ${amt:.0f}/일" for sym, amt in DCA_MARKETS.items()
    ) or "  (없음 — .env에 DCA_SYMBOL=금액 설정)"
    h, m = DCA_TIME_AEST
    await update.message.reply_text(
        f"⚡ Lighter DCA Bot\n\n"
        f"📋 DCA 종목:\n{markets_str}\n\n"
        f"⏰ 매수 시각: AEST {h:02d}:{m:02d}\n\n"
        f"명령어:\n"
        f"  /l      — 포지션 현황\n"
        f"  /dca    — DCA 즉시 실행\n"
        f"  /config — 현재 DCA 설정"
    )


async def cmd_lighter(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Lighter 포지션 조회 중...")
    try:
        msg = await get_full_status()
        await update.message.reply_text(msg)
    except Exception as e:
        log.exception("lighter status failed")
        await update.message.reply_text(f"❌ 조회 실패: {e}")


async def cmd_dca(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not DCA_MARKETS:
        await update.message.reply_text("⚠️ DCA 종목이 설정되지 않았소. .env에 DCA_SYMBOL=금액 추가 필요.")
        return
    await update.message.reply_text(f"🚀 DCA 수동 실행 시작 ({len(DCA_MARKETS)}종목)...")
    for symbol, usdc in DCA_MARKETS.items():
        try:
            result = await execute_dca(symbol, usdc)
            msg = format_dca_notification(result)
        except Exception as e:
            log.exception("DCA failed: %s", symbol)
            msg = f"❌ {symbol} DCA 실패: {e}"
        await update.message.reply_text(msg)


async def cmd_config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    h, m = DCA_TIME_AEST
    lines = [
        "⚙️ DCA 설정 현황",
        "─────────────────",
        f"⏰ 매수 시각: AEST {h:02d}:{m:02d}",
        f"📡 모니터링: AEST {', '.join(f'{x:02d}:00' for x in MONITOR_HOURS_AEST)}",
        "",
        "📋 DCA 종목:",
    ]
    if DCA_MARKETS:
        for sym, amt in DCA_MARKETS.items():
            lines.append(f"  • {sym}: ${amt:.0f}/일")
    else:
        lines.append("  (없음)")
    await update.message.reply_text("\n".join(lines))


async def job_monitor(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        msg = await get_full_status()
    except Exception as e:
        log.exception("monitor job failed")
        msg = f"❌ Lighter 자동 조회 실패: {e}"
    try:
        await ctx.bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=msg)
    except Exception:
        log.warning("모니터 메시지 전송 실패")


async def job_dca(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("DCA 잡 시작 — %d종목", len(DCA_MARKETS))
    for symbol, usdc in DCA_MARKETS.items():
        try:
            result = await execute_dca(symbol, usdc)
            msg = format_dca_notification(result)
        except Exception as e:
            log.exception("DCA job failed: %s", symbol)
            msg = f"❌ {symbol} DCA 실패: {e}"
        try:
            await ctx.bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=msg)
        except Exception:
            log.warning("DCA 알림 전송 실패: %s", symbol)


def _aest_to_utc(aest_hour: int, aest_minute: int = 0) -> time:
    utc_hour = (aest_hour - AEST_OFFSET) % 24
    return time(hour=utc_hour, minute=aest_minute, tzinfo=UTC)


def run_bot() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("l", cmd_lighter, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("dca", cmd_dca, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("config", cmd_config, filters=OWNER_FILTER))

    jq = app.job_queue

    # DCA 잡: 매일 DCA_TIME_AEST
    dca_h, dca_m = DCA_TIME_AEST
    dca_utc = _aest_to_utc(dca_h, dca_m)
    jq.run_daily(job_dca, time=dca_utc, name="dca_daily")
    log.info("DCA 잡 등록: AEST %02d:%02d (UTC %s)", dca_h, dca_m, dca_utc)

    # 모니터 잡: MONITOR_HOURS_AEST 각 시각
    for aest_h in MONITOR_HOURS_AEST:
        utc_t = _aest_to_utc(aest_h)
        jq.run_daily(job_monitor, time=utc_t, name=f"monitor_{aest_h:02d}aest")
    log.info("모니터 잡 등록: AEST %s", ", ".join(f"{h:02d}:00" for h in MONITOR_HOURS_AEST))

    log.info("Lighter DCA Bot 시작 — polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
```

- [ ] **Step 2: 임포트 확인**

```bash
cd D:\Codes\lighter_dca_bot
python -c "import bot; print('OK')"
```

Expected: OK (봇 실행 없이 임포트만)

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: add Telegram bot with /l /dca /config commands and scheduled jobs"
```

---

### Task 7: market_dashboard_bot 정리

**Files:**
- Modify: `D:\Codes\market_dashboard_bot\bot.py`
- Delete: `D:\Codes\market_dashboard_bot\lighter_status.py`
- Modify: `D:\Codes\market_dashboard_bot\requirements.txt`

- [ ] **Step 1: bot.py에서 lighter 관련 코드 제거**

`D:\Codes\market_dashboard_bot\bot.py` 에서 아래 항목 제거:

**제거할 import:**
```python
from lighter_status import fetch_lighter_status  # 이 줄 삭제
```

**제거할 핸들러 함수 전체:**
- `cmd_lighter()` 함수 (139~146번 줄) 삭제
- `lighter_scheduled()` 함수 (159~171번 줄) 삭제

**`run_bot()` 함수에서 제거:**
```python
# 아래 두 블록 삭제
owner_filter = filters.Chat(int(TELEGRAM_CHAT_ID)) if TELEGRAM_CHAT_ID else filters.ALL
app.add_handler(CommandHandler("l", cmd_lighter, filters=owner_filter))

# Lighter 포지션: AEST 08:00~23:00 매시간 (UTC 22:00~13:00)
for aest_h in range(8, 24):
    utc_h = (aest_h - 10) % 24
    job_queue.run_daily(
        lighter_scheduled,
        time=time(hour=utc_h, minute=0, tzinfo=timezone.utc),
        name=f"lighter_{aest_h:02d}aest",
    )
logger.info("Lighter 스케줄 등록: AEST 08:00~23:00 매시간 (16회/일)")
```

**`cmd_start()` 텍스트에서 `/l` 라인 삭제:**
```
"  /l        - ⚡ Lighter 포지션 현황\n"   ← 이 줄 삭제
```

- [ ] **Step 2: lighter_status.py 삭제**

```bash
del D:\Codes\market_dashboard_bot\lighter_status.py
```

- [ ] **Step 3: requirements.txt에서 lighter-sdk 제거**

`D:\Codes\market_dashboard_bot\requirements.txt` 에서 `lighter-sdk` 줄 삭제.

- [ ] **Step 4: 임포트 확인**

```bash
cd D:\Codes\market_dashboard_bot
python -c "import bot; print('OK')"
```

Expected: OK

- [ ] **Step 5: Commit (market_dashboard_bot)**

```bash
cd D:\Codes\market_dashboard_bot
git add bot.py lighter_status.py requirements.txt
git commit -m "refactor: remove Lighter monitoring (moved to lighter_dca_bot)"
```

---

### Task 8: .env 작성 및 최종 검증

**Files:**
- Create: `D:\Codes\lighter_dca_bot\.env` (gitignore에 포함됨)

- [ ] **Step 1: .env 파일 작성**

`.env.example`을 복사해 `.env`를 만들고 실제 값 입력:

```bash
copy D:\Codes\lighter_dca_bot\.env.example D:\Codes\lighter_dca_bot\.env
```

채워야 할 값:
- `TELEGRAM_BOT_TOKEN` — BotFather에서 새 봇 생성 후 발급
- `TELEGRAM_CHAT_ID` — 본인 채팅 ID (`@userinfobot`으로 확인)
- `LIGHTER_WALLET` — Lighter 연결 지갑 주소
- `LIGHTER_API_KEY_INDEX` / `LIGHTER_API_PRIVATE_KEY` — `app.lighter.xyz` → Settings → API Keys

- [ ] **Step 2: 계정 인덱스 자동 조회 검증**

```bash
cd D:\Codes\lighter_dca_bot
python -c "
import asyncio
from lighter_client import fetch_account_index
idx = asyncio.run(fetch_account_index())
print(f'account_index: {idx}')
"
```

Expected: `account_index: 12345` (실제 번호 출력)

- [ ] **Step 3: 마켓 정보 조회 검증**

```bash
python -c "
import asyncio
from lighter_client import fetch_market_info
info = asyncio.run(fetch_market_info('SKHYNIXUSD'))
print(info)
"
```

Expected: `{'market_id': N, 'price_decimals': 2, 'size_decimals': 4, 'min_base_amount': 0.001}`

- [ ] **Step 4: 포지션 조회 검증**

```bash
python -c "
import asyncio
from monitor import get_full_status
msg = asyncio.run(get_full_status())
print(msg)
"
```

Expected: 현재 포지션 현황 텍스트 출력

- [ ] **Step 5: 봇 실행 테스트**

```bash
python bot.py
```

텔레그램에서 `/start` → 봇 응답 확인
`/l` → 포지션 현황 확인
`/config` → DCA 설정 확인

- [ ] **Step 6: DCA 수동 실행 테스트**

텔레그램에서 `/dca` 전송 → 실제 주문 발생 (소액으로 테스트 권장)

- [ ] **Step 7: Termux용 실행 스크립트 작성**

`D:\Codes\lighter_dca_bot\run.sh` 작성 (Termux에서 실행):

```bash
#!/bin/bash
cd ~/lighter_dca_bot
source .env 2>/dev/null || true
nohup python bot.py >> logs/bot.log 2>&1 &
echo "PID: $!"
```

```bash
mkdir -p D:\Codes\lighter_dca_bot\logs
```

- [ ] **Step 8: 최종 Commit**

```bash
cd D:\Codes\lighter_dca_bot
git add run.sh logs/.gitkeep
git commit -m "feat: add run script and complete lighter_dca_bot"
```

---

## 참고: Termux 배포 순서

```bash
# Termux에서
cd ~
git clone <repo> lighter_dca_bot
cd lighter_dca_bot
pip install -r requirements.txt
cp .env.example .env
nano .env   # 실제 값 입력
python bot.py   # 포그라운드 테스트
# 정상 확인 후
bash run.sh   # 백그라운드 실행
```
