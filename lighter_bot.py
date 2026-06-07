"""Lighter DCA Bot — Telegram 봇 진입점"""

import logging
from datetime import time, timezone, timedelta

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    DCA_MARKETS, DCA_TIME_AEST, MONITOR_HOURS_AEST, AEST_OFFSET,
)
from monitor import get_full_status
from dca_engine import execute_dca, format_dca_notification

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
        f"  /l      — 포지션 현황\n"
        f"  /dca    — DCA 즉시 실행\n"
        f"  /config — 현재 DCA 설정"
    )


async def cmd_lighter(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Lighter 포지션 조회 중...")
    try:
        msg = await get_full_status()
        await update.message.reply_text(msg)
    except Exception as e:
        log.exception("lighter status failed")
        await update.message.reply_text(f"❌ 조회 실패: {e}")


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
        BotCommand("config", "DCA 설정 현황 조회")
    ])


def run_bot() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("l", cmd_lighter, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("dca", cmd_dca, filters=OWNER_FILTER))
    app.add_handler(CommandHandler("config", cmd_config, filters=OWNER_FILTER))

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
