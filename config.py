import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID: str = os.environ["TELEGRAM_CHAT_ID"]

LIGHTER_WALLET: str = os.environ["LIGHTER_WALLET"]
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

BASE_URL: str = "https://mainnet.zklighter.elliot.ai"
API_BASE: str = f"{BASE_URL}/api/v1"
AEST_OFFSET: int = 10  # UTC+10

HEADERS: dict[str, str] = {
    "Origin": "https://app.lighter.xyz",
    "Referer": "https://app.lighter.xyz/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/131.0.0.0",
}
