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

_now = time.time  # 시험에서 교체

_alert_last: dict[str, float] = {}      # 알림 키 -> 마지막 전송 시각 (쿨다운)
_shadow_last: dict[str, float] = {}     # 종목 -> 마지막 그림자 알림 시각 (1시간 1회)
_shadow_in_trigger: set[str] = set()    # 그림자에서 트리거 상태였던 종목 (진입 시점 알림용)
_status: dict[str, dict] = {}           # 종목별 마지막 계산값 (/guardstatus)
_last_check_ts: float | None = None


# ── 순수 계산 ────────────────────────────────────────────────

def compute_levels(pos: dict, mmf_raw=None) -> dict | None:
    """명세 1.2. 필수 값(imf, 명목가, 수량)이 없으면 None.

    mmf_raw: orderBookDetails maintenance_margin_fraction 원본(단위 미확정 — 10,000분율로 추정).
    MMF = max(MMF_RATIO x IMF, 읽은 값). 읽은 값이 비정상(<=0, IMF 이상)이면 버리고 대체값 사용.
    """
    imf_raw = float(pos.get("imf_raw") or 0)
    n = abs(float(pos.get("value") or 0))
    q = abs(float(pos.get("size") or 0))
    if imf_raw <= 0 or n <= 0 or q <= 0:
        return None
    imf = imf_raw / 100.0
    if imf >= 1:
        return None
    mmf = config.LIQ_GUARD_MMF_RATIO * imf
    parsed = None
    try:
        parsed = float(mmf_raw) / 10_000.0 if mmf_raw is not None else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None and 0 < parsed < imf:
        mmf = max(mmf, parsed)
    e = float(pos.get("margin") or 0) + float(pos.get("upnl") or 0)
    a = e / n
    return {
        "imf": imf, "mmf": mmf, "e": e, "n": n, "q": q, "lev": 1.0 / imf,
        "x_block": (a - imf) / (1 - imf) * 100.0,
        "x_part": (a - mmf) / (1 - mmf) * 100.0,
        "p_part": (n - e) / (q * (1 - mmf)),
        "mark": float(pos.get("current") or 0),
    }


def post_levels(lv: dict, collateral: float, buy_margin: float) -> dict:
    """투입 후 예상 거리 (명세 2.2: E2=E+B, 매수분만큼 N 증가, 평단 가중)."""
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
    """담보 먼저 -> 목표 완충까지 채운 뒤 남는 금액만 매수 (명세 2.6).

    부분청산 구간(x_part<0)이면 매수 없이 담보만.
    """
    imf, n, e, lev = lv["imf"], lv["n"], lv["e"], lv["lev"]
    xt = target_pct / 100.0
    raw_m = (e + budget - imf * n - xt * (1 - imf) * n) / (1 + xt * (1 - imf) * lev)
    partial_zone = lv["x_part"] < 0
    if partial_zone:
        m = 0.0
    else:
        m = min(max(raw_m, 0.0), budget * (1 - share))
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


