"""Lighter DCA Bot — Telegram 봇 진입점"""

import asyncio
import logging
import os
import sys
from datetime import time, timezone, timedelta

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TimedOut, NetworkError
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, filters

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    DCA_MARKETS, DCA_TIME_AEST, MONITOR_HOURS_AEST, AEST_OFFSET,
    add_dca_market, remove_dca_market,
)
import config
import liq_guard
import runtime_flags
from monitor import get_full_status, fetch_account, parse_positions, SYMBOL_NAMES

GUARD_JOB_KWARGS = {"misfire_grace_time": 120, "coalesce": True, "max_instances": 1}
_guard_job_interval: int | None = None
JOB_MISFIRE_GRACE_SEC = 1800  # 스케줄 잡이 이만큼 늦게 깨도 건너뛰지 않고 실행(11-05)


def register_jobs(jq):
    """DCA·모니터 잡 등록.

    11-05: 폰 부하로 스케줄러가 7~58분 늦게 깨면 APScheduler 기본 misfire_grace_time(1초)
    때문에 잡이 "was missed by" 경고만 남기고 통째로 건너뛰어졌다. 늦어도 30분 안이면 실행하고,
    여러 번 밀린 건 한 번으로 합친다(coalesce).
    """
    late_ok = {"misfire_grace_time": JOB_MISFIRE_GRACE_SEC, "coalesce": True}

    dca_h, dca_m = DCA_TIME_AEST
    dca_utc = _aest_to_utc(dca_h, dca_m)
    jq.run_daily(job_dca, time=dca_utc, name="dca_daily", job_kwargs=late_ok)
    log.info("DCA 잡 등록: AEST %02d:%02d (UTC %s)", dca_h, dca_m, dca_utc)

    for aest_h in MONITOR_HOURS_AEST:
        utc_t = _aest_to_utc(aest_h)
        jq.run_daily(job_monitor, time=utc_t, name=f"monitor_{aest_h:02d}aest", job_kwargs=late_ok)
    log.info("모니터 잡 등록: AEST %s", ", ".join(f"{h:02d}:00" for h in MONITOR_HOURS_AEST))

    # 방어 투입: 모드(off|shadow|live)는 data/runtime_flags.json 에서 매 실행 시점에 읽는다(off 면 즉시 반환).
    jq.run_repeating(
        job_liq_guard, interval=config.LIQ_GUARD_INTERVAL_SEC, first=30, name="liq_guard",
        job_kwargs=GUARD_JOB_KWARGS,
    )
    global _guard_job_interval
    _guard_job_interval = config.LIQ_GUARD_INTERVAL_SEC
