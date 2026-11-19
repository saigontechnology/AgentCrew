"""Loopback HTTP regressions for OAuth callback filtering and result ownership."""

import asyncio
import importlib.util
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

AUTH_PATH = Path(__file__).resolve().parents[2] / "AgentCrew/modules/mcpclient/auth.py"
SPEC = importlib.util.spec_from_file_location("oauth_callback_under_test", AUTH_PATH)
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)

CALLBACK = "/callback?" + urlencode(
    {
        "code": "synthetic-code",
        "state": "synthetic-state",
        "iss": "https://issuer.invalid",
    }
)
DENIED = "/callback?error=access_denied&state=synthetic-state"


@pytest.fixture
def callback_server():
    server = AUTH.OAuthCallbackServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def request(server, path):
    connection = HTTPConnection("127.0.0.1", server.server.server_port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize(
    "path,status",
    [
        ("/favicon.ico", 404),
        ("/unrelated?code=synthetic-code", 404),
        ("/callback/?code=synthetic-code", 404),
        ("/callback;extra?code=synthetic-code", 404),
        ("/callback;?code=synthetic-code", 404),
        ("/callback", 400),
        ("/callback?code=", 400),
        ("/callback?error=", 400),
        ("/callback?state=synthetic-state", 400),
    ],
)
def test_ignored_request_keeps_waiter_pending_until_valid_callback(
    callback_server, path, status
):
    async def scenario():
        waiter = asyncio.create_task(callback_server.wait_for_callback(timeout=3))
        try:
            await asyncio.sleep(0)
            response_status, _ = await asyncio.to_thread(request, callback_server, path)
            await asyncio.sleep(0.35)
            assert not waiter.done()
            assert not callback_server.has_result()
            assert callback_server.get_result() == {}
            assert response_status == status

            response_status, body = await asyncio.to_thread(
                request, callback_server, CALLBACK
            )
            assert response_status == 200
            assert b"Authorization Successful" in body
            result = await waiter
            assert result.code == "synthetic-code"
            assert result.state == "synthetic-state"
            assert result.iss == "https://issuer.invalid"
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/favicon.ico", "/callback", "/callback?code="])
def test_secondary_request_cannot_erase_successful_callback(callback_server, path):
    status, body = request(callback_server, CALLBACK)
    assert status == 200
    assert b"Authorization Successful" in body

    request(callback_server, path)
    result = asyncio.run(callback_server.wait_for_callback(timeout=1))
    assert result.code == "synthetic-code"
    assert result.state == "synthetic-state"
    assert result.iss == "https://issuer.invalid"


def test_active_waiter_survives_success_then_favicon_before_next_poll(callback_server):
    async def scenario():
        waiter = asyncio.create_task(callback_server.wait_for_callback(timeout=3))
        try:
            await asyncio.sleep(0)
            status, body = request(callback_server, CALLBACK)
            assert status == 200
            assert b"Authorization Successful" in body
            assert request(callback_server, "/favicon.ico")[0] == 404
            result = await waiter
            assert result.code == "synthetic-code"
            assert result.state == "synthetic-state"
            assert result.iss == "https://issuer.invalid"
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "first,second",
    [
        (CALLBACK, "/callback?code=another-code&state=another-state"),
        (CALLBACK, DENIED),
        (DENIED, CALLBACK),
        (DENIED, "/callback?error=server_error"),
    ],
)
def test_first_terminal_result_is_immutable(callback_server, first, second):
    request(callback_server, first)
    accepted = callback_server.get_result()
    request(callback_server, second)
    assert callback_server.get_result() == accepted

    if accepted["error"]:
        with pytest.raises(
            RuntimeError, match="^OAuth authorization failed: access_denied$"
        ):
            asyncio.run(callback_server.wait_for_callback(timeout=1))
    else:
        result = asyncio.run(callback_server.wait_for_callback(timeout=1))
        assert result.code == accepted["code"]
        assert result.state == accepted["state"]
        assert result.iss == accepted["iss"]


def test_explicit_error_retains_distinct_failure(callback_server):
    status, body = request(callback_server, DENIED)
    assert status == 400
    assert b"access_denied" in body
    with pytest.raises(
        RuntimeError, match="^OAuth authorization failed: access_denied$"
    ):
        asyncio.run(callback_server.wait_for_callback(timeout=1))


def test_missing_state_is_left_for_sdk_validation(callback_server):
    request(callback_server, "/callback?code=synthetic-code")
    result = asyncio.run(callback_server.wait_for_callback(timeout=1))
    assert result.code == "synthetic-code"
    assert result.state is None
    assert result.iss is None


def test_empty_result_cannot_be_latched_through_setter():
    server = AUTH.OAuthCallbackServer()
    server.set_result(None, "synthetic-state", None)
    assert not server.has_result()
    assert server.get_result() == {}
