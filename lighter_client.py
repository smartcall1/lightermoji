"""Lighter API 클라이언트 — 읽기(REST) + 쓰기(SDK 서명)"""

import logging
import httpx
import lighter

from config import (
    BASE_URL, API_BASE, HEADERS,
    LIGHTER_WALLET, LIGHTER_ACCOUNT_INDEX,
    LIGHTER_API_KEY_INDEX, LIGHTER_API_PRIVATE_KEY,
)

log = logging.getLogger(__name__)

# ── 마켓 정보 캐시 (symbol → dict) ────────────────────────────
_market_cache: dict[str, dict] = {}


async def fetch_account_index() -> int:
    """지갑 주소로 Lighter account_index 자동 조회."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{API_BASE}/account",
            params={"by": "l1_address", "value": LIGHTER_WALLET},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        accounts = r.json().get("accounts", [])
        if not accounts:
            raise RuntimeError(f"지갑 {LIGHTER_WALLET}에 Lighter 계정 없음")
        return int(accounts[0]["account_index"])


async def get_account_index() -> int:
    if LIGHTER_ACCOUNT_INDEX is not None:
        return LIGHTER_ACCOUNT_INDEX
    return await fetch_account_index()


async def fetch_market_info(symbol: str) -> dict:
    """심볼 → {market_id, price_decimals, size_decimals, min_base_amount} 반환. 캐시됨."""
    if symbol in _market_cache:
        return _market_cache[symbol]

    base_symbol = symbol.replace("USD", "").replace("USDC", "")
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API_BASE}/orderBooks", headers=HEADERS, timeout=15)
        r.raise_for_status()
        books = r.json().get("order_books", [])

    for book in books:
        if book.get("symbol", "").upper() == base_symbol.upper():
            info = {
                "market_id": int(book["market_id"]),
                "price_decimals": int(book.get("supported_price_decimals", 2)),
                "size_decimals": int(book.get("supported_size_decimals", 4)),
                "min_base_amount": float(book.get("min_base_amount", "0.001")),
            }
            _market_cache[symbol] = info
            return info

    raise ValueError(
        f"마켓 '{symbol}' (base: '{base_symbol}') 를 찾을 수 없음. "
        f"사용 가능: {[b.get('symbol') for b in books]}"
    )


def encode_price(price_float: float, price_decimals: int) -> int:
    return int(round(price_float * (10 ** price_decimals)))


def encode_amount(amount_float: float, size_decimals: int) -> int:
    return int(round(amount_float * (10 ** size_decimals)))


def decode_amount(amount_int: int, size_decimals: int) -> float:
    return amount_int / (10 ** size_decimals)


def _make_signer(account_index: int) -> lighter.SignerClient:
    return lighter.SignerClient(
        url=BASE_URL,
        api_private_keys={LIGHTER_API_KEY_INDEX: LIGHTER_API_PRIVATE_KEY},
        account_index=account_index,
    )


def _make_tx_api() -> lighter.TransactionApi:
    config = lighter.Configuration(host=BASE_URL)
    api_client = lighter.ApiClient(configuration=config)
    return lighter.TransactionApi(api_client)


async def place_limit_buy(
    market_id: int,
    base_amount_float: float,
    price_float: float,
    price_decimals: int,
    size_decimals: int,
    client_order_index: int,
    account_index: int,
) -> tuple[str | None, str | None]:
    """지정가 매수 주문 전송. (tx_hash, error_msg) 반환."""
    signer = _make_signer(account_index)
    tx_api = _make_tx_api()

    price_int = encode_price(price_float, price_decimals)
    amount_int = encode_amount(base_amount_float, size_decimals)

    tx_type, tx_info, tx_hash, err = signer.sign_create_order(
        market_index=market_id,
        client_order_index=client_order_index,
        base_amount=amount_int,
        price=price_int,
        is_ask=False,  # False = 매수(bid)
        order_type=signer.ORDER_TYPE_LIMIT,
        time_in_force=signer.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
        reduce_only=False,
        trigger_price=0,
        api_key_index=LIGHTER_API_KEY_INDEX,
    )

    if err:
        return None, f"서명 실패: {err}"

    try:
        tx_api.send_tx(body={"tx_type": tx_type, "tx_info": tx_info})
        log.info("주문 전송 완료: tx_hash=%s", tx_hash)
        return tx_hash, None
    except Exception as e:
        return None, f"전송 실패: {e}"


async def fetch_tx_order_index(tx_hash: str) -> int | None:
    """tx_hash → Lighter order_index 조회 (취소 시 필요)."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{API_BASE}/tx",
                params={"tx_hash": tx_hash},
                headers=HEADERS,
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                tx_data = data.get("tx") or data.get("transaction") or {}
                order_index = tx_data.get("order_index") or tx_data.get("index")
                return int(order_index) if order_index is not None else None
        except Exception as e:
            log.warning("tx 조회 실패: %s", e)
    return None


async def cancel_order(
    market_id: int,
    order_index: int,
    account_index: int,
) -> bool:
    """지정 order_index 취소. 성공 여부 반환."""
    signer = _make_signer(account_index)
    tx_api = _make_tx_api()

    tx_type, tx_info, tx_hash, err = signer.sign_cancel_order(
        market_index=market_id,
        order_index=order_index,
        api_key_index=LIGHTER_API_KEY_INDEX,
    )
    if err:
        log.error("취소 서명 실패: %s", err)
        return False

    try:
        tx_api.send_tx(body={"tx_type": tx_type, "tx_info": tx_info})
        log.info("주문 취소 완료: order_index=%d", order_index)
        return True
    except Exception as e:
        log.error("취소 전송 실패: %s", e)
        return False
