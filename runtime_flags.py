"""재시작 없이 바꾸는 런타임 스위치 (data/runtime_flags.json).

파일이 없거나 깨졌거나 값이 이상하면 .env 기본값(config)을 쓴다.
정기 DCA 는 기본 켜짐, guard 는 기본 off — 파일이 없으면 기존 동작 그대로.
"""

import json
import logging
import os

import config

log = logging.getLogger(__name__)

FLAGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runtime_flags.json")
GUARD_MODES = ("off", "shadow", "live")


def _load() -> dict:
    try:
        with open(FLAGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("runtime_flags.json 읽기 실패 — 기본값 사용: %r", e)
        return {}
    if not isinstance(data, dict):
        log.warning("runtime_flags.json 형식 오류 — 기본값 사용")
        return {}
    return data


def regular_dca_enabled() -> bool:
    val = _load().get("regular_dca_enabled")
    if isinstance(val, bool):
        return val
    return bool(config.REGULAR_DCA_ENABLED)


def guard_mode() -> str:
    """off|shadow|live. 알 수 없는 값은 안전하게 off (돈이 나가는 쪽으로 열리지 않게)."""
    val = _load().get("guard_mode")
    if isinstance(val, str) and val.strip().lower() in GUARD_MODES:
        return val.strip().lower()
    default = str(config.LIQ_GUARD_MODE).strip().lower()
    return default if default in GUARD_MODES else "off"


def _save(updates: dict) -> None:
    data = _load()
    data.update(updates)
    os.makedirs(os.path.dirname(FLAGS_PATH), exist_ok=True)
    tmp = FLAGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FLAGS_PATH)


def set_regular_dca(enabled: bool) -> None:
    _save({"regular_dca_enabled": bool(enabled)})


def set_guard_mode(mode: str) -> None:
    mode = mode.strip().lower()
    if mode not in GUARD_MODES:
        raise ValueError(f"guard_mode 는 {'|'.join(GUARD_MODES)} 중 하나여야 합니다")
    _save({"guard_mode": mode})


def collateral_min_share() -> float:
    """담보 최소 비중(0~1). 파일 값이 있고 범위 안이면 .env 기본값을 덮어쓴다."""
    val = _load().get("collateral_min_share")
    if isinstance(val, (int, float)) and not isinstance(val, bool) and 0 <= val <= 1:
        return float(val)
    default = config.LIQ_GUARD_COLLATERAL_MIN_SHARE
    return float(default) if 0 <= default <= 1 else 0.5


def set_collateral_min_share(share: float) -> None:
    if isinstance(share, bool) or not (0 <= share <= 1):  # NaN 도 여기서 거부됨
        raise ValueError("share 는 0~1 사이여야 합니다")
    _save({"collateral_min_share": float(share)})
