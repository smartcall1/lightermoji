"""Lighter 포지션 조회 및 포맷 — REST 읽기 전용 (인증 불필요)"""

import inspect
import httpx
from datetime import datetime, timezone, timedelta
from config import API_BASE, LIGHTER_WALLET, AEST_OFFSET, HEADERS

AEST = timezone(timedelta(hours=AEST_OFFSET))
# 8시간 펀딩 주기 * 연간 365일 = 1095 periods/year
FUNDING_PERIODS_PER_YEAR = 1095

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
    "CRCLUSD": "Circle",
}


def unit_for(symbol: str) -> str:
    """주식은 '주', 코인은 '개' — SYMBOL_NAMES에 등록된 종목을 주식으로 간주."""
    return "주" if symbol in SYMBOL_NAMES else "개"


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


async def _fetch_funding_rates() -> dict[int, float]:
    """market_id -> rate (8h 기준, lighter exchange 우선, 없으면 binance fallback)"""
    try:
        import lighter as lighter_sdk
        cfg = lighter_sdk.Configuration(host="https://mainnet.zklighter.elliot.ai")
        async with lighter_sdk.ApiClient(cfg) as api_client:
            fa = lighter_sdk.FundingApi(api_client)
            result = fa.funding_rates()
            if inspect.isawaitable(result):
                result = await result

            # exchange 우선순위: lighter > binance > bybit > hyperliquid
            priority = {"lighter": 0, "binance": 1, "bybit": 2, "hyperliquid": 3}
            best: dict[int, tuple[int, float]] = {}  # market_id -> (priority, rate)
            for r in result.funding_rates:
                p = priority.get(r.exchange, 99)
                if r.market_id not in best or p < best[r.market_id][0]:
                    best[r.market_id] = (p, r.rate)
            return {mid: v[1] for mid, v in best.items()}
    except Exception:
        return {}


