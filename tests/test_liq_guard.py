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


MM = {"maintenance_margin_fraction": 120, "min_initial_margin_fraction": 200,
      "default_initial_margin_fraction": 500}  # 폰 실측 ETH/BTC 시장값(1/10000 단위)


def pos_x(x, mid=1, symbol="BTCUSD", size=10.0, mark=100.0, **kw):
    """차단선(5%)까지 x% 인 롱 isolated 4x 포지션. 청산가는 시장 MMF(1.2%) 선으로 일관되게 만든다."""
    n = size * mark
    e = n * (0.05 + x / 100 * 0.95)
    liq = (n - e) / (size * (1 - 0.012))
    base = dict(mid=mid, symbol=symbol, size=size, mark=mark, margin=e, liq=liq, imf=25.0)
    base.update(kw)
    return raw_pos(**base)


def account(positions, avail="500"):
    return {"available_balance": avail, "positions": positions}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_flags, "FLAGS_PATH", str(tmp_path / "data" / "runtime_flags.json"))
    monkeypatch.setattr(liq_guard, "STATE_PATH", str(tmp_path / "data" / "liq_guard_state.json"))
    for d in (liq_guard._alert_last, liq_guard._shadow_last, liq_guard._status, liq_guard._market_cache):
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
        monkeypatch.setattr(liq_guard, "fetch_market_margin", AsyncMock(return_value=MM))
        monkeypatch.setattr(liq_guard, "execute_guard", self.guard)

    async def send(self, text):
        self.sent.append(text)

    async def tick(self):
        await liq_guard.guard_tick(self.send)


def parsed(p):
    from monitor import parse_positions
    return parse_positions({"positions": [p]})[0]


# ── 1. 공식 (폰 실측 고정값) ──
ETH = dict(mid=2, symbol="ETHUSD", size=3.2733, entry=2291.05, mark=6301.855359 / 3.2733,
           margin=1851.534580, upnl=-1197.448529, imf=25.0, liq=1746.361683)


def test_eth_real_values():
    lv = liq_guard.compute_levels(parsed(raw_pos(**ETH)), MM)
    assert lv["p_part"] == pytest.approx(1746.36, abs=0.5)  # API 청산가와 일치
    assert lv["x_block"] == pytest.approx(5.66, abs=0.02)
    assert lv["x_part"] == pytest.approx(9.29, abs=0.02)
    assert lv["lev"] == pytest.approx(4.0)
    assert lv["x_block"] > config.LIQ_GUARD_TRIGGER_PCT and lv["x_part"] > 0


def test_btc_real_values_match_api_liq():
    btc = dict(mid=3, symbol="BTCUSD", size=0.09050, entry=78464.8, mark=6210.0738 / 0.0905,
               margin=1748.004213, upnl=-890.990719, imf=25.0, liq=59868.256727)
    lv = liq_guard.compute_levels(parsed(raw_pos(**btc)), MM)
    assert lv["p_part"] == pytest.approx(59868.3, rel=0.0005)


def test_conservative_vs_min_source(monkeypatch):
    p = parsed(raw_pos(**ETH))
    cons = liq_guard.compute_levels(p, MM)
    monkeypatch.setattr(config, "LIQ_GUARD_BLOCK_IMF_SOURCE", "min")
    mn = liq_guard.compute_levels(p, MM)
    assert mn["imf"] == pytest.approx(0.02) and cons["imf"] == pytest.approx(0.05)
    assert mn["x_block"] > cons["x_block"]


@pytest.mark.parametrize("mm", [
    None, {},
    {"maintenance_margin_fraction": 0, "min_initial_margin_fraction": 200, "default_initial_margin_fraction": 500},
    {"maintenance_margin_fraction": 120, "min_initial_margin_fraction": 10000, "default_initial_margin_fraction": 500},
    {"maintenance_margin_fraction": 120, "min_initial_margin_fraction": 200},
    {"maintenance_margin_fraction": "x", "min_initial_margin_fraction": 200, "default_initial_margin_fraction": 500},
])
def test_missing_or_bad_market_ratios_is_none(mm):
    assert liq_guard.compute_levels(parsed(raw_pos(**ETH)), mm) is None


