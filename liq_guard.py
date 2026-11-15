"""청산 차단선 직전 방어 투입(Liq Guard) — 명세 docs/LIQ_DCA_SPEC_1113.md

isolated 롱 포지션의 차단선(IMR)까지 거리가 가까워지면 담보를 먼저 넣고, 남는 금액으로 매수한다.
대상은 DCA_MARKETS 와 무관하게 열린 롱·isolated·liq>0 포지션 전부(이 모듈은 config.DCA_MARKETS 를 읽지 않는다).
모드(data/runtime_flags.json): off(아무것도 안 함) / shadow(계산·알림만) / live(실제 투입).
"""

import json
import logging
import os
import time

import config
import runtime_flags
from monitor import fetch_account, parse_positions, fmt_price
from lighter_client import fetch_market_info, fetch_market_margin
from dca_engine import execute_guard

log = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "liq_guard_state.json")

LIQ_MISMATCH_TOL = 0.005  # 계산 부분청산가와 API 청산가 허용 오차(0.5%)

_now = time.time  # 시험에서 교체

_alert_last: dict[str, float] = {}      # 알림 키 -> 마지막 전송 시각 (쿨다운)
_shadow_last: dict[str, float] = {}     # 종목 -> 마지막 그림자 알림 시각 (1시간 1회)
_shadow_in_trigger: set[str] = set()    # 그림자에서 트리거 상태였던 종목 (진입 시점 알림용)
_market_cache: dict[str, tuple] = {}     # 종목 -> (시각, market_info, 증거금 비율 원본) 1시간 캐시
_status: dict[str, dict] = {}           # 종목별 마지막 계산값 (/guardstatus)
_last_check_ts: float | None = None


# ── 순수 계산 ────────────────────────────────────────────────

def _frac(raw) -> float | None:
    """orderBookDetails 비율(1/10000 단위 정수) -> 0~1 분수. 못 읽거나 0 이하·1 이상이면 None."""
    try:
        v = float(raw) / 10_000.0
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 1 else None


def market_fractions(mm: dict | None) -> tuple[float, float] | None:
    """(차단선 IMF, MMF). 필수 값을 못 읽으면 None - 대체값 없이 투입하지 않는다(fail closed)."""
    if not mm:
        return None
    mmf = _frac(mm.get("maintenance_margin_fraction"))
    mn = _frac(mm.get("min_initial_margin_fraction"))
    if mmf is None or mn is None:
        return None
    if config.LIQ_GUARD_BLOCK_IMF_SOURCE == "min":
        return mn, mmf
    df = _frac(mm.get("default_initial_margin_fraction"))
    if df is None:
        return None
    return max(mn, df), mmf


def compute_levels(pos: dict, mm: dict | None = None) -> dict | None:
    """차단선·부분청산선 계산. 시장 비율(mm)이 없거나 비정상이면 None.

    E = allocated_margin + unrealized_pnl (allocated_margin 은 미실현손익 제외 담보 - 폰 실측 검산으로 확인).
    차단선 IMF(imf_b)는 시장 비율, MMF 는 시장 maintenance_margin_fraction. 포지션 레버리지(imf_raw)는
    매수 명목 계산(lev)에만 쓴다. API liquidation_price 는 MMF 선(부분청산 시작선)과 일치.
    """
    fr = market_fractions(mm)
    imf_raw = float(pos.get("imf_raw") or 0)
    n = abs(float(pos.get("value") or 0))
    q = abs(float(pos.get("size") or 0))
    if fr is None or imf_raw <= 0 or n <= 0 or q <= 0:
        return None
    imf_b, mmf = fr
    e = float(pos.get("margin") or 0) + float(pos.get("upnl") or 0)
    a = e / n
    return {
        "imf": imf_b, "mmf": mmf, "e": e, "n": n, "q": q, "lev": 100.0 / imf_raw,
        "x_block": (a - imf_b) / (1 - imf_b) * 100.0,
        "x_part": (a - mmf) / (1 - mmf) * 100.0,
        "p_part": (n - e) / (q * (1 - mmf)),
        "mark": float(pos.get("current") or 0),
    }


def post_levels(lv: dict, collateral: float, buy_margin: float) -> dict:
    """투입 후 예상 거리: E2=E+B, 매수분(마진 x 레버리지)만큼 N 증가."""
    e2 = lv["e"] + collateral + buy_margin
    add_n = buy_margin * lv["lev"]
    n2 = lv["n"] + add_n
    q2 = lv["q"] + (add_n / lv["mark"] if lv["mark"] > 0 else 0)
    imf, mmf = lv["imf"], lv["mmf"]
    a = e2 / n2
    return {
        "x_block": (a - imf) / (1 - imf) * 100.0,
        "x_part": (a - mmf) / (1 - mmf) * 100.0,
        "p_part": (n2 - e2) / (q2 * (1 - mmf)),
    }