def _next_funding_info() -> tuple[str, int]:
    """다음 펀딩 시각 문자열, 남은 분"""
    now = datetime.now(timezone.utc)
    current_h = now.hour
    next_slot_h = ((current_h // 8) + 1) * 8
    if next_slot_h >= 24:
        next_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        next_dt = now.replace(hour=next_slot_h, minute=0, second=0, microsecond=0)
    secs = int((next_dt - now).total_seconds())
    hrs, mins = secs // 3600, (secs % 3600) // 60
    label = f"{hrs}h{mins}m" if hrs > 0 else f"{mins}m"
    return label, secs // 60


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
        # 마진(투입 자본) 대비 손실률 — 청산 리스크를 체감하는 실질 지표
        pnl_pct = (upnl / margin * 100) if margin else 0
        leverage = round(100 / imf) if imf > 0 else 1
        # liq <= 0: 거래소가 개별 청산가를 제공하지 않는 경우(예: cross-margin 공유 담보)
        # — "청산가 0원 임박"이 아니라 "해당 없음"이므로 안전한 값(거리 무한대)으로 처리
        liq_dist = ((liq - current) / current * 100) if current and liq > 0 else 999.0

        results.append({
            "market_id": p.get("market_id"),
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


def _fmt_num(v: float, sign: bool = False) -> str:
    """정수면 정수로, 소수면 소수점 둘째 자리까지 표기."""
    s = "+" if sign and v >= 0 else ""
    if abs(v - round(v)) < 1e-9:
        return f"{s}{v:,.0f}"
    return f"{s}{v:,.2f}"


def fmt_price(v: float) -> str:
    return f"${_fmt_num(v)}"


def fmt_liq(v: float) -> str:
    return "N/A" if v <= 0 else fmt_price(v)


def _fmt_pct(v: float) -> str:
    return _fmt_num(v)


def _fmt_usd(v: float, is_diff: bool = False) -> str:
    sign = "+" if is_diff and v >= 0 else ""
    return f"{sign}${_fmt_num(v)}"


def format_position_message(account: dict, positions: list[dict], funding_rates: dict[int, float]) -> str:
    now = datetime.now(AEST).strftime("%m/%d %H:%M")
    balance = float(account.get("available_balance", "0"))
    total_value = float(account.get("total_asset_value", "0"))
    total_upnl = sum(p["upnl"] for p in positions)
    total_margin = sum(p["margin"] for p in positions)
    next_fund_label, _ = _next_funding_info()

    lines = [f"⚡ Lighter — {now} AEST"]

    if not positions:
        lines += ["", "No active positions"]
    else:
        for idx, p in enumerate(positions, 1):
            pnl_e = "🟢" if p["upnl"] >= 0 else "🔴"
            d = "L" if p["side"] == "Long" else "S"
            order_tag = f" 📋{p['orders']}" if p["orders"] > 0 else ""
            lines += [
                "",
                f"{idx}. {'📈' if d == 'L' else '📉'} {p['name']} {d}{p['leverage']}x{order_tag} (Margin {_fmt_usd(p['margin'])})",
                f"{fmt_price(p['entry'])}→{fmt_price(p['current'])} ({_fmt_num(p['size'])}, {_fmt_usd(p['value'])})",
                f"{pnl_e} {_fmt_num(p['upnl'], sign=True)} ({_fmt_num(p['pnl_pct'], sign=True)}%) ⚠️{fmt_liq(p['liq'])}",
            ]

            # 펀딩피 라인 (2줄 분리: 모바일 줄바꿈 방지)
            cumulative = p["funding"]
            cum_str = _fmt_usd(cumulative, is_diff=True)
            lines.append(f"💸 Funding {cum_str}")

            rate = funding_rates.get(p["market_id"])
            if rate is not None:
                direction = -1 if p["side"] == "Long" else 1
                levered_apr_pct = rate * FUNDING_PERIODS_PER_YEAR * p["leverage"] * direction * 100
                apr_sign = "+" if levered_apr_pct >= 0 else ""
                apr_icon = "🟢" if levered_apr_pct >= 0 else "🔴"
                lines.append(f"  ({apr_icon}{apr_sign}{_fmt_pct(levered_apr_pct)}%APR) ⏰{next_fund_label}")
            else:
                lines.append(f"  ⏰{next_fund_label}")

    lines.append("─────────────────")
    pnl_e = "🟢" if total_upnl >= 0 else "🔴"
    total_upnl_str = _fmt_usd(total_upnl, is_diff=True)
    lines.append(f"{pnl_e} PnL {total_upnl_str} | Margin {_fmt_usd(total_margin)}")
    lines.append(f"💰 Available {_fmt_usd(balance)} | Total {_fmt_usd(total_value)}")

    pool_details = account.get("_pool_details", [])
    if pool_details:
        lines.append("─────────────────")
        total_equity = sum(p["equity"] for p in pool_details)
        total_lp_pnl = sum(p["lp_pnl"] for p in pool_details)
        lp_e = "🟢" if total_lp_pnl >= 0 else "🔴"
        total_lp_pnl_str = _fmt_usd(total_lp_pnl, is_diff=True)
        lines.append(f"🏦 LP {_fmt_usd(total_equity)} ({lp_e}{total_lp_pnl_str})")
        for pd in pool_details:
            apy_str = f" {_fmt_num(pd['apy'], sign=True)}%" if pd.get("apy") is not None else ""
            pnl_val = pd["lp_pnl"]
            pnl_str = f" ({_fmt_usd(pnl_val, is_diff=True)})" if pnl_val != 0 else ""
            name = (
                pd["name"]
                .replace("Lighter Liquidity Provider (LLP)", "LLP")
                .replace("Edge & Hedge (L/S Factors)", "Edge&Hedge")
                .replace("$LIT Staking", "LIT Staking")
            )
            lit_tag = pd.get("lit_tag", "")
            lines.append(f"  {name} {_fmt_usd(pd['equity'])}{pnl_str}{apy_str}{lit_tag}")

    return "\n".join(lines)


async def get_full_status() -> str:
    """포지션 + LP 풀 포함 전체 현황 메시지 반환."""
    async with httpx.AsyncClient() as client:
        account = await fetch_account(client)
        if not account:
            return "❌ Failed to fetch Lighter account"

        lit_price = await fetch_lit_price(client)
        funding_rates = await _fetch_funding_rates()
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
            # Lighter API의 total_asset_value가 total_perps_value만 반영하고
            # total_spot_value(XAUT/rhSPY 등 실물 보유분)를 누락하는 경우가 있어 직접 합산
            tav_perps = float(meta.get("total_perps_value", "0") or 0) if meta else 0
            tav_spot = float(meta.get("total_spot_value", "0") or 0) if meta else 0
            tav = (tav_perps + tav_spot) if (tav_perps or tav_spot) else (
                float(meta["total_asset_value"]) if meta and meta.get("total_asset_value") else 0
            )
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
        return format_position_message(account, positions, funding_rates)