def test_plan_trigger_case_share_variants(monkeypatch):
    monkeypatch.setattr(config, "LIQ_GUARD_SPLIT", "target")
    # 같은 ETH 포지션에서 mark 만 낮춰 x_block 이 1.9% 가 되는 지점을 찾는다
    q = 3.2733
    mark0 = 6301.855359 / q
    tm = None
    for i in range(0, 3000):
        m = mark0 * (1 - i / 10000)
        n = q * m
        e = 1851.534580 + q * (m - 2291.05)
        if (e / n - 0.05) / 0.95 * 100 <= 1.9:
            tm = m
            break
    assert tm is not None
    p = raw_pos(**{**ETH, "mark": tm, "upnl": q * (tm - 2291.05), "liq": 1746.36})
    lv = liq_guard.compute_levels(parsed(p), MM)
    assert lv["x_block"] == pytest.approx(1.9, abs=0.05)
    only_collateral = liq_guard.plan_injection(lv, 100.0, 1.0, 4.0)
    assert only_collateral["collateral"] == 100.0 and only_collateral["buy_margin"] == 0
    half = liq_guard.plan_injection(lv, 100.0, 0.5, 4.0)
    assert 0 <= half["buy_margin"] <= 50 and half["collateral"] == pytest.approx(100 - half["buy_margin"])
    assert half["after"]["x_block"] >= 1.9  # 투입 후 차단선 거리가 더 나빠지지 않음
    # 공식 검산: 상한에 안 걸리는 예산이면 투입 후 x_block == 목표
    hits = [liq_guard.plan_injection(lv, b, 0.0, 4.0) for b in range(20, 400, 20)]
    interior = [p for p in hits if 0 < p["buy_margin"] < p["collateral"] + p["buy_margin"]]
    assert interior
    for p in interior:
        assert p["after"]["x_block"] == pytest.approx(4.0, abs=0.01)


def test_budget_short_full_collateral_unresolved(monkeypatch):
    monkeypatch.setattr(config, "LIQ_GUARD_SPLIT", "target")
    lv = liq_guard.compute_levels(parsed(pos_x(-20.0, size=100.0)), MM)
    pb = liq_guard.plan_injection(lv, 100.0, 0.0, 4.0)
    assert pb["buy_margin"] == 0 and pb["collateral"] == pytest.approx(100.0) and pb["unresolved"]


# ── 2. 트리거 경계 ──
async def test_trigger_boundary(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(2.5)]))
    lv = liq_guard.compute_levels(parsed(w.acct["positions"][0]), MM)
    monkeypatch.setattr(config, "LIQ_GUARD_TRIGGER_PCT", lv["x_block"] - 0.01)  # 바로 위 -> 미발동
    await w.tick()
    assert w.guard.await_count == 0
    monkeypatch.setattr(config, "LIQ_GUARD_TRIGGER_PCT", lv["x_block"])  # 정확히 같음 -> 발동(<=)
    await w.tick()
    assert w.guard.await_count == 1


async def test_trigger_199_vs_201(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(2.01)]))
    await w.tick()
    assert w.guard.await_count == 0
    w.acct = account([pos_x(1.99)])
    await w.tick()
    assert w.guard.await_count == 1


async def test_eth_real_values_not_triggered_in_tick(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([raw_pos(**ETH)]))
    await w.tick()
    assert w.guard.await_count == 0 and not any("불일치" in x for x in w.sent)


async def test_no_market_ratios_no_order(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    monkeypatch.setattr(liq_guard, "fetch_market_margin", AsyncMock(return_value=None))
    await w.tick()
    assert w.guard.await_count == 0 and any("시장 증거금 비율" in x for x in w.sent)


async def test_liq_mismatch_no_order(monkeypatch):
    set_mode("live")
    p = pos_x(1.9)
    w = World(monkeypatch, account([p]))
    ok_liq = float(p["liquidation_price"])
    w.acct = account([{**p, "liquidation_price": str(ok_liq * 1.006)}])
    await w.tick()
    assert w.guard.await_count == 0 and any("청산가 계산 불일치" in x for x in w.sent)
    w.acct = account([{**p, "liquidation_price": str(ok_liq * 1.004)}])
    await w.tick()
    assert w.guard.await_count == 1


# ── 3. 차단 구간 ──
async def test_blocked_zone_still_acts(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(-3.0)]))
    await w.tick()
    assert w.guard.await_count == 1 and w.guard.await_args.args[2] > 0


