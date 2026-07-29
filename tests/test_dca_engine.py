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