def plan_injection(lv: dict, budget: float, share: float, target_pct: float) -> dict:
    """담보 먼저 -> 목표 완충(투입 후 x_block=TARGET)까지 채운 뒤 남는 금액만 매수.

    post_levels 모델을 그대로 푼다: (E+B)/(N+m*L) = k, k = imf_b + XT*(1-imf_b)  =>  m = ((E+B)/k - N)/L.
    부분청산 구간(x_part<0)이면 매수 없이 담보만.
    """
    imf, n, e, lev = lv["imf"], lv["n"], lv["e"], lv["lev"]
    xt = target_pct / 100.0
    k = imf + xt * (1 - imf)
    raw_m = ((e + budget) / k - n) / lev
    partial_zone = lv["x_part"] < 0
    if partial_zone:
        m = 0.0
    elif config.LIQ_GUARD_SPLIT == "target":
        # share = 담보 최소 비중. 목표 공식으로 매수를 줄인다
        m = min(max(raw_m, 0.0), budget * (1 - share))
    else:
        # fixed: share = 담보 비중 그대로 (0.3 -> 담보 30 : 매수 70)
        m = budget * (1 - share)
    c = budget - m
    after = post_levels(lv, c, m)
    return {
        "collateral": c, "buy_margin": m, "notional": m * lev,
        "partial_zone": partial_zone, "unresolved": after["x_block"] < 0, "after": after,
    }


