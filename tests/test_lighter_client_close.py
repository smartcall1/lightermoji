"""signer/tx_api 가 만든 aiohttp 세션이 성공·실패 경로 모두에서 닫히는지 검증 (실거래 없음)."""
import asyncio

import lighter_client as lc


class FakeApiClient:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeSigner:
    ISOLATED_MARGIN_MODE = 1
    ORDER_TYPE_LIMIT = 0
    ORDER_TIME_IN_FORCE_GOOD_TILL_TIME = 1

    def __init__(self, fail=False, sign_err=None):
        self.api_client = FakeApiClient()
        self.fail = fail
        self.sign_err = sign_err

    async def close(self):
        await self.api_client.close()

    def sign_create_order(self, **kw):
        return 1, "info", "hash", self.sign_err

    def sign_cancel_order(self, **kw):
        return 1, "info", "hash", self.sign_err

    async def update_leverage(self, **kw):
        if self.fail:
            raise RuntimeError("boom")
        return None, None, None

    async def create_market_order(self, **kw):
        if self.fail:
            raise RuntimeError("boom")
        return None, object(), None


class FakeTxApi:
    def __init__(self, fail=False):
        self.api_client = FakeApiClient()
        self.fail = fail

    async def send_tx(self, **kw):
        if self.fail:
            raise RuntimeError("boom")


def _patch(monkeypatch, signer, tx_api):
    monkeypatch.setattr(lc, "_make_signer", lambda idx: signer)
    monkeypatch.setattr(lc, "_make_tx_api", lambda: tx_api)


def _buy():
    return lc.place_limit_buy(1, 1.0, 10.0, 2, 2, 1, 1)


def test_limit_buy_closes_on_success_and_failure(monkeypatch):
    for fail in (False, True):
        s, t = FakeSigner(), FakeTxApi(fail=fail)
        _patch(monkeypatch, s, t)
        asyncio.run(_buy())
        assert s.api_client.closed and t.api_client.closed


def test_limit_buy_closes_on_sign_error(monkeypatch):
    s, t = FakeSigner(sign_err="bad"), FakeTxApi()
    _patch(monkeypatch, s, t)
    _, err = asyncio.run(_buy())
    assert err and s.api_client.closed and t.api_client.closed


def test_market_close_closes(monkeypatch):
    for fail in (False, True):
        s, t = FakeSigner(fail=fail), FakeTxApi()
        _patch(monkeypatch, s, t)
        asyncio.run(lc.place_market_close(1, 1.0, True, 10.0, 2, 2, 1, 1))
        assert s.api_client.closed


def test_set_leverage_closes(monkeypatch):
    for fail in (False, True):
        s, t = FakeSigner(fail=fail), FakeTxApi()
        _patch(monkeypatch, s, t)
        asyncio.run(lc.set_leverage(1, 3, 1))
        assert s.api_client.closed


def test_cancel_closes(monkeypatch):
    for fail, sign_err in ((False, None), (True, None), (False, "bad")):
        s, t = FakeSigner(sign_err=sign_err), FakeTxApi(fail=fail)
        _patch(monkeypatch, s, t)
        asyncio.run(lc.cancel_order(1, 5, 1))
        assert s.api_client.closed and t.api_client.closed


def test_close_failure_keeps_result_and_closes_other(monkeypatch):
    s, t = FakeSigner(), FakeTxApi()

    async def bad_close():
        raise RuntimeError("close boom")

    s.close = bad_close
    _patch(monkeypatch, s, t)
    tx_hash, err = asyncio.run(_buy())
    assert tx_hash == "hash" and err is None
    assert t.api_client.closed
