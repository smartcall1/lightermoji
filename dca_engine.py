"""DCA 실행 엔진 — 지정가 주문 + 가격 추격 루프"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from monitor import fetch_account, parse_positions, fmt_price, fmt_liq, SYMBOL_NAMES, unit_for
from lighter_client import (
    fetch_market_info, place_limit_buy, place_market_close, fetch_mark_price,
    fetch_active_order, fetch_order_record, cancel_order, get_account_index,
)
from config import (
    MIN_LIQ_DISTANCE_PCT, MIN_AVAILABLE_BALANCE,
    ORDER_RETRY_INTERVAL_SEC, ORDER_PRICE_STEP_PCT, ORDER_MAX_RETRIES,
    CLOSE_SLIPPAGE_PCT, CANCEL_VERIFY_ATTEMPTS, CANCEL_VERIFY_INTERVAL_SEC,
)

log = logging.getLogger(__name__)

_active_symbols: set[str] = set()


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
    target_base = symbol.replace("USD", "").replace("USDC", "").upper()
    for p in parse_positions(account):
        p_base = p["symbol"].replace("USD", "").replace("USDC", "").upper()
        if p_base == target_base:
            return p
    return None


async def _settle_order(
    market_id: int, account_index: int, client_order_index: int
) -> tuple[bool, dict | None]:
    """주문의 미체결 잔량을 취소하고 최종 기록을 반환한다.

    (취소 확정 여부, 주문 기록) — 취소가 확정되지 않으면 호출부는 추가 주문을 내면 안 된다.
    """
    live = await fetch_active_order(market_id, account_index, client_order_index)
    cancel_confirmed = True

    if live is not None:
        cancel_confirmed = False
        for _ in range(CANCEL_VERIFY_ATTEMPTS):
            await cancel_order(market_id, int(live["order_index"]), account_index)
            await asyncio.sleep(CANCEL_VERIFY_INTERVAL_SEC)
            live = await fetch_active_order(market_id, account_index, client_order_index)
            if live is None:
                cancel_confirmed = True
                break

    record = await fetch_order_record(market_id, account_index, client_order_index)
    return cancel_confirmed, record


async def execute_dca(symbol: str, target_usdc: float) -> DCAResult:
    if symbol in _active_symbols:
        log.warning("[DCA] %s 이미 진행 중인 요청 있음 — 중복 호출 스킵", symbol)
        return DCAResult(symbol=symbol, filled_usdc=0, target_usdc=target_usdc,
                         filled_amount=0, avg_price=0, error="이미 진행 중인 DCA 요청 (중복 스킵)")
    _active_symbols.add(symbol)
    try:
        return await _execute_dca_inner(symbol, target_usdc)
    finally:
        _active_symbols.discard(symbol)


async def _execute_dca_inner(symbol: str, target_usdc: float) -> DCAResult:
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

    base_price = position["current"] if position else 0.0
    if base_price <= 0:
        # 신규 종목(기존 포지션 없음) — 마크가로 기준가 조회
        base_price = await fetch_mark_price(market["market_id"]) or 0.0
    if base_price <= 0:
        return DCAResult(symbol=symbol, filled_usdc=0, target_usdc=target_usdc,
                         filled_amount=0, avg_price=0, error="가격 조회 불가")

    remaining_usdc = target_usdc
    total_filled_amount = 0.0
    total_filled_usdc = 0.0
    client_order_index = int(time.time()) % 1_000_000
    market_id = market["market_id"]

    def _fail(msg: str) -> DCAResult:
        return DCAResult(
            symbol=symbol, filled_usdc=total_filled_usdc, target_usdc=target_usdc,
            filled_amount=total_filled_amount,
            avg_price=total_filled_usdc / total_filled_amount if total_filled_amount else 0,
            position_after=_find_position(account, symbol) if account else None,
            account_after=account, error=msg,
        )

    for retry in range(ORDER_MAX_RETRIES):
        limit_price = _calc_order_price(base_price, retry, ORDER_PRICE_STEP_PCT)
        base_amount = remaining_usdc / limit_price

        if base_amount < market["min_base_amount"]:
            log.info("[DCA] %s 잔여 수량 최소 이하 — 완료 처리", symbol)
            break

        coi = client_order_index + retry

        log.info("[DCA] %s retry=%d price=%.4f amount=%.4f",
                 symbol, retry, limit_price, base_amount)

        _tx_hash, err = await place_limit_buy(
            market_id=market_id,
            base_amount_float=base_amount,
            price_float=limit_price,
            price_decimals=market["price_decimals"],
            size_decimals=market["size_decimals"],
            client_order_index=coi,
            account_index=account_index,
        )

        if err:
            log.error("[DCA] %s 주문 실패: %s", symbol, err)
            return _fail(err)

        await asyncio.sleep(ORDER_RETRY_INTERVAL_SEC)

        # 다음 주문을 내기 전에 이번 주문의 잔량을 반드시 정리한다.
        # 잔존 GTT 주문이 나중에 체결되면 목표액의 몇 배를 매수하게 된다.
        try:
            cancel_confirmed, record = await _settle_order(market_id, account_index, coi)
        except Exception as e:
            log.exception("[DCA] %s 주문 상태 조회 실패", symbol)
            return _fail(f"주문 상태 조회 실패 — 중복 매수 방지를 위해 중단: {e}")

        if record is None:
            log.error("[DCA] %s 주문 기록 조회 불가 (coi=%d)", symbol, coi)
            return _fail("주문 기록 조회 실패 — 체결량 미확정으로 중단")

        filled_this_round = float(record.get("filled_base_amount") or 0)
        filled_usdc_this_round = float(record.get("filled_quote_amount") or 0)

        total_filled_amount += filled_this_round
        total_filled_usdc += filled_usdc_this_round
        remaining_usdc = max(0.0, target_usdc - total_filled_usdc)

        log.info("[DCA] %s 체결: %.4f주 ($%.2f), 누적 $%.2f, 잔여: $%.2f",
                 symbol, filled_this_round, filled_usdc_this_round,
                 total_filled_usdc, remaining_usdc)

        if not cancel_confirmed:
            log.error("[DCA] %s 미체결 주문 취소 실패 (coi=%d) — 중단", symbol, coi)
            return _fail(
                f"미체결 주문 취소 실패 (coi={coi}) — 중복 매수 방지를 위해 중단. "
                f"Lighter 앱에서 잔존 주문 확인 필요"
            )

        if remaining_usdc < market["min_base_amount"] * limit_price:
            log.info("[DCA] %s 전량 체결 완료!", symbol)
            break

        account = await fetch_account()
        if not account:
            log.error("[DCA] %s 체결 확인 중 계정 조회 실패", symbol)
            break

        position = _find_position(account, symbol)
        skip_reason = _check_safety(account, position, MIN_LIQ_DISTANCE_PCT, MIN_AVAILABLE_BALANCE)
        if skip_reason:
            log.warning("[DCA] %s retry 중단(%s) — 잔여 $%.2f 미체결", symbol, skip_reason.value, remaining_usdc)
            break

    account = await fetch_account() or account
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


@dataclass
class CloseResult:
    symbol: str
    closed_amount: float
    side: str
    account_after: dict | None = None
    error: str | None = None


async def close_position(symbol: str) -> CloseResult:
    """포지션 반대방향 reduce-only 시장가 주문으로 전량 종료."""
    log.info("[CLOSE] %s 시작", symbol)
    account_index = await get_account_index()
    market = await fetch_market_info(symbol)

    account = await fetch_account()
    if not account:
        return CloseResult(symbol=symbol, closed_amount=0, side="", error="계정 조회 실패")

    position = _find_position(account, symbol)
    if not position or position["size"] == 0:
        return CloseResult(symbol=symbol, closed_amount=0, side="", error="종료할 포지션이 없음")

    is_long = position["side"] == "Long"
    is_ask = is_long  # Long 종료 = 매도, Short 종료 = 매수
    base_amount = abs(position["size"])
    current_price = position["current"]
    # 매도(청산)는 최저 허용가를 낮게, 매수(청산)는 최고 허용가를 높게 — 체결 보장용 슬리피지
    exec_price = current_price * (1 - CLOSE_SLIPPAGE_PCT / 100 if is_ask else 1 + CLOSE_SLIPPAGE_PCT / 100)

    client_order_index = int(time.time()) % 1_000_000

    tx_hash, err = await place_market_close(
        market_id=market["market_id"],
        base_amount_float=base_amount,
        is_ask=is_ask,
        avg_execution_price_float=exec_price,
        price_decimals=market["price_decimals"],
        size_decimals=market["size_decimals"],
        client_order_index=client_order_index,
        account_index=account_index,
    )

    if err:
        log.error("[CLOSE] %s 주문 실패: %s", symbol, err)
        return CloseResult(symbol=symbol, closed_amount=0, side=position["side"], error=err)

    await asyncio.sleep(3)
    account_after = await fetch_account()
    position_after = _find_position(account_after, symbol) if account_after else None
    remaining = position_after["size"] if position_after else 0.0
    closed_amount = base_amount - abs(remaining)

    log.info("[CLOSE] %s 완료: %.4f주 청산 (잔여 %.4f)", symbol, closed_amount, remaining)
    return CloseResult(
        symbol=symbol,
        closed_amount=closed_amount,
        side=position["side"],
        account_after=account_after,
    )


def format_close_notification(result: CloseResult) -> str:
    name = SYMBOL_NAMES.get(result.symbol, result.symbol.replace("USD", ""))
    unit = unit_for(result.symbol)

    if result.error:
        return f"❌ 종료 실패 — {name}\n{result.error}"

    lines = [
        f"✅ 포지션 종료 — {name}",
        "─────────────────",
        f"📉 {result.side} {result.closed_amount:.4f}{unit} 청산 완료",
    ]

    remaining = _find_position(result.account_after, result.symbol) if result.account_after else None
    if remaining:
        lines.append(f"⚠️ 잔여 포지션: {remaining['size']:.4f}{unit} (전량 미체결 — 상태 확인 필요)")
    return "\n".join(lines)


def format_dca_notification(result: DCAResult) -> str:
    name = SYMBOL_NAMES.get(result.symbol, result.symbol.replace("USD", ""))
    unit = unit_for(result.symbol)

    if result.error:
        return f"❌ DCA 오류 — {name}\n{result.error}"

    if result.skipped:
        reason = result.skip_reason.value if result.skip_reason else "알 수 없음"
        lines = [f"⚠️ DCA 스킵 — {name}", f"이유: {reason}"]
        p = result.position_after
        if p:
            liq_dist_str = "여유충분" if p['liq_dist'] >= 999 else f"{p['liq_dist']:.1f}% 여유"
            lines.append(f"현재 포지션: {p['size']}{unit} | 청산가 {fmt_liq(p['liq'])} ({liq_dist_str})")
        return "\n".join(lines)

    p = result.position_after
    lines = [
        f"✅ DCA 완료 — {name}",
        "─────────────────",
        f"💰 매수: ${result.filled_usdc:.2f} → {result.filled_amount:.4f}{unit} @ {fmt_price(result.avg_price)}",
    ]
    if p:
        pnl_e = "🟢" if p["upnl"] >= 0 else "🔴"
        liq_dist_str = "여유충분" if p['liq_dist'] >= 999 else f"{p['liq_dist']:.1f}% 여유"
        lines += [
            f"📊 총 포지션: {p['size']:.4f}{unit} ({fmt_price(p['value'])})",
            f"⚠️ 청산가: {fmt_liq(p['liq'])} ({liq_dist_str})",
            f"{pnl_e} 미실현 PnL: {p['upnl']:+,.1f} ({p['pnl_pct']:+.1f}%)",
        ]
    if result.filled_usdc < result.target_usdc * 0.99:
        lines.append(f"⚠️ 미체결: ${result.target_usdc - result.filled_usdc:.2f}")
    return "\n".join(lines)
