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


def test_add_remove_dca_market(tmp_path, monkeypatch):
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

    import env_editor
    fake_env = tmp_path / ".env"
    fake_env.write_text("TELEGRAM_BOT_TOKEN=tok\n", encoding="utf-8")
    monkeypatch.setattr(env_editor, "ENV_PATH", fake_env)

    cfg.add_dca_market("NVDAUSD", 20.0)
    assert cfg.DCA_MARKETS["NVDAUSD"] == 20.0
    assert "DCA_NVDAUSD=20.0" in fake_env.read_text(encoding="utf-8")

    removed = cfg.remove_dca_market("NVDAUSD")
    assert removed is True
    assert "NVDAUSD" not in cfg.DCA_MARKETS
    assert "DCA_NVDAUSD" not in fake_env.read_text(encoding="utf-8")


def _base_env(chat_id):
    return {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": chat_id,
        "LIGHTER_WALLET": "0xABC",
        "LIGHTER_API_KEY_INDEX": "2",
        "LIGHTER_API_PRIVATE_KEY": "privkey",
    }


import pytest


@pytest.mark.parametrize("bad", ["", "   ", "abc", "12a", "1.5"])
def test_chat_id_invalid_refuses_start(bad):
    with pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID"):
        _load_config(_base_env(bad))


def test_chat_id_missing_refuses_start():
    env = _base_env("1")
    del env["TELEGRAM_CHAT_ID"]
    with pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID"):
        _load_config(env)


def test_chat_id_valid_accepts_negative_group_id():
    assert _load_config(_base_env(" -1001234 ")).TELEGRAM_CHAT_ID == "-1001234"
    assert _load_config(_base_env("123")).TELEGRAM_CHAT_ID == "123"
