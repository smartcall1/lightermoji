import pytest
from dca_engine import _check_safety, DCASkipReason, _calc_order_price, format_dca_notification, DCAResult

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
