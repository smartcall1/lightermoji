"""/buynow 물타기 지금 — 모의 객체만 사용, 실주문·API 호출 없음."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import dca_engine
import lighter_bot as lb
from tests.test_liq_guard import raw_pos, account, MM

OWNER = 42


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(lb, "TELEGRAM_CHAT_ID", str(OWNER))
    monkeypatch.setattr(config, "MIN_AVAILABLE_BALANCE", 30.0)
    lb._buynow.clear()
    dca_engine._active_symbols.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(lb, "_buynow_now", lambda: clock["t"])
    return clock


@pytest.fixture
def clock(env):
    return env


def long_pos():
    # 4x 롱(imf 25), 지금가 100, isolated
    return raw_pos(mid=1, symbol="BTCUSD", size=10.0, mark=100.0, margin=250.0, liq=80.0, imf=25.0)


@pytest.fixture
def world(monkeypatch):
    w = MagicMock()
    w.acct = account([long_pos()], avail="500")
    monkeypatch.setattr(lb, "fetch_account", AsyncMock(side_effect=lambda: w.acct))
    monkeypatch.setattr(lb, "fetch_market_info", AsyncMock(return_value={"market_id": 1}))
    monkeypatch.setattr(lb, "fetch_market_margin", AsyncMock(return_value=MM))
    w.buy = AsyncMock(return_value=dca_engine.DCAResult(
        symbol="BTCUSD", filled_usdc=400.0, target_usdc=400.0, filled_amount=4.0, avg_price=100.0))
    monkeypatch.setattr(lb, "execute_buynow", w.buy)
    return w


def msg_update(text, chat=OWNER):
    u = MagicMock()
    u.effective_chat.id = chat
    u.message.text = text
    u.message.reply_text = AsyncMock()
    return u


def cb_update(data, chat=OWNER):
    u = MagicMock()
    u.effective_chat.id = chat
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u.callback_query = q
    u.edit_message_text, u.answer = q.edit_message_text, q.answer  # 시험 편의 별칭
    return u


def ctx():
    c = MagicMock()
    c.bot.send_message = AsyncMock()
    return c


async def to_confirm(amount="100"):
    await lb.cb_buynow_pick(cb_update("bn_pick:BTCUSD"), ctx())
    u = msg_update(amount)
    await lb.on_buynow_text(u, ctx())
    return u


# ── 금액 파싱 ──
@pytest.mark.parametrize("text,want", [("500", 500.0), (" 12.5 ", 12.5), ("abc", None), ("0", None),
                                       ("-5", None), ("nan", None), ("inf", None), ("1e3", None),
                                       ("1_000", None), ("", None), ("9" * 400, None)])
def test_parse_amount(text, want):
    assert lb.parse_buynow_amount(text) == want


def test_plan_notional_is_margin_times_leverage():
    p = lb.buynow_plan({"imf_raw": 25.0, "current": 100.0}, 100.0)
    assert p["lev"] == pytest.approx(4.0)
    assert p["notional"] == pytest.approx(400.0)
    assert p["qty"] == pytest.approx(4.0)


# ── 흐름 ──
@pytest.mark.asyncio
async def test_no_long_message(world):
    world.acct = account([raw_pos(sign=-1)])
    u = msg_update("/buynow")
    await lb.cmd_buynow(u, ctx())
    assert u.message.reply_text.call_args[0][0] == "열린 롱 없음"


@pytest.mark.asyncio
async def test_list_shows_long_buttons(world):
    u = msg_update("/buynow")
    await lb.cmd_buynow(u, ctx())
    kb = u.message.reply_text.call_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "bn_pick:BTCUSD"


@pytest.mark.asyncio
async def test_non_owner_rejected(world):
    u = msg_update("/buynow", chat=999)
    await lb.cmd_buynow(u, ctx())
    u.message.reply_text.assert_not_called()
    q = cb_update("bn_pick:BTCUSD", chat=999)
    await lb.cb_buynow_pick(q, ctx())
    assert lb._buynow == {}
    q2 = cb_update("bn_go:abcd", chat=999)
    await lb.cb_buynow_go(q2, ctx())
    world.buy.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_screen_and_single_order_on_double_click(world):
    u = await to_confirm("100")
    text = u.message.reply_text.call_args[0][0]
    assert "증거금 $100.00" in text and "4.0배" in text and "$400" in text and "예상" in text
    token = lb._buynow[OWNER]["token"]
    q1, q2 = cb_update(f"bn_go:{token}"), cb_update(f"bn_go:{token}")
    await lb.cb_buynow_go(q1, ctx())
    await lb.cb_buynow_go(q2, ctx())
    world.buy.assert_awaited_once()
    assert world.buy.call_args[0] == ("BTCUSD", pytest.approx(400.0))  # 명목가 = 증거금 x 레버리지
    assert "DCA 완료" in q1.edit_message_text.call_args[0][0]
    assert q2.answer.call_args[0][0].startswith("이미 처리")


@pytest.mark.asyncio
async def test_confirm_expires(world, clock):
    await to_confirm("100")
    token = lb._buynow[OWNER]["token"]
    clock["t"] += lb.BUYNOW_TTL_SEC + 1
    q = cb_update(f"bn_go:{token}")
    await lb.cb_buynow_go(q, ctx())
    world.buy.assert_not_called()
    assert "취소" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_amount_wait_expires(world, clock):
    await lb.cb_buynow_pick(cb_update("bn_pick:BTCUSD"), ctx())
    clock["t"] += lb.BUYNOW_TTL_SEC + 1
    u = msg_update("100")
    await lb.on_buynow_text(u, ctx())
    assert "시간이 지나" in u.message.reply_text.call_args[0][0]
    assert OWNER not in lb._buynow


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "0", "-1", "nan", "inf"])
async def test_bad_amount_ends_without_reasking(world, bad):
    await lb.cb_buynow_pick(cb_update("bn_pick:BTCUSD"), ctx())
    u = msg_update(bad)
    await lb.on_buynow_text(u, ctx())
    assert "다시 /buynow" in u.message.reply_text.call_args[0][0]
    assert OWNER not in lb._buynow
    u2 = msg_update("100")  # 이후 숫자는 무시
    await lb.on_buynow_text(u2, ctx())
    u2.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_text_without_pending_is_ignored(world):
    u = msg_update("100")
    await lb.on_buynow_text(u, ctx())
    u.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_low_balance_refused_on_confirm_screen(world):
    world.acct = account([long_pos()], avail="120")  # 120 - 100 = 20 < 30
    u = await to_confirm("100")
    assert "하한" in u.message.reply_text.call_args[0][0]
    assert OWNER not in lb._buynow
    world.buy.assert_not_called()


@pytest.mark.asyncio
async def test_low_balance_refused_at_execute(world):
    await to_confirm("100")
    token = lb._buynow[OWNER]["token"]
    world.acct = account([long_pos()], avail="120")  # 확인 뒤 잔고가 줄어든 경우
    q = cb_update(f"bn_go:{token}")
    await lb.cb_buynow_go(q, ctx())
    world.buy.assert_not_called()
    assert "하한" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_guard_in_progress_refused(world):
    await to_confirm("100")
    token = lb._buynow[OWNER]["token"]
    dca_engine._active_symbols.add("BTCUSD")
    q = cb_update(f"bn_go:{token}")
    await lb.cb_buynow_go(q, ctx())
    world.buy.assert_not_called()
    assert "가드 투입 중" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_buy_failure_message_shown(world):
    world.buy.return_value = dca_engine.DCAResult(
        symbol="BTCUSD", filled_usdc=0, target_usdc=400, filled_amount=0, avg_price=0,
        error="차단선 근처라 포지션 증가 불가 (거래소 거부)")
    await to_confirm("100")
    q = cb_update(f"bn_go:{lb._buynow[OWNER]['token']}")
    await lb.cb_buynow_go(q, ctx())
    assert "차단선 근처라 포지션 증가 불가" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_buy_exception_message_shown(world):
    world.buy.side_effect = RuntimeError("boom")
    await to_confirm("100")
    q = cb_update(f"bn_go:{lb._buynow[OWNER]['token']}")
    await lb.cb_buynow_go(q, ctx())
    assert "boom" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_wrong_token_does_not_order(world):
    await to_confirm("100")
    q = cb_update("bn_go:deadbeef")
    await lb.cb_buynow_go(q, ctx())
    world.buy.assert_not_called()
    assert OWNER in lb._buynow  # 올바른 토큰은 여전히 유효


@pytest.mark.asyncio
async def test_execute_buynow_busy_and_leaves_guard_state(monkeypatch, tmp_path):
    import liq_guard
    monkeypatch.setattr(liq_guard, "STATE_PATH", str(tmp_path / "state.json"))
    dca_engine._active_symbols.add("BTCUSD")
    res = await dca_engine.execute_buynow("BTCUSD", 400.0)
    assert res.error and res.filled_usdc == 0
    assert not (tmp_path / "state.json").exists()