async def test_partial_zone_collateral_only(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(-6.0)]))  # x_part < 0
    await w.tick()
    args = w.guard.await_args.args
    assert args[2] == pytest.approx(100.0) and args[3] == 0
    assert any("부분청산 구간" in s for s in w.sent)


# ── 5. 숏 / cross / liq=0 / 필드 누락 / 조회 실패 ──
@pytest.mark.parametrize("kw", [{"sign": -1}, {"margin_mode": 0}, {"liq": 0.0}, {"imf": 0}])
async def test_non_targets_alert_only(monkeypatch, kw):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9, **kw)]))
    await w.tick()
    assert w.guard.await_count == 0 and w.sent


async def test_missing_margin_mode_alert_only(monkeypatch):
    set_mode("live")
    p = pos_x(1.9)
    del p["margin_mode"]
    w = World(monkeypatch, account([p]))
    await w.tick()
    assert w.guard.await_count == 0 and any("margin_mode" in s for s in w.sent)


async def test_account_fetch_failure_is_unknown(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    monkeypatch.setattr(liq_guard, "fetch_account", AsyncMock(side_effect=RuntimeError("timeout")))
    await w.tick()
    assert w.guard.await_count == 0 and any("알 수 없음" in s for s in w.sent)


# ── 6. 잔고 하한 ──
async def test_balance_floor(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)], avail="129.9"))
    await w.tick()
    assert w.guard.await_count == 0 and any("잔고 하한" in s for s in w.sent)
    w.acct = account([pos_x(1.9)], avail="130")
    await w.tick()
    assert w.guard.await_count == 1


# ── 7. 확인당 1회 / 잠금 ──
async def test_one_per_symbol_per_tick(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9), pos_x(1.9, mid=2, symbol="ETHUSD")]))
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
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    assert w.guard.await_count == 1
    await w.tick()  # 같은 가격 -> 재호출 0
    assert w.guard.await_count == 1
    liq_guard._alert_last.clear()  # 재시작 효과: 메모리 비우고 파일에서 복원
    with open(liq_guard.STATE_PATH, encoding="utf-8") as f:
        assert json.load(f) == {"1": pytest.approx(100.0)}
    await w.tick()
    assert w.guard.await_count == 1
    w.acct = account([pos_x(1.9, mark=99.0)])  # 1% 더 하락
    await w.tick()
    assert w.guard.await_count == 2