from lighter_client import fetch_market_info, set_leverage, get_account_index
from dca_engine import (
    execute_dca, format_dca_notification, close_position, format_close_notification,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

UTC = timezone.utc
OWNER_FILTER = filters.Chat(int(TELEGRAM_CHAT_ID))

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lighter_bot.lock")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_lock() -> None:
    """중복 실행 방지용 PID 락. 죽은 프로세스가 남긴 락은 자동 정리."""
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            old_pid = None
            try:
                with open(LOCK_PATH) as f:
                    old_pid = int(f.read().strip())
            except (ValueError, OSError):
                pass
            if old_pid and _pid_alive(old_pid):
                log.error("이미 실행 중인 봇 프로세스 발견 (PID %d) — 종료", old_pid)
                sys.exit(1)
            log.warning("오래된 락 파일 발견 (PID %s, 죽은 프로세스) — 정리 후 재시도", old_pid)
            try:
                os.remove(LOCK_PATH)
            except OSError:
                pass
            continue
        else:
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return


def _release_lock() -> None:
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    markets_str = "\n".join(
        f"  • {sym}: ${amt:.0f}/일" for sym, amt in DCA_MARKETS.items()
    ) or "  (없음 — .env에 DCA_SYMBOL=금액 설정)"
    h, m = DCA_TIME_AEST
    await update.message.reply_text(
        f"⚡ Lighter DCA Bot\n\n"
        f"📋 DCA 종목:\n{markets_str}\n\n"
        f"⏰ 매수 시각: AEST {h:02d}:{m:02d}\n\n"
        f"명령어:\n"
        f"  /l         — 포지션 현황 (종료 버튼 포함)\n"
        f"  /dca       — DCA 수동 실행 (확인 버튼)\n"
        f"  /config    — 현재 DCA 설정\n"
        f"  /addcoin <티커> <금액> [레버리지]   — DCA 종목 추가/수정\n"
        f"  /removecoin <티커>                — DCA 종목 제거\n"
        f"  /leverage <티커> <배수>            — 레버리지만 변경\n"
        f"  /dcaoff, /dcaon                   — 정기 DCA 끄기/켜기\n"
        f"  /guard off|shadow|live            — 청산 방어 투입 모드\n"
        f"  /guard share <0~1>                — 담보 비중\n"
        f"  /guard interval <초>              — 감시 간격(5~300)\n"
        f"  /guardstatus                      — 방어 투입 현황"
    )


async def cmd_lighter(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Lighter 포지션 조회 중...")
    try:
        msg = await get_full_status()
        account = await fetch_account()
        positions = parse_positions(account) if account else []
        keyboard = None
        if positions:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"❌ {p['name']} 종료", callback_data=f"close_ask:{p['symbol']}")]
                for p in positions
            ])
        await update.message.reply_text(msg, reply_markup=keyboard)
    except Exception as e:
        log.exception("lighter status failed")
        await update.message.reply_text(f"❌ 조회 실패: {e}")


async def cmd_addcoin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if len(args) not in (2, 3):
        await update.message.reply_text(
            "사용법: /addcoin <티커> <금액> [레버리지]\n"
            "예: /addcoin NVDA 20\n"
            "예: /addcoin NVDA 20 5   (5배 레버리지로 설정)"
        )
        return
    ticker, amount_str = args[0], args[1]
    leverage_str = args[2] if len(args) == 3 else None
    ticker = ticker.upper().replace("USDC", "").replace("USD", "")
    symbol = f"{ticker}USD"
    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ 금액은 0보다 큰 숫자여야 해요.")
        return

    leverage = None
    if leverage_str is not None:
        try:
            leverage = int(leverage_str)
            if leverage <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ 레버리지는 1 이상의 정수여야 해요.")
            return

    try:
        market = await fetch_market_info(symbol)
    except ValueError as e:
        await update.message.reply_text(f"❌ 유효하지 않은 종목: {e}")
        return

    if leverage is not None:
        account_index = await get_account_index()
        err = await set_leverage(market["market_id"], leverage, account_index)
        if err:
            await update.message.reply_text(f"❌ {err}")
            return

    add_dca_market(symbol, amount)
    name = SYMBOL_NAMES.get(symbol, ticker)
    lev_str = f" ({leverage}x)" if leverage else ""
    await update.message.reply_text(f"✅ DCA 종목 추가/수정됨 — {name}: ${amount:.0f}/일{lev_str}")


async def cmd_leverage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if len(args) != 2:
        await update.message.reply_text("사용법: /leverage <티커> <배수>\n예: /leverage NVDA 5")
        return
    ticker, leverage_str = args
    ticker = ticker.upper().replace("USDC", "").replace("USD", "")
    symbol = f"{ticker}USD"
    try:
        leverage = int(leverage_str)
        if leverage <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ 레버리지는 1 이상의 정수여야 해요.")
        return

    try:
        market = await fetch_market_info(symbol)
    except ValueError as e:
        await update.message.reply_text(f"❌ 유효하지 않은 종목: {e}")
        return

    account_index = await get_account_index()
    err = await set_leverage(market["market_id"], leverage, account_index)
    name = SYMBOL_NAMES.get(symbol, ticker)
    if err:
        await update.message.reply_text(f"❌ {err}")
    else:
        await update.message.reply_text(f"✅ {name} 레버리지 {leverage}x로 설정됨")


