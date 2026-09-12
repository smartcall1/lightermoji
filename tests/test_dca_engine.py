import pytest
from dca_engine import (
    _check_safety, DCASkipReason, _calc_order_price, format_dca_notification, DCAResult, _find_position,
    CloseResult, format_close_notification,
)

SAMPLE_POSITION = {
    "symbol": "SKHYNIXUSD",
    "side": "Long",
    "size": 0.5,
    "current": 600.0,
    "liq": 400.0,
    "liq_dist": 33.3,
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


def test_safety_check_passes():
    reason = _check_safety(SAMPLE_ACCOUNT, SAMPLE_POSITION, min_liq_pct=5.0, min_balance=30.0)
    assert reason is None


def test_safety_check_low_balance():
    account = {**SAMPLE_ACCOUNT, "available_balance": "20.0"}
    reason = _check_safety(account, SAMPLE_POSITION, min_liq_pct=5.0, min_balance=30.0)
    assert reason == DCASkipReason.LOW_BALANCE


def test_safety_check_liq_too_close():
    pos = {**SAMPLE_POSITION, "liq_dist": 3.0}
    reason = _check_safety(SAMPLE_ACCOUNT, pos, min_liq_pct=5.0, min_balance=30.0)
    assert reason == DCASkipReason.LIQ_TOO_CLOSE


def test_safety_check_no_position():
    reason = _check_safety(SAMPLE_ACCOUNT, None, min_liq_pct=5.0, min_balance=30.0)
    assert reason is None


def test_calc_order_price_retry_0():
    price = _calc_order_price(base_price=600.0, retry=0, step_pct=0.05)
    assert price == pytest.approx(600.0, rel=1e-3)


def test_calc_order_price_retry_5():
    price = _calc_order_price(base_price=600.0, retry=5, step_pct=0.05)
    assert price == pytest.approx(601.5, rel=1e-3)


def test_format_dca_notification_skip():
    result = DCAResult(
        symbol="SKHYNIXUSD",
        filled_usdc=0,
        target_usdc=50,
        filled_amount=0,
        avg_price=0,
        skipped=True,
        skip_reason=DCASkipReason.LOW_BALANCE,
        position_after=SAMPLE_POSITION,
    )
    msg = format_dca_notification(result)
    assert "스킵" in msg
    assert "잔고 부족" in msg


def test_format_dca_notification_success():
    result = DCAResult(
        symbol="SKHYNIXUSD",
        filled_usdc=50.0,
        target_usdc=50.0,
        filled_amount=0.083,
        avg_price=602.4,
        position_after=SAMPLE_POSITION,
    )
    msg = format_dca_notification(result)
    assert "완료" in msg
    assert "SK하이닉스" in msg


def test_format_dca_notification_error():
    result = DCAResult(
        symbol="NVDAUSD",
        filled_usdc=0,
        target_usdc=30,
        filled_amount=0,
        avg_price=0,
        error="서명 실패: invalid key",
    )
    msg = format_dca_notification(result)
    assert "오류" in msg
    assert "서명 실패" in msg


def test_find_position_flexible_matching():
    account = {
        "positions": [
            {
                "symbol": "HYPEUSD",
                "position": "10.0",
                "avg_entry_price": "60.0",
                "position_value": "600.0",
                "unrealized_pnl": "0.0",
                "liquidation_price": "40.0",
                "allocated_margin": "200.0",
                "initial_margin_fraction": "10",
                "sign": 1,
            }
        ]
    }
    # "HYPE" 로 조회 시 매칭 성공 확인
    p1 = _find_position(account, "HYPE")
    assert p1 is not None
    assert p1["symbol"] == "HYPEUSD"

    # "HYPEUSD" 로 조회 시 매칭 성공 확인
    p2 = _find_position(account, "HYPEUSD")
    assert p2 is not None

    # 대소문자 무관 조회 확인
    p3 = _find_position(account, "hype")
    assert p3 is not None

    # 존재하지 않는 심볼 매칭 실패 확인
    assert _find_position(account, "LIT") is None


def test_format_close_notification_success():
    result = CloseResult(
        symbol="SKHYNIXUSD",
        closed_amount=0.5,
        side="Long",
        account_after=SAMPLE_ACCOUNT,
    )
    msg = format_close_notification(result)
    assert "종료" in msg
    assert "SK하이닉스" in msg
    assert "0.5000주" in msg


def test_format_close_notification_error():
    result = CloseResult(symbol="NVDAUSD", closed_amount=0, side="", error="종료할 포지션이 없음")
    msg = format_close_notification(result)
    assert "실패" in msg
    assert "종료할 포지션이 없음" in msg



# ── 중복 매수 회귀 테스트 (retry 사다리 잔존 주문) ──────────────


class _FakeExchange:
    """place/cancel/조회를 인메모리로 흉내내는 테스트용 거래소.

    fill_after: 주문 생성 후 몇 번째 폴링에서 체결되는지 (None이면 영원히 미체결)
    """

    def __init__(self, fill_plan: dict[int, float], cancel_works: bool = True):
        # fill_plan: retry 인덱스 → 그 주문이 sleep 동안 체결되는 비율(0.0~1.0)
        self.fill_plan = fill_plan
        self.cancel_works = cancel_works
        self.orders: dict[int, dict] = {}
        self.placed: list[dict] = []
        self.cancels: list[int] = []
        self.events: list[str] = []
        self._next_order_index = 1000

    async def place_limit_buy(self, *, market_id, base_amount_float, price_float,
                              price_decimals, size_decimals, client_order_index, account_index):
        retry = len(self.placed)
        ratio = self.fill_plan.get(retry, 0.0)
        oi = self._next_order_index
        self._next_order_index += 1
        filled_base = base_amount_float * ratio
        self.orders[client_order_index] = {
            "order_index": oi,
            "client_order_index": client_order_index,
            "initial_base_amount": f"{base_amount_float}",
            "filled_base_amount": f"{filled_base}",
            "filled_quote_amount": f"{filled_base * price_float}",
            "price": f"{price_float}",
            "status": "filled" if ratio >= 1.0 else "open",
            "_live": ratio < 1.0,
        }
        self.placed.append({"coi": client_order_index, "amount": base_amount_float, "price": price_float})
        self.events.append(f"place:{client_order_index}")
        return f"0xtx{oi}", None

    async def fetch_active_order(self, market_id, account_index, client_order_index):
        o = self.orders.get(client_order_index)
        return o if o and o["_live"] else None

    async def fetch_order_record(self, market_id, account_index, client_order_index):
        return self.orders.get(client_order_index)

    async def cancel_order(self, market_id, order_index, account_index):
        self.cancels.append(order_index)
        self.events.append(f"cancel:{order_index}")
        if not self.cancel_works:
            return False
        for o in self.orders.values():
            if o["order_index"] == order_index:
                o["_live"] = False
                o["status"] = "canceled"
        return True


def _install(monkeypatch, ex, *, price=25.0, balance="5000.0"):
    import dca_engine as eng

    async def fake_account():
        return {"available_balance": balance, "total_asset_value": "10000.0", "positions": []}

    async def fake_market(symbol):
        return {"market_id": 123, "price_decimals": 3, "size_decimals": 3, "min_base_amount": 0.45}

    async def fake_mark(market_id):
        return price

    async def fake_sleep(_sec):
        return None

    monkeypatch.setattr(eng, "fetch_account", fake_account)
    monkeypatch.setattr(eng, "fetch_market_info", fake_market)
    monkeypatch.setattr(eng, "fetch_mark_price", fake_mark)
    monkeypatch.setattr(eng, "get_account_index", lambda: _coro(114487))
    monkeypatch.setattr(eng, "place_limit_buy", ex.place_limit_buy)
    monkeypatch.setattr(eng, "fetch_active_order", ex.fetch_active_order)
    monkeypatch.setattr(eng, "fetch_order_record", ex.fetch_order_record)
    monkeypatch.setattr(eng, "cancel_order", ex.cancel_order)
    monkeypatch.setattr(eng.asyncio, "sleep", fake_sleep)


def _coro(value):
    async def _inner():
        return value
    return _inner()


async def test_stale_order_canceled_before_next_order(monkeypatch):
    """미체결 주문은 다음 주문을 내기 전에 반드시 취소돼야 한다."""
    from dca_engine import _execute_dca_inner

    ex = _FakeExchange(fill_plan={0: 0.0, 1: 0.0, 2: 1.0})
    _install(monkeypatch, ex)

    await _execute_dca_inner("BMNRUSD", 200.0)

    # place/cancel 이 교대로 나와야 하며, 연속 place 는 중복 주문 사다리를 의미한다
    places = [i for i, e in enumerate(ex.events) if e.startswith("place")]
    for a, b in zip(places, places[1:]):
        assert any(ex.events[i].startswith("cancel") for i in range(a, b)), \
            f"취소 없이 연속 주문 발생: {ex.events}"


async def test_total_filled_never_exceeds_target(monkeypatch):
    """느린 체결 종목에서도 총 매수액이 목표를 넘지 않아야 한다."""
    from dca_engine import _execute_dca_inner

    ex = _FakeExchange(fill_plan={0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 1.0})
    _install(monkeypatch, ex)

    result = await _execute_dca_inner("BMNRUSD", 200.0)

    total_filled = sum(float(o["filled_quote_amount"]) for o in ex.orders.values())
    assert total_filled <= 200.0 * 1.01, f"목표 초과 매수: ${total_filled:.2f}"
    assert result.filled_usdc == pytest.approx(total_filled, rel=1e-6)
    assert result.error is None


async def test_partial_fill_reduces_remaining(monkeypatch):
    """부분 체결분은 주문 기록 기준으로 잔여 금액에서 차감돼야 한다."""
    from dca_engine import _execute_dca_inner

    ex = _FakeExchange(fill_plan={0: 0.5, 1: 1.0})
    _install(monkeypatch, ex)

    result = await _execute_dca_inner("BMNRUSD", 200.0)

    assert len(ex.placed) == 2
    # 1회차에서 절반($100) 체결 → 2회차 주문은 약 $100 규모여야 한다
    second = ex.placed[1]
    assert second["amount"] * second["price"] == pytest.approx(100.0, rel=0.02)
    assert result.filled_usdc == pytest.approx(200.0, rel=0.02)


async def test_abort_when_cancel_cannot_be_confirmed(monkeypatch):
    """취소가 확인되지 않으면 추가 주문 없이 즉시 중단해야 한다."""
    from dca_engine import _execute_dca_inner

    ex = _FakeExchange(fill_plan={0: 0.0, 1: 0.0}, cancel_works=False)
    _install(monkeypatch, ex)

    result = await _execute_dca_inner("BMNRUSD", 200.0)

    assert len(ex.placed) == 1, f"취소 미확인 상태에서 추가 주문 발생: {ex.events}"
    assert result.error is not None
    assert "취소" in result.error


async def test_immediate_full_fill_places_single_order(monkeypatch):
    """즉시 전량 체결되면 주문은 1건, 취소는 0건이어야 한다."""
    from dca_engine import _execute_dca_inner

    ex = _FakeExchange(fill_plan={0: 1.0})
    _install(monkeypatch, ex)

    result = await _execute_dca_inner("BMNRUSD", 200.0)

    assert len(ex.placed) == 1
    assert ex.cancels == []
    assert result.filled_usdc == pytest.approx(200.0, rel=0.02)
