"""Centralized, fail-closed outbound network policy enforcement."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from aura_edge.config import ConnectivityMode, Settings, is_loopback_host

_ALLOWED_URL_SCHEMES = {"http", "https", "ws", "wss"}


class NetworkPolicyViolationError(PermissionError):
    """Raised when an outbound target violates the active connectivity policy."""

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        super().__init__(
            f"Outbound request blocked by '{decision.mode.value}' policy "
            f"for target '{decision.target}' ({decision.reason})."
        )


class PolicyDecision(BaseModel):
    """Structured result of evaluating a target URL or host against connectivity policy."""

    allowed: bool
    mode: ConnectivityMode
    target: str
    host: str
    reason: str


class NetworkPolicyEnforcer:
    """Evaluates and enforces strict_offline, lan_only, and online_opt_in policies.

    Evaluation uses pure syntactic URL/IP inspection and never triggers external DNS lookups.
    """

    def __init__(
        self,
        mode: ConnectivityMode = ConnectivityMode.STRICT_OFFLINE,
        lan_allowed_cidrs: list[str] | None = None,
        online_allowed_hosts: list[str] | None = None,
    ) -> None:
        self.mode = mode
        self.lan_networks = [
            ipaddress.ip_network(cidr, strict=False)
            for cidr in (
                lan_allowed_cidrs
                if lan_allowed_cidrs is not None
                else ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]
            )
        ]
        self.online_allowed_hosts = {
            h.strip().lower().strip("[]")
            for h in (online_allowed_hosts or [])
            if h.strip()
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> NetworkPolicyEnforcer:
        return cls(
            mode=settings.connectivity_mode,
            lan_allowed_cidrs=settings.lan_allowed_cidrs,
            online_allowed_hosts=settings.online_allowed_hosts,
        )

    def evaluate_url(self, target: str) -> PolicyDecision:
        """Evaluate whether *target* URL is permitted under the active policy."""
        cleaned = target.strip()
        if not cleaned:
            return PolicyDecision(
                allowed=False,
                mode=self.mode,
                target=target,
                host="",
                reason="Empty target URL is denied (fail-closed).",
            )

        parsed = urlsplit(cleaned)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_URL_SCHEMES:
            return PolicyDecision(
                allowed=False,
                mode=self.mode,
                target=cleaned,
                host="",
                reason=f"Unsupported or missing URL scheme '{scheme or '<none>'}'.",
            )

        host = (parsed.hostname or "").strip().lower()
        if not host:
            return PolicyDecision(
                allowed=False,
                mode=self.mode,
                target=cleaned,
                host="",
                reason="Target URL has no valid hostname or IP address.",
            )

        return self._evaluate_host(target=cleaned, host=host)

    def _evaluate_host(self, *, target: str, host: str) -> PolicyDecision:
        if is_loopback_host(host):
            return PolicyDecision(
                allowed=True,
                mode=self.mode,
                target=target,
                host=host,
                reason="Loopback target is permitted in all connectivity modes.",
            )

        if self.mode == ConnectivityMode.STRICT_OFFLINE:
            return PolicyDecision(
                allowed=False,
                mode=self.mode,
                target=target,
                host=host,
                reason="strict_offline permits loopback targets only; non-loopback host denied.",
            )

        ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        try:
            ip_obj = ipaddress.ip_address(host)
            if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
                ip_obj = ip_obj.ipv4_mapped
        except ValueError:
            ip_obj = None

        if ip_obj is not None and (
            ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast
        ):
            return PolicyDecision(
                allowed=False,
                mode=self.mode,
                target=target,
                host=host,
                reason="Link-local, unspecified, or multicast IP targets are denied.",
            )

        in_lan = ip_obj is not None and ip_obj.is_private and any(
            ip_obj in net for net in self.lan_networks
        )

        if self.mode == ConnectivityMode.LAN_ONLY:
            if in_lan:
                return PolicyDecision(
                    allowed=True,
                    mode=self.mode,
                    target=target,
                    host=host,
                    reason="Target IP belongs to configured private LAN range.",
                )
            return PolicyDecision(
                allowed=False,
                mode=self.mode,
                target=target,
                host=host,
                reason="lan_only permits loopback and configured private LAN IPs only.",
            )

        # ConnectivityMode.ONLINE_OPT_IN
        if in_lan:
            return PolicyDecision(
                allowed=True,
                mode=self.mode,
                target=target,
                host=host,
                reason="Target IP belongs to configured private LAN range.",
            )
        if host in self.online_allowed_hosts:
            return PolicyDecision(
                allowed=True,
                mode=self.mode,
                target=target,
                host=host,
                reason="Target host is explicitly listed in online_allowed_hosts.",
            )
        return PolicyDecision(
            allowed=False,
            mode=self.mode,
            target=target,
            host=host,
            reason="online_opt_in blocks external hosts not listed in online_allowed_hosts.",
        )

    def assert_url_allowed(self, target: str) -> PolicyDecision:
        """Raise NetworkPolicyViolationError if *target* is denied."""
        decision = self.evaluate_url(target)
        if not decision.allowed:
            raise NetworkPolicyViolationError(decision)
        return decision


class PolicyEnforcingTransport(httpx.BaseTransport):
    """Synchronous HTTPX transport that enforces NetworkPolicyEnforcer before every request."""

    def __init__(
        self,
        enforcer: NetworkPolicyEnforcer,
        inner_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.enforcer = enforcer
        self._inner = inner_transport or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.enforcer.assert_url_allowed(str(request.url))
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class AsyncPolicyEnforcingTransport(httpx.AsyncBaseTransport):
    """Asynchronous HTTPX transport that enforces NetworkPolicyEnforcer before every request."""

    def __init__(
        self,
        enforcer: NetworkPolicyEnforcer,
        inner_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enforcer = enforcer
        self._inner = inner_transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.enforcer.assert_url_allowed(str(request.url))
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_http_client(
    enforcer: NetworkPolicyEnforcer,
    *,
    timeout: float = 5.0,
    inner_transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Create a synchronous HTTPX client bound to the centralized network policy."""
    return httpx.Client(
        transport=PolicyEnforcingTransport(enforcer, inner_transport=inner_transport),
        timeout=timeout,
    )


def build_async_http_client(
    enforcer: NetworkPolicyEnforcer,
    *,
    timeout: float = 5.0,
    inner_transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Create an asynchronous HTTPX client bound to the centralized network policy."""
    return httpx.AsyncClient(
        transport=AsyncPolicyEnforcingTransport(enforcer, inner_transport=inner_transport),
        timeout=timeout,
    )
