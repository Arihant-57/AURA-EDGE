"""Start a disposable desktop backend with Python socket connections limited to loopback."""
import argparse
import asyncio
import ipaddress
import socket
import sys
from pathlib import Path

from desktop.managed_backend import main


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
    options = parser.parse_args()
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
