"""Tests for centralized offline, LAN, and opt-in online network policy enforcement."""

from __future__ import annotations

import httpx
import pytest

from aura_edge.config import ConnectivityMode
from aura_edge.infrastructure.network_policy import (
    NetworkPolicyEnforcer,
    NetworkPolicyViolationError,
    build_async_http_client,
    build_http_client,
)


def test_strict_offline_allows_only_loopback() -> None:
    enforcer = NetworkPolicyEnforcer(mode=ConnectivityMode.STRICT_OFFLINE)

    for allowed_url in [
        "http://127.0.0.1:11434/api/tags",
        "http://localhost:8000/api/v1/health",
        "http://[::1]:5173/",
        "ws://127.0.0.1:8000/ws",
    ]:
        decision = enforcer.evaluate_url(allowed_url)
        assert decision.allowed is True, f"Expected allowed for {allowed_url}: {decision}"

    for denied_url in [
        "https://example.com",
        "https://api.openai.com/v1/chat/completions",
        "http://8.8.8.8/",
        "http://192.168.1.50:1883",
        "http://10.0.0.5:8080",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "",
        "ftp://127.0.0.1/file",
        "not-a-valid-url",
    ]:
        decision = enforcer.evaluate_url(denied_url)
        assert decision.allowed is False, f"Expected denied for {denied_url}: {decision}"


def test_policy_enforcing_sync_transport_blocks_before_io() -> None:
    enforcer = NetworkPolicyEnforcer(mode=ConnectivityMode.STRICT_OFFLINE)
    inner_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inner_calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    mock_inner = httpx.MockTransport(handler)

    with build_http_client(enforcer, inner_transport=mock_inner) as client:
        ok_resp = client.get("http://127.0.0.1:11434/api/tags")
        assert ok_resp.status_code == 200
        assert len(inner_calls) == 1

        with pytest.raises(NetworkPolicyViolationError):
            client.get("https://example.com/leak-test")

        with pytest.raises(NetworkPolicyViolationError):
            client.post("http://192.168.1.25:8123/api/states")

    # Inner transport must never have received the blocked requests.
    assert inner_calls == ["http://127.0.0.1:11434/api/tags"]


async def test_policy_enforcing_async_transport_blocks_before_io() -> None:
    enforcer = NetworkPolicyEnforcer(mode=ConnectivityMode.STRICT_OFFLINE)
    inner_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inner_calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    mock_inner = httpx.MockTransport(handler)

    async with build_async_http_client(enforcer, inner_transport=mock_inner) as client:
        ok_resp = await client.get("http://localhost:8000/api/v1/health")
        assert ok_resp.status_code == 200

        with pytest.raises(NetworkPolicyViolationError):
            await client.get("https://dns.google/resolve")

    assert inner_calls == ["http://localhost:8000/api/v1/health"]


def test_lan_only_allows_private_lan_and_blocks_internet_and_metadata() -> None:
    enforcer = NetworkPolicyEnforcer(
        mode=ConnectivityMode.LAN_ONLY,
        lan_allowed_cidrs=["192.168.0.0/16", "10.0.0.0/8"],
    )

    assert enforcer.evaluate_url("http://127.0.0.1:8000").allowed is True
    assert enforcer.evaluate_url("http://192.168.1.42:1883").allowed is True
    assert enforcer.evaluate_url("http://10.0.0.8:80").allowed is True

    assert enforcer.evaluate_url("http://172.16.0.5:80").allowed is False
    assert enforcer.evaluate_url("http://169.254.169.254/latest/meta-data/").allowed is False
    assert enforcer.evaluate_url("https://api.github.com/").allowed is False
    assert enforcer.evaluate_url("http://1.1.1.1/").allowed is False


def test_online_opt_in_requires_explicit_host_allowlist() -> None:
    enforcer = NetworkPolicyEnforcer(
        mode=ConnectivityMode.ONLINE_OPT_IN,
        online_allowed_hosts=["gmail.googleapis.com"],
    )

    assert enforcer.evaluate_url("http://127.0.0.1:11434").allowed is True
    assert enforcer.evaluate_url("http://192.168.1.10:80").allowed is True
    assert enforcer.evaluate_url("https://gmail.googleapis.com/gmail/v1/users/me/messages").allowed is True
    assert enforcer.evaluate_url("https://example.org/other").allowed is False
