import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]


def _require_chat_id() -> str:
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    try:
        int(raw)
    except ValueError:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID가 비었거나 정수가 아닙니다. 본인 chat id(숫자)를 .env에 설정하세요. "
            "(비어 있으면 누구나 주문 명령을 보낼 수 있어 시작을 거부합니다)"
        ) from None
    return raw


TELEGRAM_CHAT_ID: str = _require_chat_id()

LIGHTER_WALLET: str = os.environ["LIGHTER_WALLET"]
# 4시간 알림 LP 칸에만 쓰는 두 번째 지갑(공개 주소, 읽기 전용 조회)
LIGHTER_WALLET2: str = os.getenv(
    "LIGHTER_WALLET2", "0x43ceaa361bab42FF5a46Cb2cb3E2d90E95ddB6Bb"
).strip()
# 지갑2 LIT Staking 평단(USD/LIT) — 손익 = 평가액 - 개수×평단 (싸장님 11-05: $3.0)
LIGHTER_WALLET2_LIT_ENTRY: float = float(os.getenv("LIGHTER_WALLET2_LIT_ENTRY", "3.0"))
LIGHTER_ACCOUNT_INDEX: int | None = (
    int(os.environ["LIGHTER_ACCOUNT_INDEX"])
    if os.environ.get("LIGHTER_ACCOUNT_INDEX", "").strip()
    else None
)
LIGHTER_API_KEY_INDEX: int = int(os.environ["LIGHTER_API_KEY_INDEX"])
LIGHTER_API_PRIVATE_KEY: str = os.environ["LIGHTER_API_PRIVATE_KEY"]


def _parse_dca_markets() -> dict[str, float]:
    result = {}
    for key, val in os.environ.items():
        if key.startswith("DCA_") and key != "DCA_TIME_AEST":
            symbol = key[4:]  # "DCA_SKHYNIXUSD" → "SKHYNIXUSD"
            try:
                amount = float(val)
                if amount > 0:
                    result[symbol] = amount
            except ValueError:
                pass
    return result


DCA_MARKETS: dict[str, float] = _parse_dca_markets()


