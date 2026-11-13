"""청산 방어 투입(Liq Guard) — 모의 객체만 사용, 실주문·API 호출 없음."""
import json
import math
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import dca_engine
import liq_guard
import runtime_flags
import lighter_client


def raw_pos(mid=1, symbol="BTCUSD", size=10.0, mark=100.0, margin=346.6, upnl=0.0,
            sign=1, margin_mode=1, liq=80.0, imf=33.3333333, entry=100.0):
    return {
        "market_id": mid, "symbol": symbol, "position": str(size), "avg_entry_price": str(entry),
        "position_value": str(size * mark), "unrealized_pnl": str(upnl),
        "liquidation_price": str(liq), "allocated_margin": str(margin),
        "initial_margin_fraction": str(imf), "sign": sign, "margin_mode": margin_mode,
    }


def account(positions, avail="500"):
    return {"available_balance": avail, "positions": positions}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_flags, "FLAGS_PATH", str(tmp_path / "data" / "runtime_flags.json"))
    monkeypatch.setattr(liq_guard, "STATE_PATH", str(tmp_path / "data" / "liq_guard_state.json"))
    for d in (liq_guard._alert_last, liq_guard._shadow_last, liq_guard._status):
        d.clear()
    liq_guard._shadow_in_trigger.clear()
    monkeypatch.setattr(config, "LIQ_GUARD_COLLATERAL_MIN_SHARE", 0.5)
    monkeypatch.setattr(config, "LIQ_GUARD_BUDGET_USD", 100.0)
    monkeypatch.setattr(config, "MIN_AVAILABLE_BALANCE", 30.0)
    monkeypatch.setattr(config, "LIQ_GUARD_TRIGGER_PCT", 2.0)
    monkeypatch.setattr(config, "LIQ_GUARD_REARM_PCT", 1.0)
    monkeypatch.setattr(dca_engine, "GUARD_VERIFY_INTERVAL_SEC", 0)
    dca_engine._active_symbols.clear()


def set_mode(mode):
    runtime_flags.set_guard_mode(mode)


class World:
    """tick 수준 시험: fetch_account·시장 조회·execute_guard 를 모의."""
    def __init__(self, monkeypatch, acct):
        self.acct = acct
        self.sent = []
        self.guard = AsyncMock(return_value=dca_engine.GuardResult(
            symbol="X", collateral_sent=True, collateral_usdc=50, collateral_confirmed=True))
        monkeypatch.setattr(liq_guard, "fetch_account", AsyncMock(side_effect=lambda: self.acct))
        monkeypatch.setattr(liq_guard, "fetch_market_info",
                            AsyncMock(return_value={"market_id": 1, "price_decimals": 2, "size_decimals": 3,
                                                    "min_base_amount": 0.001}))
        monkeypatch.setattr(liq_guard, "fetch_market_margin", AsyncMock(return_value=None))
        monkeypatch.setattr(liq_guard, "execute_guard", self.guard)

    async def send(self, text):
        self.sent.append(text)

    async def tick(self):
        await liq_guard.guard_tick(self.send)


def parsed(p):
    from monitor import parse_positions
    return parse_positions({"positions": [p]})[0]


# ── 1. 공식 ──
def test_formula_table_a_and_plan():
    lv = liq_guard.compute_levels(parsed(raw_pos()))
    assert lv["x_block"] == pytest.approx(2.0, abs=0.02)
    assert lv["x_part"] == pytest.approx(18.33, abs=0.01)
    assert lv["p_part"] == pytest.approx(81.67, abs=0.01)
    p = liq_guard.plan_injection(lv, 100.0, 0.0, 4.0)  # 표 C: 담보 19.8 / 매수 80.2
    assert p["buy_margin"] == pytest.approx(80.25, abs=0.1)
    assert p["collateral"] == pytest.approx(19.75, abs=0.1)
    assert p["after"]["x_block"] == pytest.approx(4.0, abs=0.05)
    assert p["after"]["p_part"] == pytest.approx(80.0, abs=0.1)
    p5 = liq_guard.plan_injection(lv, 100.0, 0.5, 4.0)  # 담보 최소 비중 보장
    assert p5["buy_margin"] == pytest.approx(50.0) and p5["collateral"] == pytest.approx(50.0)


def test_table_b_blocked_zone_and_budget_short():
    lv = liq_guard.compute_levels(parsed(raw_pos(margin=333.3333, upnl=-30.9)))
    assert lv["x_block"] < 0 and lv["x_part"] == pytest.approx(12.80, abs=0.05)
    p = liq_guard.plan_injection(lv, 100.0, 0.0, 4.0)
    assert p["collateral"] + p["buy_margin"] == pytest.approx(100.0)
    # N=3000 은 예산이 차단 부족액보다 작아 전액 담보 + 차단 미해소
    big = liq_guard.compute_levels(parsed(raw_pos(size=30.0, margin=1000.0, upnl=-120.0)))
    pb = liq_guard.plan_injection(big, 100.0, 0.0, 4.0)
    assert pb["buy_margin"] == 0 and pb["collateral"] == pytest.approx(100.0) and pb["unresolved"]


