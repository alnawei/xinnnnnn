import asyncio
import json

import pytest

import netts_api


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        if self.exc:
            raise self.exc
        return self.response


@pytest.mark.asyncio
async def test_delegate_energy_success(monkeypatch):
    monkeypatch.setattr(
        netts_api.aiohttp,
        "ClientSession",
        lambda: FakeSession(FakeResponse(200, {"code": 10000}))
    )

    result = await netts_api.delegate_energy("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", 65000)

    assert result["success"] is True


@pytest.mark.asyncio
async def test_delegate_energy_business_reject_is_certain_failure(monkeypatch):
    monkeypatch.setattr(
        netts_api.aiohttp,
        "ClientSession",
        lambda: FakeSession(FakeResponse(200, {"code": 400, "msg": "库存不足"}))
    )

    result = await netts_api.delegate_energy("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", 65000)

    assert result["success"] is False
    assert result["uncertain"] is False


@pytest.mark.asyncio
async def test_delegate_energy_http_503_is_uncertain(monkeypatch):
    monkeypatch.setattr(
        netts_api.aiohttp,
        "ClientSession",
        lambda: FakeSession(FakeResponse(503, {"error": "busy"}))
    )

    result = await netts_api.delegate_energy("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", 65000)

    assert result["success"] is False
    assert result["uncertain"] is True


@pytest.mark.asyncio
async def test_delegate_energy_timeout_is_uncertain(monkeypatch):
    monkeypatch.setattr(
        netts_api.aiohttp,
        "ClientSession",
        lambda: FakeSession(exc=asyncio.TimeoutError())
    )

    result = await netts_api.delegate_energy("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", 65000)

    assert result["success"] is False
    assert result["uncertain"] is True
