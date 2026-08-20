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
            "initial_margin_fraction": "10",
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


def test_liq_dist_is_positive():
    positions = parse_positions(SAMPLE_ACCOUNT)
    # Long: liq(400) < current(640), raw = (400-640)/640 = -37.5%, abs = 37.5%
    assert positions[0]["liq_dist"] >= 0


def test_fmt_price():
    # >= 1000: 소수점 없음
    assert fmt_price(1234.0) == "$1,234"
    assert fmt_price(2500.0) == "$2,500"
    # 소수점 둘째자리까지 표기
    assert fmt_price(150.5) == "$150.50"
    assert fmt_price(100.0) == "$100"
    # < 100: 소수점 2자리
    assert fmt_price(5.678) == "$5.68"
    assert fmt_price(50.12) == "$50.12"


def test_format_message_has_symbol():
    positions = parse_positions(SAMPLE_ACCOUNT)
    msg = format_position_message(SAMPLE_ACCOUNT, positions, {})
    assert "SK하이닉스" in msg
    assert "Long" in msg or "L10x" in msg


def test_format_message_no_positions():
    account = {**SAMPLE_ACCOUNT, "positions": [], "_pool_details": []}
    msg = format_position_message(account, [], {})
    assert "No active positions" in msg


def test_format_message_funding_two_lines():
    positions = parse_positions(SAMPLE_ACCOUNT)
    # market_id가 없는 경우
    msg_no_rate = format_position_message(SAMPLE_ACCOUNT, positions, {})
    assert "💸 Funding +$1.50" in msg_no_rate
    assert "  ⏰" in msg_no_rate

    # market_id가 있는 경우
    positions[0]["market_id"] = 1
    funding_rates = {1: 0.0001}
    msg_with_rate = format_position_message(SAMPLE_ACCOUNT, positions, funding_rates)
    assert "💸 Funding +$1.50" in msg_with_rate
    assert "%APR) ⏰" in msg_with_rate
