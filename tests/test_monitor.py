from monitor import parse_positions, format_position_message, fmt_price, _fmt_usd

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


def test_fmt_usd_no_k_abbreviation():
    assert _fmt_usd(18687.0) == "$18,687"
    assert _fmt_usd(18687.50) == "$18,687.50"
    assert _fmt_usd(3090.0) == "$3,090"
    assert _fmt_usd(7710.0, is_diff=True) == "+$7,710"
    assert _fmt_usd(-63.22, is_diff=True) == "$-63.22"
    assert _fmt_usd(151980.0) == "$151,980"
    assert _fmt_usd(23820.0, is_diff=True) == "+$23,820"


def test_format_message_has_symbol_and_number():
    positions = parse_positions(SAMPLE_ACCOUNT)
    msg = format_position_message(SAMPLE_ACCOUNT, positions, {})
    assert "1. 📈 SK하이닉스 L10x (Margin $100)" in msg


def test_format_message_multiple_positions_numbered():
    account = {
        "available_balance": "10680.0",
        "total_asset_value": "22620.0",
        "positions": [
            {
                "symbol": "ETHUSD",
                "position": "8.31",
                "avg_entry_price": "1895.59",
                "position_value": "18687.0",
                "unrealized_pnl": "2926.17",
                "liquidation_price": "1542.56",
                "allocated_margin": "3090.0",
                "initial_margin_fraction": "20",
                "total_funding_paid_out": "-63.22",
                "sign": 1,
            },
            {
                "symbol": "BTCUSD",
                "position": "0.25",
                "avg_entry_price": "64116.10",
                "position_value": "17030.0",
                "unrealized_pnl": "1297.09",
                "liquidation_price": "52140.33",
                "allocated_margin": "3090.0",
                "initial_margin_fraction": "20",
                "total_funding_paid_out": "-53.96",
                "sign": 1,
            },
        ],
        "_pool_details": [
            {
                "name": "LLP",
                "principal": 116600.0,
                "equity": 119030.0,
                "lp_pnl": 2430.0,
                "apy": 11.65,
            }
        ]
    }
    positions = parse_positions(account)
    msg = format_position_message(account, positions, {})

    assert "1. 📈 ETH L5x (Margin $3,090)" in msg
    assert "$1,895.59→$2,248.74 (8.31, $18,687)" in msg
    assert "2. 📈 BTC L5x (Margin $3,090)" in msg
    assert "$64,116.10→$68,120 (0.25, $17,030)" in msg
    assert "Available $10,680 | Total $22,620" in msg
    assert "🏦 LP $119,030 (🟢+$2,430)" in msg
    assert "LLP $119,030 (+$2,430) +11.65%" in msg
    assert "k" not in msg.lower() or "skhynix" in msg.lower()  # k 축약이 없어야 함


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