async def test_rearm_resets_when_price_recovers(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    w.acct = account([pos_x(10.0, mark=105.0)])  # 투입가 위로 회복(트리거 아님)
    await w.tick()
    assert liq_guard.load_state() == {}
    w.acct = account([pos_x(1.9)])
    await w.tick()
    assert w.guard.await_count == 2


async def test_rearm_kept_on_small_bounce(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    assert w.guard.await_count == 1
    w.acct = account([pos_x(1.9, mark=100.5)])  # +0.5%: 기록 유지, 재투입 0
    await w.tick()
    assert liq_guard.load_state() == {"1": pytest.approx(100.0)}
    assert w.guard.await_count == 1


async def test_rearm_cleared_on_big_bounce(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    w.acct = account([pos_x(1.9, mark=101.1)])  # +1.1%: 기록 삭제
    await w.tick()
    assert w.guard.await_count == 2  # 기록이 지워져 재투입됨
    assert liq_guard.load_state() == {"1": pytest.approx(101.1)}


async def test_rearm_recorded_for_buy_only_share_zero(monkeypatch):
    set_mode("live")
    runtime_flags.set_collateral_min_share(0.0)
    w = World(monkeypatch, account([pos_x(1.9)]))
    w.guard.return_value = dca_engine.GuardResult(
        symbol="X", collateral_sent=False, buy_margin=100.0,
        dca=dca_engine.DCAResult("BTCUSD", 400, 400, 4, 100))
    await w.tick()
    await w.tick()  # 같은 가격 재호출 -> 0
    assert w.guard.await_count == 1 and liq_guard.load_state() == {"1": pytest.approx(100.0)}


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
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    assert w.guard.await_count == 0 and add.await_count == 0
    assert w.sent and "[방어 그림자 - 주문 없음]" in w.sent[0] and "만약 실행했다면" in w.sent[0]
    n = len(w.sent)
    await w.tick()  # 1시간 안 + 이미 트리거 상태 -> 재알림 없음
    assert len(w.sent) == n


async def test_mode_off_does_nothing(monkeypatch):
    w = World(monkeypatch, account([pos_x(1.9)]))
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
    w = World(monkeypatch, account([pos_x(1.9)]))
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
    assert "분할: fixed" in upd.message.reply_text.await_args.args[0]


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
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    assert w.guard.await_count == 1
    src = open(liq_guard.__file__, encoding="utf-8").read().split(chr(34) * 3)[2]  # docstring 제외
    assert "config.DCA_MARKETS" not in src and "import DCA_MARKETS" not in src


# ── 고정 비율 / 간격 / 캐시 ──
def test_fixed_split_share_03():
    lv = liq_guard.compute_levels(parsed(pos_x(1.9)), MM)
    p = liq_guard.plan_injection(lv, 100.0, 0.3, 4.0)  # 기본 fixed
    assert p["collateral"] == pytest.approx(30.0) and p["buy_margin"] == pytest.approx(70.0)
    assert p["notional"] == pytest.approx(280.0)  # 4x
    pz = liq_guard.compute_levels(parsed(pos_x(-6.0)), MM)
    pp = liq_guard.plan_injection(pz, 100.0, 0.3, 4.0)  # 부분청산 구간: 담보만
    assert pp["collateral"] == pytest.approx(100.0) and pp["buy_margin"] == 0


async def test_fixed_split_not_reduced_by_replan(monkeypatch):
    set_mode("live")
    runtime_flags.set_collateral_min_share(0.3)
    w = World(monkeypatch, account([pos_x(1.9)]))
    await w.tick()
    args, kw = w.guard.await_args.args, w.guard.await_args.kwargs
    assert args[2] == pytest.approx(30.0) and args[3] == pytest.approx(70.0)
    assert kw["replan"](account([pos_x(50.0)])) == pytest.approx(70.0)


def test_interval_flag_range_and_default(monkeypatch):
    monkeypatch.setattr(config, "LIQ_GUARD_INTERVAL_SEC", 15)
    assert runtime_flags.guard_interval_sec() == 15
    runtime_flags.set_guard_interval_sec(30)
    assert runtime_flags.guard_interval_sec() == 30
    for bad in (4, 301, True, float("nan")):
        with pytest.raises(ValueError):
            runtime_flags.set_guard_interval_sec(bad)
    with open(runtime_flags.FLAGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"guard_interval_sec": 1}, f)
    assert runtime_flags.guard_interval_sec() == 15


async def test_market_lookups_cached_within_hour(monkeypatch):
    set_mode("live")
    liq_guard._market_cache.clear()
    t = [1000.0]
    monkeypatch.setattr(liq_guard, "_now", lambda: t[0])
    w = World(monkeypatch, account([pos_x(10.0)]))
    await w.tick()
    await w.tick()
    assert liq_guard.fetch_market_info.await_count == 1 and liq_guard.fetch_market_margin.await_count == 1
    assert liq_guard.fetch_account.await_count == 2  # 확인 1번 = 계정 조회 1회
    t[0] += 3601
    await w.tick()
    assert liq_guard.fetch_market_info.await_count == 2


async def test_interval_command_and_resched(monkeypatch):
    import lighter_bot
    upd = MagicMock()
    upd.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = ["interval", "30"]
    await lighter_bot.cmd_guard(upd, ctx)
    assert runtime_flags.guard_interval_sec() == 30
    ctx.args = ["interval", "3"]
    await lighter_bot.cmd_guard(upd, ctx)
    assert runtime_flags.guard_interval_sec() == 30
    monkeypatch.setattr(lighter_bot, "_guard_job_interval", 15)
    jctx = MagicMock()
    old = MagicMock()
    jctx.job_queue.get_jobs_by_name.return_value = [old]
    lighter_bot._resched_guard(jctx)
    old.schedule_removal.assert_called_once()
    assert jctx.job_queue.run_repeating.call_args.kwargs["interval"] == 30


async def test_rearm_locked_when_execute_guard_raises_after_collateral(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    w.guard.side_effect = RuntimeError("fetch failed after collateral")
    await w.tick()
    await w.tick()  # 같은 가격 -> 투입 0
    assert w.guard.await_count == 1
    assert len([x for x in w.sent if "처리 중 오류" in x]) == 1  # 오류 알림도 쿨다운


async def test_busy_does_not_lock_rearm(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    w.guard.return_value = dca_engine.GuardResult(symbol="X", busy=True)
    await w.tick()
    assert liq_guard.load_state() == {}
    await w.tick()
    assert w.guard.await_count == 2


async def test_failed_result_alert_has_cooldown_but_success_does_not(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    w.guard.return_value = dca_engine.GuardResult(symbol="X", collateral_sent=True, collateral_usdc=50,
                                                  error="담보 반영 확인 실패")
    await w.tick()
    liq_guard.save_state({})
    await w.tick()
    assert w.guard.await_count == 2
    assert len([x for x in w.sent if "중단" in x]) == 1


async def test_busy_restores_previous_mark_in_file(monkeypatch):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)]))
    liq_guard.save_state({"1": 150.0})  # 이전 투입 가격 (현재 mark 100 은 충분히 아래)
    w.guard.return_value = dca_engine.GuardResult(symbol="X", busy=True)
    await w.tick()
    assert w.guard.await_count == 1
    assert liq_guard.load_state() == {"1": 150.0}
    await w.tick()
    assert w.guard.await_count == 2


# ── [방어 투입] 6줄 형식 (싸장님 11-15 승인) ──
def _live_world(monkeypatch, **kw):
    set_mode("live")
    w = World(monkeypatch, account([pos_x(1.9)], avail="9876"))
    w.guard.return_value = dca_engine.GuardResult(
        symbol="X", collateral_sent=True, collateral_usdc=30, collateral_confirmed=True, buy_margin=70, **kw)
    return w


async def test_defense_msg_six_lines_refetch_ok(monkeypatch):
    w = _live_world(monkeypatch)
    await w.tick()
    lines = w.sent[-1].split("\n")
    print(w.sent[-1])
    assert len(lines) == 6
    assert lines[0].startswith("[방어 투입] ") and lines[0].endswith("롱 4x · 라이터")
    assert lines[1] == "투입: 담보 $30 + 매수 마진 $70 (명목 $280)"
    assert [l.split(":")[0] for l in lines[2:]] == ["청산가", "차단선까지", "평단", "가용 잔고"]
    assert " → " in lines[2] and " → " in lines[3] and "(부분청산선 " in lines[3]
    assert lines[4] == "평단: $100 → $100"
    assert lines[5] == "가용 잔고: $9,876"
    assert "(예상)" not in w.sent[-1]


async def test_defense_msg_refetch_fail_marks_estimate(monkeypatch):
    w = _live_world(monkeypatch)
    calls = {"n": 0}
    first = w.acct

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return first
        raise RuntimeError("net")
    monkeypatch.setattr(liq_guard, "fetch_account", flaky)
    await w.tick()
    lines = w.sent[-1].split("\n")
    print(w.sent[-1])
    assert len(lines) == 6
    assert lines[2].endswith("(예상)") and lines[3].endswith("(예상)")
    assert lines[4].endswith("(예상)") and lines[4].startswith("평단: $100 → $")
    assert lines[5] == "가용 잔고: $9,776 (예상)"  # 9,876 - 30 - 70


async def test_defense_msg_no_entry_basis(monkeypatch):
    res = dca_engine.GuardResult(symbol="X", collateral_sent=True, collateral_usdc=30, buy_margin=70)
    pos = parsed(pos_x(1.9, entry=0.0))
    lv = liq_guard.compute_levels(pos, MM)
    msg = liq_guard._defense_msg(pos, lv, res, None, None, 500.0, None)
    assert msg.split("\n")[4] == "평단: $0 → 확인 못 함"


async def test_defense_msg_failure_has_abort_line_and_unresolved(monkeypatch):
    w = _live_world(monkeypatch, error="담보 반영 확인 실패")
    monkeypatch.setattr(config, "LIQ_GUARD_BUDGET_USD", 1.0)  # 예산이 모자라 차단 미해소
    w.acct = account([pos_x(-0.5)], avail="9876")
    await w.tick()
    txt = w.sent[-1]
    print(txt)
    assert txt.split("\n")[0].endswith("· 라이터")
    assert "\n중단: 담보 반영 확인 실패" in txt
    assert txt.endswith("차단 미해소 - 수동 확인")


async def test_defense_msg_no_injection(monkeypatch):
    w = _live_world(monkeypatch)
    w.guard.return_value = dca_engine.GuardResult(symbol="X", error="x")
    await w.tick()
    assert w.sent[-1].split("\n")[1] == "투입 없음"