async def cmd_removecoin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if len(args) != 1:
        await update.message.reply_text("사용법: /removecoin <티커>\n예: /removecoin NVDA")
        return
    ticker = args[0].upper().replace("USDC", "").replace("USD", "")
    symbol = f"{ticker}USD"
    name = SYMBOL_NAMES.get(symbol, ticker)
    if remove_dca_market(symbol):
        await update.message.reply_text(f"🗑️ DCA 종목 제거됨 — {name}")
    else:
        await update.message.reply_text(f"⚠️ {name}은(는) DCA 목록에 없었어요.")


def _is_owner(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.id == int(TELEGRAM_CHAT_ID)


async def cb_close_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("권한 없음", show_alert=True)
        return
    await query.answer()
    symbol = query.data.split(":", 1)[1]
    name = SYMBOL_NAMES.get(symbol, symbol.replace("USD", ""))
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 예, 종료", callback_data=f"close_yes:{symbol}"),
        InlineKeyboardButton("아니오", callback_data="close_no"),
    ]])
    await query.edit_message_text(f"⚠️ {name} 포지션을 시장가로 종료할까요?", reply_markup=keyboard)


async def cb_close_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("권한 없음", show_alert=True)
        return
    await query.answer()
    symbol = query.data.split(":", 1)[1]
    try:
        await query.edit_message_text(f"⏳ {symbol} 종료 중...")
    except Exception:
        log.warning("포지션 종료 콜백 메시지 수정 실패 (종료 실행 계속)")
    try:
        result = await close_position(symbol)
        msg = format_close_notification(result)
    except Exception as e:
        log.exception("close_position failed: %s", symbol)
        msg = f"❌ 종료 실패: {e}"
    try:
        await query.edit_message_text(msg)
    except Exception:
        log.warning("콜백 메시지 수정 실패, 일반 메시지로 대체 전송 시도: %s", symbol)
        await _send_safe_message(ctx.bot, chat_id=update.effective_chat.id, text=msg)


async def cb_close_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("권한 없음", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("취소됨")


async def _send_safe_message(bot, chat_id: int, text: str, retries: int = 1, **kwargs) -> bool:
    for attempt in range(retries + 1):
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return True
        except (TimedOut, NetworkError) as e:
            if attempt < retries:
                log.warning("텔레그램 전송 일시 지연(%s), 1초 후 재시도 (%d/%d)", e, attempt + 1, retries)
                await asyncio.sleep(1.0)
            else:
                log.warning("텔레그램 전송 최종 실패(%s), 다음 작업을 계속 진행합니다", e)
        except Exception as e:
            log.exception("텔레그램 전송 중 예외 발생: %s", e)
            return False
    return False


async def _execute_manual_dca(chat_id: int, bot, start_msg: str | None = None) -> None:
    if start_msg:
        await _send_safe_message(bot, chat_id=chat_id, text=start_msg)
    for symbol, usdc in DCA_MARKETS.items():
        try:
            result = await execute_dca(symbol, usdc)
            msg = format_dca_notification(result)
        except Exception as e:
            log.exception("DCA failed: %s", symbol)
            msg = f"❌ {symbol} DCA 실패: {e}"
        await _send_safe_message(bot, chat_id=chat_id, text=msg)


async def cmd_dca(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not DCA_MARKETS:
        await update.message.reply_text(
            "⚠️ DCA 종목이 설정되지 않았어요. .env에 DCA_SYMBOL=금액 추가 필요."
        )
        return

    args = ctx.args or []
    if args and args[0].lower() in ("confirm", "yes", "y", "go"):
        await _execute_manual_dca(
            update.effective_chat.id,
            ctx.bot,
            start_msg=f"🚀 DCA 수동 즉시 실행 시작 ({len(DCA_MARKETS)}종목)...",
        )
        return

    markets_summary = "\n".join(
        f"• {SYMBOL_NAMES.get(sym, sym)}: ${amt:.0f}" for sym, amt in DCA_MARKETS.items()
    )
    total_amount = sum(DCA_MARKETS.values())

    confirm_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"⚡ 예, DCA 실행 (${total_amount:.0f})", callback_data="dca_confirm"),
        ],
        [
            InlineKeyboardButton("❌ 취소", callback_data="dca_cancel"),
        ],
    ])
    msg = (
        f"⚠️ <b>[DCA 수동 실행 확인]</b>\n\n"
        f"📋 <b>실행 대상 종목 ({len(DCA_MARKETS)}개):</b>\n{markets_summary}\n\n"
        f"💰 <b>총 매수 금액:</b> ${total_amount:.0f}\n\n"
        f"지금 수동으로 DCA를 실행할까요?"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=confirm_markup)


