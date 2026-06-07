import os
import importlib
from unittest.mock import patch


def _load_config(env: dict):
    with patch.dict(os.environ, env, clear=True):
        with patch('dotenv.load_dotenv'):
            import config
            importlib.reload(config)
            return config


def test_dca_config_parsing():
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "123",
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "2",
        "LIGHTER_API_PRIVATE_KEY": "privkey",
        "DCA_SKHYNIXUSD": "50",
        "DCA_NVDAUSD": "30",
        "DCA_TIME_AEST": "09:00",
        "MONITOR_HOURS_AEST": "8,12,16",
        "MIN_LIQ_DISTANCE_PCT": "5",
        "MIN_AVAILABLE_BALANCE": "30",
        "ORDER_RETRY_INTERVAL_SEC": "30",
        "ORDER_PRICE_STEP_PCT": "0.05",
        "ORDER_MAX_RETRIES": "20",
    }
    cfg = _load_config(env)
    assert cfg.DCA_MARKETS == {"SKHYNIXUSD": 50.0, "NVDAUSD": 30.0}
    assert cfg.DCA_TIME_AEST == (9, 0)
    assert cfg.MONITOR_HOURS_AEST == [8, 12, 16]
    assert cfg.MIN_LIQ_DISTANCE_PCT == 5.0
    assert cfg.MIN_AVAILABLE_BALANCE == 30.0
    assert cfg.ORDER_RETRY_INTERVAL_SEC == 30
    assert cfg.ORDER_PRICE_STEP_PCT == 0.05
    assert cfg.ORDER_MAX_RETRIES == 20


def test_dca_config_no_markets():
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "123",
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "0",
        "LIGHTER_API_PRIVATE_KEY": "pk",
        "DCA_TIME_AEST": "09:00",
        "MONITOR_HOURS_AEST": "8,12",
    }
    cfg = _load_config(env)
    assert cfg.DCA_MARKETS == {}


def test_account_index_auto():
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "123",
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "0",
        "LIGHTER_API_PRIVATE_KEY": "pk",
        "LIGHTER_ACCOUNT_INDEX": "",
        "DCA_TIME_AEST": "09:00",
        "MONITOR_HOURS_AEST": "8",
    }
    cfg = _load_config(env)
    assert cfg.LIGHTER_ACCOUNT_INDEX is None


def test_headers_present():
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "123",
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "0",
        "LIGHTER_API_PRIVATE_KEY": "pk",
        "DCA_TIME_AEST": "09:00",
        "MONITOR_HOURS_AEST": "8",
    }
    cfg = _load_config(env)
    assert "Origin" in cfg.HEADERS
    assert cfg.API_BASE.startswith("https://")