def select_guard_targets(positions: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """열린 롱·isolated·liq>0 만 대상. 나머지는 (포지션, 사유) 알림 목록. DCA_MARKETS 는 보지 않는다."""
    targets, skipped = [], []
    for p in positions:
        if p.get("sign") != 1 or p.get("side") != "Long":
            skipped.append((p, "숏 포지션 - 감시 제외 (봇 매수는 숏을 줄이는 방향)"))
        elif p.get("margin_mode") is None:
            skipped.append((p, "margin_mode 필드 없음 - 감시 제외"))
        elif p.get("margin_mode") != 1:
            skipped.append((p, "cross 포지션 - 감시 제외"))
        elif not (float(p.get("liq") or 0) > 0):
            skipped.append((p, "청산가 없음(liq=0) - 감시 제외"))
        elif float(p.get("imf_raw") or 0) <= 0:
            skipped.append((p, "증거금 비율 필드 없음 - 감시 제외"))
        else:
            targets.append(p)
    return targets, skipped


# ── REARM 상태 (파일 저장) ──────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items() if float(v) > 0}
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("liq_guard_state.json 읽기 실패 — 빈 상태로 시작: %r", e)
    return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def rearm_ok(last_mark: float | None, mark: float, rearm_pct: float) -> bool:
    """직전 투입가보다 rearm_pct% 이상 더 내려갔을 때만 (경계 포함). 기록 없으면 True."""
    if not last_mark:
        return True
    return mark <= last_mark * (1 - rearm_pct / 100.0)


# ── 알림 ─────────────────────────────────────────────────────

def _cooldown_ok(key: str) -> bool:
    now = _now()
    last = _alert_last.get(key)
    if last is not None and now - last < config.LIQ_GUARD_ALERT_COOLDOWN_SEC:
        return False
    _alert_last[key] = now
    return True


def _fmt_levels(lv: dict, pos: dict) -> str:
    return (f"차단선까지 {lv['x_block']:.1f}% / 부분청산선까지 {lv['x_part']:.1f}% "
            f"(청산가 {fmt_price(pos['liq'])})")


def _defense_msg(pos: dict, lv: dict, res, pos2: dict | None, lv2: dict | None,
                 avail_before: float, avail_after: float | None) -> str:
    """[방어 투입] 6줄 알림(싸장님 11-15 승인). 재조회 값이 없으면 계산한 예상값에 (예상)을 붙인다."""
    coll = res.collateral_usdc if res.collateral_sent else 0.0
    parts = []
    if coll > 0:
        parts.append(f"담보 ${coll:,.0f}")
    if res.buy_margin > 0:
        parts.append(f"매수 마진 ${res.buy_margin:,.0f}")
    if not parts:
        line2 = "투입 없음"
    elif res.buy_margin > 0:
        line2 = f"투입: {' + '.join(parts)} (명목 ${res.buy_margin * lv['lev']:,.0f})"
    else:
        line2 = f"투입: {parts[0]}"
    est = " (예상)"
    if pos2 is not None and lv2 is not None:
        liq_a, xb_a, xp_a, tag = pos2["liq"], lv2["x_block"], lv2["x_part"], ""
        entry_a = fmt_price(pos2["entry"])
    else:
        pl = post_levels(lv, coll, res.buy_margin)
        liq_a, xb_a, xp_a, tag = pl["p_part"], pl["x_block"], pl["x_part"], est
        entry = float(pos.get("entry") or 0)
        mark = lv["mark"]
        if entry > 0 and res.buy_margin <= 0:
            entry_a = fmt_price(entry) + est
        elif entry > 0 and mark > 0:
            d = res.dca
            if d is not None and (d.filled_amount or 0) > 0 and (d.avg_price or 0) > 0:
                add_q, px = d.filled_amount, d.avg_price  # 실제 체결 수량·평균가
            else:
                add_q, px = res.buy_margin * lv["lev"] / mark, mark
            entry_a = fmt_price((entry * lv["q"] + px * add_q) / (lv["q"] + add_q)) + est
        else:
            entry_a = "확인 못 함"
    mark_a = lv2["mark"] if (pos2 is not None and lv2 is not None) else lv["mark"]
    if avail_after is not None:
        avail_txt = f"${avail_after:,.0f}"
    else:
        avail_txt = f"${avail_before - coll - res.buy_margin:,.0f}{est}"
    return "\n".join([
        f"[방어 투입] {pos['name']} 롱 {lv['lev']:.0f}x · 라이터",
        line2,
        f"청산가: {fmt_price(pos['liq'])} → {fmt_price(liq_a)}{tag}",
        f"청산까지: {lv['x_part']:.1f}% → {xp_a:.1f}% (현재가 {fmt_price(mark_a)}){tag}",
        f"차단선까지: {lv['x_block']:.1f}% → {xb_a:.1f}%{tag}",
        f"평단: {fmt_price(pos['entry'])} → {entry_a}",
        f"가용 잔고: {avail_txt}",
    ])


def _log_raw(pos: dict, lv: dict | None, mm) -> None:
    log.info(
        "[GUARD-RAW] %s imf_raw=%s market_raw=%s allocated_margin=%s position_value=%s "
        "unrealized_pnl=%s api_liq=%s calc_p_part=%s x_block=%s x_part=%s",
        pos.get("symbol"), pos.get("imf_raw"), mm, pos.get("margin"), pos.get("value"),
        pos.get("upnl"), pos.get("liq"),
        f"{lv['p_part']:.6g}" if lv else None,
        f"{lv['x_block']:.3f}" if lv else None, f"{lv['x_part']:.3f}" if lv else None,
    )


async def _alert(send, key: str, text: str) -> None:
    if _cooldown_ok(key):
        await send(text)


# ── 한 번 확인 ───────────────────────────────────────────────

async def guard_tick(send) -> None:
    """send: async (text) -> None. 모드가 off 면 아무 조회도 하지 않는다."""
    global _last_check_ts
    mode = runtime_flags.guard_mode()
    if mode == "off":
        return
    try:
        account = await fetch_account()
        positions = parse_positions(account) if account else None
    except Exception as e:
        log.warning("[GUARD] 계정 조회 실패: %r", e)
        positions = None
    if positions is None:
        await _alert(send, "unknown", "[방어 확인] 알 수 없음 - 계정 조회 실패. 이번 확인에서는 투입하지 않았어요.")
        return
    _last_check_ts = _now()

    targets, skipped = select_guard_targets(positions)
    for pos, reason in skipped:
        await _alert(send, f"skip:{pos.get('market_id')}:{reason}",
                     f"[방어 확인] {pos['name']} - {reason}")

    state = load_state()
    open_ids = {str(p.get("market_id")) for p in positions}
    pruned = {k: v for k, v in state.items() if k in open_ids}
    # 가격이 직전 투입가 대비 REARM% 넘게 반등해야 기록 초기화(작은 등락으로 재투입 방지)
    for p in targets:
        k = str(p.get("market_id"))
        if k in pruned and p["current"] > pruned[k] * (1 + config.LIQ_GUARD_REARM_PCT / 100.0):
            del pruned[k]
    if pruned != state:
        save_state(pruned)
    state = pruned

    available = float(account.get("available_balance", "0") or 0)
    for pos in targets:  # 확인 한 번에 종목당 최대 1회
        try:
            available = await _handle_position(send, mode, pos, state, available)
        except Exception as e:
            log.exception("[GUARD] %s 처리 실패", pos.get("symbol"))
            await _alert(send, f"err:{pos.get('market_id')}", f"[방어 확인] {pos['name']} 처리 중 오류: {e}")


async def _handle_position(send, mode: str, pos: dict, state: dict, available: float) -> float:
    symbol, mid = pos["symbol"], str(pos.get("market_id"))
    cached = _market_cache.get(symbol)
    if cached and _now() - cached[0] < config.LIQ_GUARD_MARKET_CACHE_SEC:
        market, margin_info = cached[1], cached[2]
    else:
        try:
            market = await fetch_market_info(symbol)
        except Exception as e:
            await _alert(send, f"nomarket:{mid}", f"[방어 확인] {pos['name']} - 시장 정보 없음 ({e})")
            return available
        margin_info = await fetch_market_margin(market["market_id"])
        if market_fractions(margin_info) is not None:  # 읽기 실패·비정상 값은 캐시하지 않고 다음 확인에서 재시도
            _market_cache[symbol] = (_now(), market, margin_info)
    lv = compute_levels(pos, margin_info)
    _log_raw(pos, lv, margin_info)
    if lv is None:
        await _alert(send, f"nolv:{mid}", f"[방어 확인] {pos['name']} - 시장 증거금 비율을 읽지 못함/비정상 - 투입 안 함")
        return available
    api_liq = float(pos.get("liq") or 0)
    if api_liq <= 0 or abs(lv["p_part"] - api_liq) / api_liq > LIQ_MISMATCH_TOL:
        await _alert(send, f"mismatch:{mid}",
                     f"[방어 확인] 청산가 계산 불일치 - 투입 안 함 (계산 {fmt_price(lv['p_part'])} / API {fmt_price(api_liq)})")
        return available

    _status[symbol] = {"x_block": lv["x_block"], "x_part": lv["x_part"], "liq": pos["liq"],
                       "p_part": lv["p_part"], "mark": lv["mark"], "last_inject_mark": state.get(mid), "ts": _now(),
                       "lev": lv["lev"], "side": "롱"}
    triggered = lv["x_block"] <= config.LIQ_GUARD_TRIGGER_PCT
    share = runtime_flags.collateral_min_share()
    budget = config.LIQ_GUARD_BUDGET_USD
    plan = plan_injection(lv, budget, share, config.LIQ_GUARD_TARGET_PCT)

    if mode == "shadow":
        entered = triggered and symbol not in _shadow_in_trigger
        if triggered:
            _shadow_in_trigger.add(symbol)
        else:
            _shadow_in_trigger.discard(symbol)
        hourly = _now() - _shadow_last.get(symbol, -1e18) >= config.LIQ_GUARD_SHADOW_HOURLY_SEC
        if entered or hourly:
            _shadow_last[symbol] = _now()
            txt = f"[방어 그림자 - 주문 없음] {pos['name']} 롱 {lv['lev']:.0f}x\n현재: {_fmt_levels(lv, pos)}"
            txt += f"\n계산 부분청산가 {fmt_price(lv['p_part'])}"
            if triggered:
                a = plan["after"]
                txt += (f"\n만약 실행했다면: 담보 ${plan['collateral']:.0f} + 매수 마진 ${plan['buy_margin']:.0f} "
                        f"(명목 ${plan['notional']:.0f})\n투입 후: 차단선까지 {a['x_block']:.1f}% / "
                        f"부분청산선까지 {a['x_part']:.1f}% (청산가 {fmt_price(a['p_part'])})")
            await send(txt)
        return available

    # ── live ──
    if not triggered:
        return available
    if not rearm_ok(state.get(mid), lv["mark"], config.LIQ_GUARD_REARM_PCT):
        log.info("[GUARD] %s 트리거 상태이나 직전 투입가 대비 %.1f%% 미만 하락 - 재발동 대기",
                 symbol, config.LIQ_GUARD_REARM_PCT)
        return available
    if available - budget < config.MIN_AVAILABLE_BALANCE:
        await _alert(send, f"lowbal:{mid}",
                     f"[방어 불가 - 잔고 하한] {pos['name']} {_fmt_levels(lv, pos)}\n"
                     f"가용 ${available:.0f} - 투입 ${budget:.0f} < 하한 ${config.MIN_AVAILABLE_BALANCE:.0f}")
        return available
    if plan["partial_zone"]:
        await _alert(send, f"partial:{mid}",
                     f"[부분청산 구간 - 수동 확인] {pos['name']} {_fmt_levels(lv, pos)}\n담보만 투입하고 매수는 하지 않아요.")

    def replan(acc: dict) -> float:
        ps = [p for p in parse_positions(acc) if str(p.get("market_id")) == mid]
        lv2 = compute_levels(ps[0], margin_info) if ps else None
        if lv2 is None:
            return 0.0
        if config.LIQ_GUARD_SPLIT != "target":
            return plan["buy_margin"]  # fixed: 계획한 비율 그대로 (부분청산 구간이면 이미 0)
        # target: 담보가 반영된 상태에서, 남은 예산(계획 매수 마진) 안에서 다시 계산 - 줄이기만 한다
        return plan_injection(lv2, plan["buy_margin"], 0.0, config.LIQ_GUARD_TARGET_PCT)["buy_margin"]

    # 주문 전에 REARM 기록부터 남긴다: 중간에 예외·취소가 나도 같은 가격에서 다시 투입되지 않게(잠기는 쪽)
    prev = state.get(mid)
    state[mid] = lv["mark"]
    save_state(state)
    res = await execute_guard(
        symbol, market["market_id"], plan["collateral"], plan["buy_margin"], lv["lev"],
        replan=replan, max_retries=config.LIQ_GUARD_MAX_RETRIES,
    )
    if res.busy:  # 아무것도 안 했으니 기록을 되돌린다
        if prev is None:
            state.pop(mid, None)
        else:
            state[mid] = prev
        save_state(state)
        return available

    avail_before = available
    lv2 = pos2 = None
    avail_after = None
    try:
        acc2 = await fetch_account()
        ps = [p for p in parse_positions(acc2) if str(p.get("market_id")) == mid] if acc2 else []
        if ps:
            lv2 = compute_levels(ps[0], margin_info)
            if lv2:
                pos2 = ps[0]
        if acc2:
            available = float(acc2.get("available_balance", available) or available)
            avail_after = available
    except Exception as e:
        log.warning("[GUARD] 사후 조회 실패: %r", e)

    head = _defense_msg(pos, lv, res, pos2, lv2, avail_before, avail_after)
    note = ""
    if res.error:
        note += f"\n중단: {res.error}"
    if plan["unresolved"]:
        note += "\n차단 미해소 - 수동 확인"
    if res.error:
        await _alert(send, f"err:{mid}", head + note)  # 실패 반복 도배 방지(쿨다운)
    else:
        await send(head + note)
    return available


def format_status() -> str:
    """/guardstatus 표시(싸장님 11-15 승인 형식). 표시만 한다."""
    if _last_check_ts:
        last = f"마지막 {int(_now() - _last_check_ts)}초 전"
    else:
        last = "마지막 확인 아직 없음"
    dca = "켜짐" if runtime_flags.regular_dca_enabled() else "꺼짐"
    lines = [
        f"[방어 상태] 라이터 · {runtime_flags.guard_mode()}",
        f"{runtime_flags.guard_interval_sec()}초마다 확인 · {last} · 정기 DCA {dca}",
        f"투입 조건: 차단선까지 {config.LIQ_GUARD_TRIGGER_PCT:.1f}% 아래면 "
        f"${config.LIQ_GUARD_BUDGET_USD:,.0f} (담보 {runtime_flags.collateral_min_share() * 100:.0f}%), "
        f"{config.LIQ_GUARD_TARGET_PCT:.1f}%까지 회복이 목표",
        "",
    ]
    if not _status:
        lines.append("종목: 아직 확인 전")
        return "\n".join(lines)
    blocks = []
    for sym, s in _status.items():
        head = f"{sym} {s.get('side') or '롱'}"
        if s.get("lev"):
            head += f" {s['lev']:.0f}x"
        api_liq = float(s.get("liq") or 0)
        diff = ""
        if not (api_liq > 0 and abs(s["p_part"] - api_liq) / api_liq <= LIQ_MISMATCH_TOL):
            diff = f" · 계산 {fmt_price(s['p_part'])} — 다름!"
        mark = s.get("last_inject_mark")
        blocks.append("\n".join([
            head,
            f"청산까지: {s['x_part']:.1f}% (현재가 {fmt_price(s['mark'])} / 청산가 {fmt_price(s['liq'])}){diff}",
            f"차단선까지: {s['x_block']:.1f}%",
            f"마지막 투입: {fmt_price(mark)} 때" if mark else "마지막 투입: 없음",
        ]))
    return "\n".join(lines) + "\n" + "\n\n".join(blocks)