# ── 2. 트리거 경계 ──
async def test_trigger_boundary(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(margin=346.6 + 1.0)]))
    lv = liq_guard.compute_levels(parsed(w.acct["positions"][0]))
    monkeypatch.setattr(config, "LIQ_GUARD_TRIGGER_PCT", lv["x_block"] - 0.01)  # 바로 위 -> 미발동
    await w.tick()
    assert w.guard.await_count == 0
    monkeypatch.setattr(config, "LIQ_GUARD_TRIGGER_PCT", lv["x_block"])  # 정확히 같음 -> 발동(<=)
    await w.tick()
    assert w.guard.await_count == 1


async def test_trigger_199_vs_201(monkeypatch):
    set_mode("live")
    # 마진을 조절해 x_block 1.99 / 2.01 근처 만들기: x_block = ((m/1000)-1/3)/(2/3)*100
    def margin_for(x):
        return (x / 100 * (2 / 3) + 1 / 3) * 1000
    w = World(monkeypatch, account([raw_pos(margin=margin_for(2.01))]))
    await w.tick()
    assert w.guard.await_count == 0
    w.acct = account([raw_pos(margin=margin_for(1.99))])
    await w.tick()
    assert w.guard.await_count == 1


# ── 3. 차단 구간 ──
async def test_blocked_zone_still_acts(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(margin=333.3333, upnl=-30.9)]))
    await w.tick()
    assert w.guard.await_count == 1 and w.guard.await_args.args[2] > 0


async def test_partial_zone_collateral_only(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(margin=150.0, upnl=-10.0)]))  # x_part < 0
    await w.tick()
    args = w.guard.await_args.args
    assert args[2] == pytest.approx(100.0) and args[3] == 0
    assert any("부분청산 구간" in s for s in w.sent)


# ── 5. 숏 / cross / liq=0 / 필드 누락 / 조회 실패 ──
@pytest.mark.parametrize("kw", [{"sign": -1}, {"margin_mode": 0}, {"liq": 0.0}, {"imf": 0}])
async def test_non_targets_alert_only(monkeypatch, kw):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(**kw)]))
    await w.tick()
    assert w.guard.await_count == 0 and w.sent


async def test_missing_margin_mode_alert_only(monkeypatch):
    set_mode("live")
    p = raw_pos()
    del p["margin_mode"]
    w = World(monkeypatch, account([p]))
    await w.tick()
    assert w.guard.await_count == 0 and any("margin_mode" in s for s in w.sent)


async def test_account_fetch_failure_is_unknown(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos()]))
    monkeypatch.setattr(liq_guard, "fetch_account", AsyncMock(side_effect=RuntimeError("timeout")))
    await w.tick()
    assert w.guard.await_count == 0 and any("알 수 없음" in s for s in w.sent)


# ── 6. 잔고 하한 ──
async def test_balance_floor(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos()], avail="129.9"))
    await w.tick()
    assert w.guard.await_count == 0 and any("잔고 하한" in s for s in w.sent)
    w.acct = account([raw_pos()], avail="130")
    await w.tick()
    assert w.guard.await_count == 1


# ── 7. 확인당 1회 / 잠금 ──
async def test_one_per_symbol_per_tick(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(), raw_pos(mid=2, symbol="ETHUSD")]))
    await w.tick()
    symbols = sorted(c.args[0] for c in w.guard.await_args_list)
    assert symbols == ["BTCUSD", "ETHUSD"]


async def test_execute_guard_busy_when_regular_dca_running(monkeypatch):
    add = AsyncMock(return_value=None)
    monkeypatch.setattr(lighter_client, "add_isolated_margin", add)
    dca_engine._active_symbols.add("BTCUSD")
    res = await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3)
    assert res.busy and add.await_count == 0


# ── 8. REARM ──
def test_rearm_boundary():
    assert liq_guard.rearm_ok(None, 100, 1.0)
    assert not liq_guard.rearm_ok(100.0, 100.0, 1.0)
    assert not liq_guard.rearm_ok(100.0, 99.01, 1.0)
    assert liq_guard.rearm_ok(100.0, 99.0, 1.0)


async def test_rearm_in_tick_and_persisted(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(margin=346.6)]))
    await w.tick()
    assert w.guard.await_count == 1
    await w.tick()  # 같은 가격 -> 재호출 0
    assert w.guard.await_count == 1
    liq_guard._alert_last.clear()  # 재시작 효과: 메모리 비우고 파일에서 복원
    with open(liq_guard.STATE_PATH, encoding="utf-8") as f:
        assert json.load(f) == {"1": pytest.approx(100.0)}
    await w.tick()
    assert w.guard.await_count == 1
    w.acct = account([raw_pos(mark=99.0, margin=346.6 * 0.99)])  # 1% 더 하락
    await w.tick()
    assert w.guard.await_count == 2


