from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib import request as urllib_request


APP_USER_AGENT = "Solar-Manga-Translator/0.1"


def build_json_post_request(
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any],
    headers: Mapping[str, str] | None = None,
) -> urllib_request.Request:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": APP_USER_AGENT,
    }
    if headers:
        request_headers.update({str(name): str(value) for name, value in headers.items()})
    return urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
