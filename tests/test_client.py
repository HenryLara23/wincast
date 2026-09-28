"""LiveClient against a fake Live Client API on localhost (plain http)."""

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests import ROOT

from wincast.client import DATA, LOADING, OFFLINE, LiveClient

GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "golden_game.json").read_text("utf-8"))


class Fake(BaseHTTPRequestHandler):
    replies = []                                  # (status, body) per request

    def do_GET(self):
        status, body = Fake.replies.pop(0) if Fake.replies else (200, "{}")
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *a):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestLiveClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = LiveClient(f"http://127.0.0.1:{cls.server.server_port}")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.shutdown()
        cls.server.server_close()

    def test_loading_screen_404_then_data(self):
        Fake.replies = [(404, '{"errorCode":"RESOURCE_NOT_FOUND"}'),
                        (200, "not json"),
                        (200, json.dumps({"gameData": {}})),
                        (200, json.dumps(GOLDEN["snapshots"][3]))]
        kinds = [self.client.poll().kind for _ in range(4)]
        self.assertEqual(kinds, [LOADING, LOADING, LOADING, DATA])

    def test_nothing_listening_is_offline(self):
        c = LiveClient(f"http://127.0.0.1:{free_port()}")
        self.addCleanup(c.close)
        self.assertEqual(c.poll().kind, OFFLINE)

    def test_https_without_pem_says_unverified(self):
        c = LiveClient("https://127.0.0.1:2999", cert=False)
        self.addCleanup(c.close)
        self.assertIn("unverified", c.tls)


if __name__ == "__main__":
    unittest.main()
