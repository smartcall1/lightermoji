"""Lighter DCA Bot — Telegram 봇 진입점"""

import logging
from datetime import time, timezone, timedelta

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, filters

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    DCA_MARKETS, DCA_TIME_AEST, MONITOR_HOURS_AEST, AEST_OFFSET,
    add_dca_market, remove_dca_market,
)
from monitor import get_full_status, fetch_account, parse_positions, SYMBOL_NAMES
from lighter_client import fetch_market_info, set_leverage, get_account_index
from dca_engine import (
    execute_dca, format_dca_notification, close_position, format_close_notification,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

UTC = timezone.utc
OWNER_FILTER = filters.Chat(int(TELEGRAM_CHAT_ID)) if TELEGRAM_CHAT_ID else filters.ALL


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
        f"  /dca       — DCA 즉시 실행\n"
        f"  /config    — 현재 DCA 설정\n"
        f"  /addcoin <티커> <금액> [레버리지]   — DCA 종목 추가/수정\n"
        f"  /removecoin <티커>                — DCA 종목 제거\n"
        f"  /leverage <티커> <배수>            — 레버리지만 변경"
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
        await update.message.reply_text("⚠️ 금액은 0보다 큰 숫자여야 하오.")
        return

    leverage = None
    if leverage_str is not None:
        try:
            leverage = int(leverage_str)
            if leverage <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ 레버리지는 1 이상의 정수여야 하오.")
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
        await update.message.reply_text("⚠️ 레버리지는 1 이상의 정수여야 하오.")
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
        await update.message.reply_text(f"⚠️ {name}은(는) DCA 목록에 없었소.")


def _is_owner(update: Update) -> bool:
    return not TELEGRAM_CHAT_ID or update.effective_chat.id == int(TELEGRAM_CHAT_ID)


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
    await query.edit_message_text(f"⚠️ {name} 포지션을 시장가로 종료하시겠소?", reply_markup=keyboard)


async def cb_close_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("권한 없음", show_alert=True)
        return
    await query.answer()
    symbol = query.data.split(":", 1)[1]
    await query.edit_message_text(f"⏳ {symbol} 종료 중...")
    try:
        result = await close_position(symbol)
        msg = format_close_notification(result)
    except Exception as e:
        log.exception("close_position failed: %s", symbol)
        msg = f"❌ 종료 실패: {e}"
    await query.edit_message_text(msg)


async def cb_close_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("취소됨")


async def cmd_dca(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not DCA_MARKETS:
        await update.message.reply_text(
            "⚠️ DCA 종목이 설정되지 않았소. .env에 DCA_SYMBOL=금액 추가 필요."
        )
        return
    await update.message.reply_text(f"🚀 DCA 수동 실행 시작 ({len(DCA_MARKETS)}종목)...")
    for symbol, usdc in DCA_MARKETS.items():
        try:
            result = await execute_dca(symbol, usdc)
            msg = format_dca_notification(result)
        except Exception as e:
            log.exception("DCA failed: %s", symbol)
            msg = f"❌ {symbol} DCA 실패: {e}"
        await update.message.reply_text(msg)


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
    try:
        await ctx.bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=msg)
    except Exception:
        log.warning("모니터 메시지 전송 실패")


async def job_dca(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("DCA 잡 시작 — %d종목", len(DCA_MARKETS))
    for symbol, usdc in DCA_MARKETS.items():
        try:
            result = await execute_dca(symbol, usdc)
            msg = format_dca_notification(result)
        except Exception as e:
            log.exception("DCA job failed: %s", symbol)
            msg = f"❌ {symbol} DCA 실패: {e}"
        try:
            await ctx.bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=msg)
        except Exception:
            log.warning("DCA 알림 전송 실패: %s", symbol)


def _aest_to_utc(aest_hour: int, aest_minute: int = 0) -> time:
    utc_hour = (aest_hour - AEST_OFFSET) % 24
    return time(hour=utc_hour, minute=aest_minute, tzinfo=UTC)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "봇 시작 및 도움말"),
        BotCommand("l", "Lighter 포지션 현황 조회"),
        BotCommand("dca", "DCA 수동 즉시 실행"),
        BotCommand("config", "DCA 설정 현황 조회"),
        BotCommand("addcoin", "DCA 종목 추가/수정 <티커> <금액> [레버리지]"),
        BotCommand("removecoin", "DCA 종목 제거 <티커>"),
        BotCommand("leverage", "레버리지 변경 <티커> <배수>"),
    ])


def run_bot() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("l", cmd_lighter, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("dca", cmd_dca, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("config", cmd_config, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("addcoin", cmd_addcoin, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("removecoin", cmd_removecoin, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("leverage", cmd_leverage, filters=OWNER_FILTER))
    app.add_handler(CallbackQueryHandler(cb_close_ask, pattern=r"^close_ask:"))
    app.add_handler(CallbackQueryHandler(cb_close_yes, pattern=r"^close_yes:"))
    app.add_handler(CallbackQueryHandler(cb_close_no, pattern=r"^close_no$"))

    jq = app.job_queue

    dca_h, dca_m = DCA_TIME_AEST
    dca_utc = _aest_to_utc(dca_h, dca_m)
    jq.run_daily(job_dca, time=dca_utc, name="dca_daily")
    log.info("DCA 잡 등록: AEST %02d:%02d (UTC %s)", dca_h, dca_m, dca_utc)

    for aest_h in MONITOR_HOURS_AEST:
        utc_t = _aest_to_utc(aest_h)
        jq.run_daily(job_monitor, time=utc_t, name=f"monitor_{aest_h:02d}aest")
    log.info("모니터 잡 등록: AEST %s", ", ".join(f"{h:02d}:00" for h in MONITOR_HOURS_AEST))

    log.info("Lighter DCA Bot 시작 — polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
