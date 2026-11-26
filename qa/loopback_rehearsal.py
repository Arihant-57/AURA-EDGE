"""Start a disposable desktop backend with Python socket connections limited to loopback."""
import argparse
import asyncio
import ipaddress
import os
import socket
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from desktop.managed_backend import main


def require_disposable_database(value: str) -> None:
    url = make_url(value)
    if (url.get_backend_name() != 'postgresql' or url.host != '127.0.0.1'
            or not url.port or url.port == 5432 or not url.database or url.database == 'aura_edge'
            or url.query):
        raise ValueError('Specify a disposable PostgreSQL database on an alternate loopback port.')


def restrict_connections(event, args):
    if event in {"socket.connect", "socket.sendto"}:
        address = args[1]
        if not isinstance(address, tuple):
            return
        host = address[0]
    elif event == "socket.getaddrinfo":
        host = args[0]
        if host is None:
            return
    else:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    print(f"Denied {event} outside loopback", file=sys.stderr, flush=True)
    raise PermissionError("Audit backend permits loopback only")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("marker", type=Path)
    parser.add_argument("port", type=int, choices=range(8010, 8100))
    parser.add_argument("--interactive-audio", action="store_true", help="Enable direct replies for an operator microphone session.")
    options = parser.parse_args()
    try:
        require_disposable_database(os.environ.get("AURA_EDGE_DATABASE_URL", ""))
    except (ValueError, ArgumentError):
        parser.error("An explicit disposable loopback AURA_EDGE_DATABASE_URL is required.")
    os.environ["AURA_EDGE_AUDIO_OUTPUT_ENABLED"] = str(options.interactive_audio)
    os.environ["AURA_EDGE_PROACTIVE_AUDIO_ENABLED"] = "false"
    sys.addaudithook(restrict_connections)
    # The hook rejects this reserved address before a connection is attempted.
    with socket.socket() as probe:
        try:
            probe.connect(("192.0.2.1", 9))
        except PermissionError:
            print("Outbound self-check blocked", flush=True)
        else:
            raise RuntimeError("Outbound audit self-check failed")
    asyncio.run(main(options.marker, options.port))