async def test_rearm_resets_when_price_recovers(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos()]))
    await w.tick()
    w.acct = account([raw_pos(mark=105.0, margin=400.0)])  # 투입가 위로 회복
    await w.tick()
    assert liq_guard.load_state() == {}
    w.acct = account([raw_pos()])
    await w.tick()
    assert w.guard.await_count == 2


# ── 9. execute_guard 실패 경로 ──
def _guard_world(monkeypatch, allocs, add_err=None):
    seq = [account([raw_pos(margin=a)]) for a in allocs]
    monkeypatch.setattr(dca_engine, "fetch_account", AsyncMock(side_effect=seq + [seq[-1]] * 10))
    monkeypatch.setattr(dca_engine, "get_account_index", AsyncMock(return_value=7))
    add = AsyncMock(return_value=add_err)
    monkeypatch.setattr(lighter_client, "add_isolated_margin", add)
    inner = AsyncMock(return_value=dca_engine.DCAResult("BTCUSD", 50, 150, 1.5, 100))
    monkeypatch.setattr(dca_engine, "_execute_dca_inner", inner)
    return add, inner


async def test_collateral_then_buy_in_order(monkeypatch):
    add, inner = _guard_world(monkeypatch, [300, 350])
    res = await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3.0)
    assert add.await_count == 1 and inner.await_count == 1 and res.collateral_confirmed
    assert inner.await_args.args == ("BTCUSD", 150.0)
    assert inner.await_args.kwargs["guard"] is True
    assert inner.await_args.kwargs["coi_base"] >= 1_000_000  # 정기 DCA(<1,000,000)와 안 겹침


async def test_collateral_not_reflected_no_buy(monkeypatch):
    add, inner = _guard_world(monkeypatch, [300, 300])
    res = await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3.0)
    assert add.await_count == 1 and inner.await_count == 0 and "반영 확인 실패" in res.error
    assert res.collateral_sent and not res.collateral_confirmed


async def test_update_margin_error_no_buy_no_retry(monkeypatch):
    add, inner = _guard_world(monkeypatch, [300], add_err="담보 추가 실패: boom")
    res = await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3.0)
    assert add.await_count == 1 and inner.await_count == 0 and res.error and not res.collateral_sent


async def test_buy_failure_reports_error_without_more_orders(monkeypatch):
    add, inner = _guard_world(monkeypatch, [300, 350])
    inner.return_value = dca_engine.DCAResult("BTCUSD", 0, 150, 0, 0, error="미체결 주문 취소 실패")
    res = await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3.0)
    assert inner.await_count == 1 and res.error == "미체결 주문 취소 실패"


async def test_replan_can_only_reduce_buy(monkeypatch):
    add, inner = _guard_world(monkeypatch, [300, 350])
    await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3.0, replan=lambda acc: 9999)
    assert inner.await_args.args[1] == pytest.approx(150.0)
    add, inner = _guard_world(monkeypatch, [300, 350])
    await dca_engine.execute_guard("BTCUSD", 1, 50, 50, 3.0, replan=lambda acc: 0)
    assert inner.await_count == 0


def test_check_safety_guard_skips_only_liq():
    pos = {"liq_dist": 1.0}
    acct = {"available_balance": "500"}
    assert dca_engine._check_safety(acct, pos, 5, 30) == dca_engine.DCASkipReason.LIQ_TOO_CLOSE
    assert dca_engine._check_safety(acct, pos, 5, 30, skip_liq=True) is None
    low = {"available_balance": "10"}
    assert dca_engine._check_safety(low, pos, 5, 30, skip_liq=True) == dca_engine.DCASkipReason.LOW_BALANCE


# ── 10. shadow ──
async def test_shadow_no_orders(monkeypatch):
    set_mode("shadow")
    add = AsyncMock()
    monkeypatch.setattr(lighter_client, "add_isolated_margin", add)
    w = World(monkeypatch, account([raw_pos()]))
    await w.tick()
    assert w.guard.await_count == 0 and add.await_count == 0
    assert w.sent and "[방어 그림자 - 주문 없음]" in w.sent[0] and "만약 실행했다면" in w.sent[0]
    n = len(w.sent)
    await w.tick()  # 1시간 안 + 이미 트리거 상태 -> 재알림 없음
    assert len(w.sent) == n


async def test_mode_off_does_nothing(monkeypatch):
    w = World(monkeypatch, account([raw_pos()]))
    await w.tick()
    assert liq_guard.fetch_account.await_count == 0 and w.guard.await_count == 0


