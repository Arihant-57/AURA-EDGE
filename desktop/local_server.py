"""Serve the compiled dashboard and its JSON API on a private loopback origin."""

from functools import partial
from http.client import HTTPConnection, HTTPException
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
from threading import Thread
from urllib.parse import urlsplit

API_SECTIONS = {
    "readiness", "health", "voice", "intent", "conversation", "smart-home",
    "timers", "tasks", "calendar", "briefing", "reminders", "schedules",
    "notifications", "memory", "groceries", "recipes", "expenses", "parcels",
}


class DashboardHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def handle(self):
        try:
            super().handle()
        except ConnectionError:
            # WebEngine can cancel an in-flight read on navigation or refresh.
            pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                         "frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
        super().end_headers()

    def handle_dashboard(self):
        server = self.server
        if self.headers.get("Host") != server.authority or not self.path.startswith(server.prefix):
            self.send_error(404)
            return
        self.path = "/" + self.path[len(server.prefix):]
        if self.path.startswith("/api/v1/"):
            if self.command != "GET" and self.headers.get("Origin") != server.origin:
                self.send_error(403)
                return
            self.proxy()
        elif self.command == "GET" and not urlsplit(self.path).path.endswith("/"):
            super().do_GET()
        elif self.command == "GET" and self.path == "/":
            self.path = "/index.html"
            super().do_GET()
        else:
            self.send_error(404)

    def list_directory(self, _path):
        self.send_error(404)

    def proxy(self):
        section = urlsplit(self.path).path.removeprefix("/api/v1/").split("/")[0]
        if section not in API_SECTIONS:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if not 0 <= length <= 2_000_000:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        headers = {"Content-Type": "application/json"} if body else {}
        connection = HTTPConnection("127.0.0.1", self.server.backend_port, timeout=300)
        try:
            connection.request(self.command, self.path, body, headers)
            response = connection.getresponse()
            content = response.read()
        except (OSError, HTTPException):
            self.send_error(502, "Local AURA backend is unavailable")
            return
        finally:
            connection.close()
        self.send_response(response.status)
        self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_dashboard

    def do_HEAD(self):
        self.send_error(405)


def start_server(directory, backend_port):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(DashboardHandler, directory=directory))
    server.authority = f"127.0.0.1:{server.server_port}"
    server.origin = f"http://{server.authority}"
    server.prefix = f"/{token_urlsafe(24)}/"
    server.url = server.origin + server.prefix
    server.backend_port = backend_port
    Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    return server