async def cb_dca_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("권한 없음", show_alert=True)
        return
    await query.answer()

    if not DCA_MARKETS:
        await query.edit_message_text("⚠️ DCA 종목이 설정되지 않았어요.")
        return

    try:
        await query.edit_message_text(f"⏳ DCA 수동 실행 중 ({len(DCA_MARKETS)}종목)...")
    except Exception:
        log.warning("수동 DCA 콜백 메시지 수정 실패 (DCA 실행 계속)")
    await _execute_manual_dca(update.effective_chat.id, ctx.bot)


async def cb_dca_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("권한 없음", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("❌ <b>DCA 실행이 취소되었어요.</b>", parse_mode="HTML")


async def cmd_config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    h, m = DCA_TIME_AEST
    lines = [
        "⚙️ DCA 설정 현황",
        "─────────────────",
        f"⏰ 매수 시각: AEST {h:02d}:{m:02d}",
        f"📡 모니터링: AEST {', '.join(f'{x:02d}:00' for x in MONITOR_HOURS_AEST)}",
        "",
        "📋 DCA 종목:",
    ]
    if DCA_MARKETS:
        for sym, amt in DCA_MARKETS.items():
            lines.append(f"  • {sym}: ${amt:.0f}/일")
    else:
        lines.append("  (없음)")
    await update.message.reply_text("\n".join(lines))


async def job_monitor(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        msg = await get_full_status()
    except Exception as e:
        log.exception("monitor job failed")
        msg = f"❌ Lighter 자동 조회 실패: {e}"
    await _send_safe_message(ctx.bot, chat_id=int(TELEGRAM_CHAT_ID), text=msg)


async def job_liq_guard(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async def send(text: str) -> None:
        await _send_safe_message(ctx.bot, chat_id=int(TELEGRAM_CHAT_ID), text=text)
    try:
        await liq_guard.guard_tick(send)
    except Exception:
        log.exception("liq_guard job failed")
    _resched_guard(ctx)


def _resched_guard(ctx) -> None:
    """runtime_flags 의 감시 간격이 등록된 값과 다르면 잡을 새 간격으로 다시 등록한다(재시작 불필요)."""
    global _guard_job_interval
    try:
        want = runtime_flags.guard_interval_sec()
        if _guard_job_interval is None or want == _guard_job_interval or ctx.job_queue is None:
            return
        for j in ctx.job_queue.get_jobs_by_name("liq_guard"):
            j.schedule_removal()
        ctx.job_queue.run_repeating(job_liq_guard, interval=want, first=want, name="liq_guard",
                                    job_kwargs=GUARD_JOB_KWARGS)
        _guard_job_interval = want
        log.info("liq_guard 간격 변경: %d초", want)
    except Exception:
        log.exception("liq_guard 간격 재등록 실패")


async def cmd_dcaoff(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    runtime_flags.set_regular_dca(False)
    await update.message.reply_text("정기 DCA를 껐어요. 수동 /dca 는 그대로 쓸 수 있어요. 다시 켜려면 /dcaon")


async def cmd_dcaon(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    runtime_flags.set_regular_dca(True)
    await update.message.reply_text("정기 DCA를 켰어요.")


async def cmd_guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.lower() for a in (ctx.args or [])]
    usage = "사용법: /guard off|shadow|live  |  /guard share <0~1>  |  /guard interval <5~300초>"
    if len(args) == 2 and args[0] == "interval":
        try:
            sec = int(args[1])
            runtime_flags.set_guard_interval_sec(sec)
        except ValueError:
            await update.message.reply_text("간격은 5~300 사이 정수(초)여야 해요. 예: /guard interval 15")
            return
        await update.message.reply_text(f"감시 간격: {sec}초 (다음 확인부터 반영)")
        return
    if len(args) == 1 and args[0] in runtime_flags.GUARD_MODES:
        runtime_flags.set_guard_mode(args[0])
        await update.message.reply_text(f"방어 투입 모드: {args[0]}")
        return
    if len(args) == 2 and args[0] == "share":
        try:
            share = float(args[1])
            runtime_flags.set_collateral_min_share(share)
        except ValueError:
            await update.message.reply_text("share 는 0~1 사이 숫자여야 해요. 예: /guard share 1.0")
            return
        await update.message.reply_text(f"담보 비중: {share:.2f} (1.0 = 담보만, 0.3 = 담보 3 : 매수 7)")
        return
    await update.message.reply_text(usage)


async def cmd_guardstatus(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(liq_guard.format_status())


async def job_dca(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not runtime_flags.regular_dca_enabled():
        log.info("DCA 잡 건너뜀 — 정기 DCA 꺼짐(runtime_flags)")
        return
    log.info("DCA 잡 시작 — %d종목", len(DCA_MARKETS))
    for symbol, usdc in DCA_MARKETS.items():
        try:
            result = await execute_dca(symbol, usdc)
            msg = format_dca_notification(result)
        except Exception as e:
            log.exception("DCA job failed: %s", symbol)
            msg = f"❌ {symbol} DCA 실패: {e}"
        await _send_safe_message(ctx.bot, chat_id=int(TELEGRAM_CHAT_ID), text=msg)


def _aest_to_utc(aest_hour: int, aest_minute: int = 0) -> time:
    utc_hour = (aest_hour - AEST_OFFSET) % 24
    return time(hour=utc_hour, minute=aest_minute, tzinfo=UTC)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (TimedOut, NetworkError)):
        log.warning("Telegram 통신 일시 지연/타임아웃 (자동 복구됨): %s", context.error)
    else:
        log.error("Telegram 핸들러 예외 발생: %s", context.error, exc_info=context.error)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "봇 시작 및 도움말"),
        BotCommand("l", "Lighter 포지션 현황 조회"),
        BotCommand("dca", "DCA 수동 실행 (확인 단계 포함)"),
        BotCommand("config", "DCA 설정 현황 조회"),
        BotCommand("addcoin", "DCA 종목 추가/수정 <티커> <금액> [레버리지]"),
        BotCommand("removecoin", "DCA 종목 제거 <티커>"),
        BotCommand("leverage", "레버리지 변경 <티커> <배수>"),
        BotCommand("dcaoff", "정기 DCA 끄기"),
        BotCommand("dcaon", "정기 DCA 켜기"),
        BotCommand("guard", "방어 투입 모드 off|shadow|live / share <0~1>"),
        BotCommand("guardstatus", "방어 투입 현황"),
    ])


def run_bot() -> None:
    _acquire_lock()
    try:
        _run_bot_locked()
    finally:
        _release_lock()


def _run_bot_locked() -> None:
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=5.0,
    )
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("l", cmd_lighter, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("dca", cmd_dca, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("config", cmd_config, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("addcoin", cmd_addcoin, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("removecoin", cmd_removecoin, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("leverage", cmd_leverage, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("dcaoff", cmd_dcaoff, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("dcaon", cmd_dcaon, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("guard", cmd_guard, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("guardstatus", cmd_guardstatus, filters=OWNER_FILTER))
    app.add_handler(CallbackQueryHandler(cb_close_ask, pattern=r"^close_ask:"))
    app.add_handler(CallbackQueryHandler(cb_close_yes, pattern=r"^close_yes:"))
    app.add_handler(CallbackQueryHandler(cb_close_no, pattern=r"^close_no$"))
    app.add_handler(CallbackQueryHandler(cb_dca_confirm, pattern=r"^dca_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_dca_cancel, pattern=r"^dca_cancel$"))

    register_jobs(app.job_queue)

    log.info("Lighter DCA Bot 시작 — polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
