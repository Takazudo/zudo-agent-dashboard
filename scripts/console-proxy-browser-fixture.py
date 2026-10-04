"""Disposable HTTPS Serve simulator, never a deployable proxy or real identity."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import subprocess
import threading

from zudo_agent.console_proxy import make_proxy


def start(backend, directory):
    class SimulatedServe(BaseHTTPRequestHandler):
        def proxy(self):
            headers = {k: self.headers[k] for k in ("Authorization", "Origin", "Content-Type", "X-Console-CSRF", "X-Workflow-CSRF") if k in self.headers}
            headers.update({"Host": authority, "X-Forwarded-Host": authority,
                            "X-Forwarded-Proto": "https", "Tailscale-User-Login": "fixture@example.test"})
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            conn = http.client.HTTPConnection("127.0.0.1", adapter.server_port, timeout=5)
            try:
                conn.request(self.command, self.path, body=body or None, headers=headers)
                response = conn.getresponse(); data = response.read()
                self.send_response(response.status)
                for k, v in response.getheaders():
                    if k.lower() not in {"server", "date", "connection"}: self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)
            finally: conn.close()
        do_GET = do_POST = proxy
        def log_message(self, *_args): pass
    class FixtureServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # Browser intentionally aborts replies to test uncertain delivery.
            pass
    front = FixtureServer(("127.0.0.1", 0), SimulatedServe)
    authority = f"127.0.0.1:{front.server_port}"
    cert, key = Path(directory) / "fixture-cert.pem", Path(directory) / "fixture-key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
    front.socket = context.wrap_socket(front.socket, server_side=True)
    origin = "https://" + authority
    adapter = make_proxy(origin, "fixture@example.test", backend.server_port, 0, trust_local_serve=True)
    for server in (backend, adapter):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return front, adapter, origin
