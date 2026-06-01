#!/usr/bin/env python3
"""Create or update the jawiki index template from a JSON file."""
import base64
import json
import os
import sys
import urllib.request


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def http_request(
    method: str,
    url: str,
    body: bytes = b"",
    content_type: str = "application/json",
    auth_user: str = "",
    auth_password: str = "",
) -> bytes:
    req = urllib.request.Request(url=url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "test-elasticsearch-create-index-template/1.0 (+local)")

    if auth_user:
        token = base64.b64encode(f"{auth_user}:{auth_password}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")

    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read()


def es_request(method: str, path: str, body: bytes = b"") -> bytes:
    es_url = env("ES_URL", "http://localhost:9200").rstrip("/")
    es_user = env("ES_USER", "")
    es_password = env("ES_PASSWORD", "")
    return http_request(method, f"{es_url}{path}", body=body, auth_user=es_user, auth_password=es_password)


def load_template_payload() -> dict:
    template_path = env("WIKI_INDEX_TEMPLATE_PATH", "resources/elastic/jawiki-index-template.json")

    with open(template_path, "r", encoding="utf-8") as f:
        raw = f.read()

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid template JSON: {template_path}")
    return payload


def main() -> int:
    template_name = env("WIKI_INDEX_TEMPLATE_NAME", "jawiki-template")
    payload = load_template_payload()

    es_request(
        "PUT",
        f"/_index_template/{template_name}",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    print(f"[done] index template upserted: {template_name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[error] {exc}", file=sys.stderr)
        raise