def _parse_time(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


DCA_TIME_AEST: tuple[int, int] = _parse_time(os.getenv("DCA_TIME_AEST", "09:00"))
MONITOR_HOURS_AEST: list[int] = [
    int(h.strip()) for h in os.getenv("MONITOR_HOURS_AEST", "8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23").split(",")
]

MIN_LIQ_DISTANCE_PCT: float = float(os.getenv("MIN_LIQ_DISTANCE_PCT", "5"))
MIN_AVAILABLE_BALANCE: float = float(os.getenv("MIN_AVAILABLE_BALANCE", "30"))
ORDER_RETRY_INTERVAL_SEC: int = int(os.getenv("ORDER_RETRY_INTERVAL_SEC", "30"))
ORDER_PRICE_STEP_PCT: float = float(os.getenv("ORDER_PRICE_STEP_PCT", "0.05"))
ORDER_MAX_RETRIES: int = int(os.getenv("ORDER_MAX_RETRIES", "20"))
CLOSE_SLIPPAGE_PCT: float = float(os.getenv("CLOSE_SLIPPAGE_PCT", "1.0"))
# 미체결 주문 취소 후 호가창에서 실제로 사라졌는지 재확인하는 횟수/간격.
# 취소가 확정되지 않으면 다음 주문을 내지 않는다 (중복 매수 방지).
CANCEL_VERIFY_ATTEMPTS: int = int(os.getenv("CANCEL_VERIFY_ATTEMPTS", "5"))
CANCEL_VERIFY_INTERVAL_SEC: float = float(os.getenv("CANCEL_VERIFY_INTERVAL_SEC", "2"))
# 주문 후 체결 확인(잔량 취소·기록 조회)이 네트워크 오류로 실패할 때 다시 묻는 횟수/간격.
SETTLE_ATTEMPTS: int = max(1, int(os.getenv("SETTLE_ATTEMPTS", "3")))
SETTLE_RETRY_SEC: float = float(os.getenv("SETTLE_RETRY_SEC", "5"))

# ── 청산 전 방어 투입(Liq Guard) / 정기 DCA 스위치 ─────────────────────────
# 키 이름에 DCA_ 접두를 쓰면 _parse_dca_markets 가 종목으로 오인하므로 쓰지 않는다.
# 런타임 스위치(정기 DCA 켜짐/꺼짐, guard 모드)는 data/runtime_flags.json 이 이 기본값을 덮어쓴다.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


REGULAR_DCA_ENABLED: bool = _env_bool("REGULAR_DCA_ENABLED", True)
LIQ_GUARD_MODE: str = os.getenv("LIQ_GUARD_MODE", "off").strip().lower()  # off|shadow|live
LIQ_GUARD_INTERVAL_SEC: int = int(os.getenv("LIQ_GUARD_INTERVAL_SEC", "15"))  # runtime_flags guard_interval_sec 가 우선(5~300)
LIQ_GUARD_TRIGGER_PCT: float = float(os.getenv("LIQ_GUARD_TRIGGER_PCT", "2.0"))
LIQ_GUARD_TARGET_PCT: float = float(os.getenv("LIQ_GUARD_TARGET_PCT", "4.0"))
LIQ_GUARD_BUDGET_USD: float = float(os.getenv("LIQ_GUARD_BUDGET_USD", "100"))
LIQ_GUARD_COLLATERAL_MIN_SHARE: float = float(os.getenv("LIQ_GUARD_COLLATERAL_MIN_SHARE", "0.5"))
LIQ_GUARD_REARM_PCT: float = float(os.getenv("LIQ_GUARD_REARM_PCT", "1.0"))
LIQ_GUARD_MAX_RETRIES: int = int(os.getenv("LIQ_GUARD_MAX_RETRIES", "5"))
# 차단선 IMF 출처: conservative = max(min, default) 초기증거금 비율 / min = min 만
# 담보/매수 분할: fixed = 담보 B*share + 매수 B*(1-share) 고정 / target = 목표 차단선 공식으로 매수를 줄임
LIQ_GUARD_SPLIT: str = os.getenv("LIQ_GUARD_SPLIT", "fixed").strip().lower()
LIQ_GUARD_MARKET_CACHE_SEC: int = int(os.getenv("LIQ_GUARD_MARKET_CACHE_SEC", "3600"))
LIQ_GUARD_BLOCK_IMF_SOURCE: str = os.getenv("LIQ_GUARD_BLOCK_IMF_SOURCE", "conservative").strip().lower()
LIQ_GUARD_ALERT_COOLDOWN_SEC: int = int(os.getenv("LIQ_GUARD_ALERT_COOLDOWN_SEC", "600"))
LIQ_GUARD_SHADOW_HOURLY_SEC: int = int(os.getenv("LIQ_GUARD_SHADOW_HOURLY_SEC", "3600"))

BASE_URL: str = "https://mainnet.zklighter.elliot.ai"
API_BASE: str = f"{BASE_URL}/api/v1"
AEST_OFFSET: int = 10  # UTC+10

HEADERS: dict[str, str] = {
    "Origin": "https://app.lighter.xyz",
    "Referer": "https://app.lighter.xyz/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/131.0.0.0",
}


def add_dca_market(symbol: str, amount: float) -> None:
    """DCA 종목 추가/수정 — .env와 메모리 DCA_MARKETS 동시 갱신 (재시작 불필요)."""
    from env_editor import upsert_key
    upsert_key(f"DCA_{symbol}", str(amount))
    DCA_MARKETS[symbol] = amount


def remove_dca_market(symbol: str) -> bool:
    """DCA 종목 제거. .env에 실제로 있었으면 True."""
    from env_editor import remove_key
    removed = remove_key(f"DCA_{symbol}")
    DCA_MARKETS.pop(symbol, None)
    return removed