# ── 스위치 / 파일 ──
def test_runtime_flags_defaults_missing_and_broken():
    assert runtime_flags.regular_dca_enabled() is True
    assert runtime_flags.guard_mode() == "off"
    os.makedirs(os.path.dirname(runtime_flags.FLAGS_PATH), exist_ok=True)
    with open(runtime_flags.FLAGS_PATH, "w", encoding="utf-8") as f:
        f.write("{깨진 json")
    assert runtime_flags.regular_dca_enabled() is True and runtime_flags.guard_mode() == "off"
    with open(runtime_flags.FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"regular_dca_enabled": "no", "guard_mode": "LIVE!!", "collateral_min_share": 7}, f)
    assert runtime_flags.regular_dca_enabled() is True
    assert runtime_flags.guard_mode() == "off"
    assert runtime_flags.collateral_min_share() == 0.5


def test_collateral_share_file_overrides_env_and_rejects_out_of_range(monkeypatch):
    monkeypatch.setattr(config, "LIQ_GUARD_COLLATERAL_MIN_SHARE", 0.5)
    assert runtime_flags.collateral_min_share() == 0.5
    runtime_flags.set_collateral_min_share(1.0)
    assert runtime_flags.collateral_min_share() == 1.0
    for bad in (-0.1, 1.01, math.nan, True):
        with pytest.raises(ValueError):
            runtime_flags.set_collateral_min_share(bad)
    assert runtime_flags.collateral_min_share() == 1.0


async def test_share_1_means_collateral_only_in_tick(monkeypatch):
    set_mode("live")
    runtime_flags.set_collateral_min_share(1.0)
    w = World(monkeypatch, account([raw_pos()]))
    await w.tick()
    args = w.guard.await_args.args
    assert args[2] == pytest.approx(100.0) and args[3] == 0


async def test_regular_dca_off_skips_job_but_manual_runs(monkeypatch):
    import lighter_bot
    ex = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(lighter_bot, "execute_dca", ex)
    monkeypatch.setattr(lighter_bot, "format_dca_notification", lambda r: "ok")
    monkeypatch.setattr(lighter_bot, "_send_safe_message", AsyncMock())
    monkeypatch.setattr(lighter_bot, "DCA_MARKETS", {"BTCUSD": 10.0})
    ctx = MagicMock()
    runtime_flags.set_regular_dca(False)
    await lighter_bot.job_dca(ctx)
    assert ex.await_count == 0
    await lighter_bot._execute_manual_dca(1, ctx.bot)  # 수동 /dca 는 막지 않음
    assert ex.await_count == 1
    runtime_flags.set_regular_dca(True)
    await lighter_bot.job_dca(ctx)
    assert ex.await_count == 2


async def test_commands_set_flags():
    import lighter_bot
    upd = MagicMock()
    upd.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = ["live"]
    await lighter_bot.cmd_guard(upd, ctx)
    assert runtime_flags.guard_mode() == "live"
    ctx.args = ["share", "1.5"]
    await lighter_bot.cmd_guard(upd, ctx)
    assert runtime_flags.collateral_min_share() == 0.5
    ctx.args = ["share", "1"]
    await lighter_bot.cmd_guard(upd, ctx)
    assert runtime_flags.collateral_min_share() == 1.0
    await lighter_bot.cmd_dcaoff(upd, ctx)
    assert runtime_flags.regular_dca_enabled() is False
    await lighter_bot.cmd_dcaon(upd, ctx)
    assert runtime_flags.regular_dca_enabled() is True
    await lighter_bot.cmd_guardstatus(upd, ctx)
    assert "담보 최소 비중" in upd.message.reply_text.await_args.args[0]


def test_register_jobs_has_liq_guard():
    from lighter_bot import register_jobs
    jq = MagicMock()
    register_jobs(jq)
    assert jq.run_repeating.call_args.kwargs["name"] == "liq_guard"


# ── 12. 설정 / DCA_MARKETS 무관 ──
def test_new_keys_not_parsed_as_dca_markets(monkeypatch):
    for k in ("LIQ_GUARD_BUDGET_USD", "REGULAR_DCA_ENABLED", "LIQ_GUARD_MODE"):
        monkeypatch.setenv(k, "1")
    assert not set(config._parse_dca_markets()) & {"ENABLED", "LIQ_GUARD_BUDGET_USD"}


async def test_targets_independent_of_dca_markets(monkeypatch):
    set_mode("live")
    monkeypatch.setattr(config, "DCA_MARKETS", {})
    w = World(monkeypatch, account([raw_pos()]))
    await w.tick()
    assert w.guard.await_count == 1
    src = open(liq_guard.__file__, encoding="utf-8").read().split(chr(34) * 3)[2]  # docstring 제외
    assert "config.DCA_MARKETS" not in src and "import DCA_MARKETS" not in src