def _log_raw(pos: dict, lv: dict | None, mmf_raw) -> None:
    log.info(
        "[GUARD-RAW] %s imf_raw=%s mmf_raw=%s allocated_margin=%s position_value=%s "
        "unrealized_pnl=%s api_liq=%s calc_p_part=%s x_block=%s x_part=%s",
        pos.get("symbol"), pos.get("imf_raw"), mmf_raw, pos.get("margin"), pos.get("value"),
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
    # 가격이 직전 투입가 위로 회복하면 기록 초기화
    for p in targets:
        k = str(p.get("market_id"))
        if k in pruned and p["current"] > pruned[k]:
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
    try:
        market = await fetch_market_info(symbol)
    except Exception as e:
        await _alert(send, f"nomarket:{mid}", f"[방어 확인] {pos['name']} - 시장 정보 없음 ({e})")
        return available
    margin_info = await fetch_market_margin(market["market_id"])
    mmf_raw = (margin_info or {}).get("maintenance_margin_fraction")
    lv = compute_levels(pos, mmf_raw)
    _log_raw(pos, lv, mmf_raw)
    if lv is None:
        await _alert(send, f"nolv:{mid}", f"[방어 확인] {pos['name']} - 증거금 계산 불가(필드 이상) - 투입 안 함")
        return available

    _status[symbol] = {"x_block": lv["x_block"], "x_part": lv["x_part"], "liq": pos["liq"],
                       "p_part": lv["p_part"], "last_inject_mark": state.get(mid), "ts": _now()}
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
        lv2 = compute_levels(ps[0], mmf_raw) if ps else None
        if lv2 is None:
            return 0.0
        # 담보가 반영된 상태에서, 남은 예산(계획 매수 마진) 안에서 다시 계산 - 줄이기만 한다
        return plan_injection(lv2, plan["buy_margin"], 0.0, config.LIQ_GUARD_TARGET_PCT)["buy_margin"]

    res = await execute_guard(
        symbol, market["market_id"], plan["collateral"], plan["buy_margin"], lv["lev"],
        replan=replan, max_retries=config.LIQ_GUARD_MAX_RETRIES,
    )
    if res.busy:
        return available
    if res.collateral_sent:
        state[mid] = lv["mark"]
        save_state(state)

    after_txt = ""
    try:
        acc2 = await fetch_account()
        ps = [p for p in parse_positions(acc2) if str(p.get("market_id")) == mid] if acc2 else []
        if ps:
            lv2 = compute_levels(ps[0], mmf_raw)
            if lv2:
                after_txt = (f"\n투입 후: {_fmt_levels(lv2, ps[0])}\n평단 {fmt_price(pos['entry'])} -> "
                             f"{fmt_price(ps[0]['entry'])}  |  가용 잔고 ${float(acc2.get('available_balance', 0)):.0f}")
        if acc2:
            available = float(acc2.get("available_balance", available) or available)
    except Exception as e:
        log.warning("[GUARD] 사후 조회 실패: %r", e)

    head = f"[방어 투입] {pos['name']} 롱 {lv['lev']:.0f}x\n투입 전: {_fmt_levels(lv, pos)}\n"
    head += (f"투입: 담보 ${res.collateral_usdc:.0f} + 매수 마진 ${res.buy_margin:.0f} "
             f"(명목 ${res.buy_margin * lv['lev']:.0f})") if res.collateral_sent or res.buy_margin else "투입 없음"
    note = ""
    if res.error:
        note += f"\n중단: {res.error}"
    if plan["unresolved"]:
        note += "\n차단 미해소 - 수동 확인"
    await send(head + after_txt + note)
    return available


def format_status() -> str:
    lines = [
        f"방어 투입 모드: {runtime_flags.guard_mode()}",
        f"정기 DCA: {'켜짐' if runtime_flags.regular_dca_enabled() else '꺼짐'}",
        f"담보 최소 비중: {runtime_flags.collateral_min_share():.2f} | 1회 ${config.LIQ_GUARD_BUDGET_USD:.0f} | "
        f"트리거 {config.LIQ_GUARD_TRIGGER_PCT}% / 목표 {config.LIQ_GUARD_TARGET_PCT}%",
    ]
    if _last_check_ts:
        lines.append(f"마지막 확인: {int(_now() - _last_check_ts)}초 전")
    else:
        lines.append("마지막 확인: 아직 없음")
    state = load_state()
    if not _status:
        lines.append("(종목별 계산값 아직 없음)")
    for sym, s in _status.items():
        lines.append(f"- {sym}: 차단선까지 {s['x_block']:.1f}% / 부분청산선까지 {s['x_part']:.1f}% "
                     f"(API 청산가 {fmt_price(s['liq'])}, 계산 {fmt_price(s['p_part'])})")
    if state:
        lines.append("마지막 투입가: " + ", ".join(f"market {k}: {fmt_price(v)}" for k, v in state.items()))
    else:
        lines.append("마지막 투입가: 없음")
    return "\n".join(lines)
