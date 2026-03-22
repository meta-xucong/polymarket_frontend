from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path


class PanelIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        panel_dir = Path(__file__).resolve().parents[1]
        if str(panel_dir) not in sys.path:
            sys.path.insert(0, str(panel_dir))
        self.instance_dir = Path(tempfile.mkdtemp(prefix="poly_panel_test_"))
        os.environ["POLY_APP_ROOT"] = r"D:\AI\vibe_coding4"
        os.environ["POLY_INSTANCE_ROOT"] = str(self.instance_dir)
        os.environ["POLY_AUTH_DEFAULT_USERNAME"] = "admin"
        os.environ["POLY_AUTH_DEFAULT_PASSWORD"] = "admin"
        os.environ["POLY_AUTH_REQUIRED"] = "1"
        os.environ["POLY_SESSION_SECRET"] = "panel_test_secret"
        os.environ["POLY_FORCE_SOURCE_SERVICES"] = "1"

        import runtime_paths  # noqa: WPS433
        import config_store  # noqa: WPS433
        import server  # noqa: WPS433

        self.runtime_paths = importlib.reload(runtime_paths)
        self.config_store = importlib.reload(config_store)
        self.server = importlib.reload(server)

    def test_runtime_paths_and_instance_write(self) -> None:
        self.assertEqual(self.runtime_paths.resolve_instance_root(), self.instance_dir.resolve())
        self.assertEqual(
            self.runtime_paths.resolve_v2_root(),
            Path(r"D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2").resolve(),
        )

        account = self.config_store.get_account_payload()
        account["POLY_HOST"] = "https://clob.polymarket.com"
        self.config_store.save_account_payload(account)

        expected_account = self.instance_dir / "v2" / "account.json"
        self.assertTrue(expected_account.exists())

    def test_auth_guard_and_runtime_api(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        httpd = self.server.create_http_server("127.0.0.1", port)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)

        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/runtime")
        unauthorized = conn.getresponse()
        self.assertEqual(unauthorized.status, 401)
        unauthorized.read()

        conn.request(
            "POST",
            "/api/auth/login",
            body=json.dumps({"username": "admin", "password": "admin"}),
            headers={"Content-Type": "application/json"},
        )
        login = conn.getresponse()
        self.assertEqual(login.status, 200)
        login_payload = json.loads(login.read().decode("utf-8"))
        self.assertTrue(login_payload["must_change_credentials"])
        cookie = (login.getheader("Set-Cookie") or "").split(";", 1)[0]
        self.assertTrue(cookie.startswith("poly_panel_session="))

        conn.request("GET", "/api/runtime", headers={"Cookie": cookie})
        blocked = conn.getresponse()
        self.assertEqual(blocked.status, 403)
        blocked.read()

        conn.request(
            "POST",
            "/api/auth/credentials",
            body=json.dumps(
                {
                    "username": "operator01",
                    "password": "secret123",
                    "password_confirm": "secret123",
                }
            ),
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        update_resp = conn.getresponse()
        self.assertEqual(update_resp.status, 200)
        update_payload = json.loads(update_resp.read().decode("utf-8"))
        self.assertFalse(update_payload["must_change_credentials"])
        new_cookie = (update_resp.getheader("Set-Cookie") or "").split(";", 1)[0]
        self.assertTrue(new_cookie.startswith("poly_panel_session="))

        auth_path = self.instance_dir / "panel" / "auth.json"
        auth_payload = json.loads(auth_path.read_text(encoding="utf-8"))
        self.assertEqual(auth_payload["username"], "operator01")
        self.assertFalse(auth_payload["must_change_credentials"])

        conn.request("GET", "/api/runtime", headers={"Cookie": new_cookie})
        runtime_resp = conn.getresponse()
        self.assertEqual(runtime_resp.status, 200)
        runtime_payload = json.loads(runtime_resp.read().decode("utf-8"))
        self.assertIn("instance", runtime_payload)

        conn.request("POST", "/api/auth/logout", headers={"Cookie": new_cookie})
        logout = conn.getresponse()
        self.assertEqual(logout.status, 200)
        logout.read()

        conn.request("GET", "/api/runtime", headers={"Cookie": new_cookie})
        after = conn.getresponse()
        self.assertEqual(after.status, 401)
        after.read()

        conn.close()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    unittest.main()
