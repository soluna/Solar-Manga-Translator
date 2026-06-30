from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from http_requests import build_json_post_request


class HttpRequestTests(unittest.TestCase):
    def test_json_post_request_uses_app_user_agent(self) -> None:
        request = build_json_post_request(
            "https://api.example.com/v1/chat/completions",
            api_key="secret",
            payload={"model": "example-model"},
        )

        headers = dict(request.header_items())
        self.assertEqual(request.full_url, "https://api.example.com/v1/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"model": "example-model"})
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Content-type"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertIn("Solar-Manga-Translator", headers["User-agent"])


if __name__ == "__main__":
    unittest.main()
