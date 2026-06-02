#!/usr/bin/env python3
"""Load ES bulk-compatible NDJSON into Elasticsearch with alias switch."""
import base64
import glob
import json
import os
import socket
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List
from urllib.error import HTTPError, URLError


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
    timeout_seconds = int(env("ES_HTTP_TIMEOUT", "60"))
    retries = int(env("ES_HTTP_RETRIES", "5"))

    for attempt in range(retries + 1):
        req = urllib.request.Request(url=url, data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        req.add_header("User-Agent", "test-elasticsearch-bulk-json-to-es/1.0 (+local)")

        if auth_user:
            token = base64.b64encode(f"{auth_user}:{auth_password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")

        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as res:
                return res.read()
        except HTTPError as exc:
            retriable_status = {429, 502, 503, 504}
            if exc.code in retriable_status and attempt < retries:
                sleep_seconds = min(2 ** attempt, 15)
                print(
                    f"[warn] transient HTTP {exc.code} for {method} {url}; retry {attempt + 1}/{retries} in {sleep_seconds}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_seconds)
                continue
            raise
        except (URLError, TimeoutError, socket.timeout, ConnectionResetError) as exc:
            if attempt < retries:
                sleep_seconds = min(2 ** attempt, 15)
                print(
                    f"[warn] transient network error for {method} {url}: {exc}; retry {attempt + 1}/{retries} in {sleep_seconds}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_seconds)
                continue
            raise

    raise RuntimeError("unreachable: request retry loop exhausted")


def es_request(method: str, path: str, body: bytes = b"", content_type: str = "application/json") -> bytes:
    es_url = env("ES_URL", "http://localhost:9200").rstrip("/")
    es_user = env("ES_USER", "")
    es_password = env("ES_PASSWORD", "")
    return http_request(
        method,
        f"{es_url}{path}",
        body=body,
        content_type=content_type,
        auth_user=es_user,
        auth_password=es_password,
    )


def resource_exists(path: str) -> bool:
    try:
        es_request("HEAD", path)
        return True
    except HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def alias_targets(alias_name: str) -> Dict[str, object]:
    try:
        data = es_request("GET", f"/_alias/{alias_name}")
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, dict):
        return {}
    return parsed


def update_alias(alias_name: str, target_index: str) -> None:
    actions: List[Dict[str, object]] = []
    for index_name in sorted(alias_targets(alias_name).keys()):
        actions.append({"remove": {"index": index_name, "alias": alias_name}})
    actions.append({"add": {"index": target_index, "alias": alias_name, "is_write_index": True}})
    es_request("POST", "/_aliases", json.dumps({"actions": actions}).encode("utf-8"))
    print(f"[info] alias updated: {alias_name} -> {target_index}")


def generate_index_name() -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = f"jawiki-{date_str}"
    if not resource_exists(f"/{base}"):
        return base
    for seq in range(1, 1000):
        candidate = f"{base}-{seq}"
        if not resource_exists(f"/{candidate}"):
            return candidate
    raise RuntimeError("failed to generate unique index name")


def load_index_template_payload() -> Dict[str, object]:
    template_path = env("WIKI_INDEX_TEMPLATE_PATH", "resources/elastic/jawiki-index-template.json")
    with open(template_path, "r", encoding="utf-8") as f:
        raw = f.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid template JSON: {template_path}")
    return payload


def ensure_index_template() -> None:
    template_name = env("WIKI_INDEX_TEMPLATE_NAME", "jawiki-template")
    payload = load_index_template_payload()
    es_request(
        "PUT",
        f"/_index_template/{template_name}",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    print(f"[info] index template upserted: {template_name}")


def ensure_index(index_name: str) -> None:
    if resource_exists(f"/{index_name}"):
        print(f"[info] index exists: {index_name}")
        return
    es_request("PUT", f"/{index_name}")
    print(f"[info] index created: {index_name}")


def bulk_insert(index_name: str, lines: List[str]) -> int:
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    result = es_request(
        "POST",
        f"/{index_name}/_bulk",
        payload,
        content_type="application/x-ndjson",
    )
    parsed = json.loads(result.decode("utf-8"))
    if parsed.get("errors"):
        print("[error] bulk indexing had errors", file=sys.stderr)
        for item in parsed.get("items", [])[:5]:
            status = item.get("index", {}).get("status")
            if status and status >= 300:
                print(item, file=sys.stderr)
        raise RuntimeError("bulk indexing failed")
    return len(lines) // 2


def resolve_input_paths(input_path: str) -> List[str]:
    if os.path.exists(input_path):
        return [input_path]

    base_root, base_ext = os.path.splitext(input_path)
    if not base_ext:
        base_ext = ".ndjson"
    pattern = f"{base_root}.part*{base_ext}"
    part_paths = sorted(glob.glob(pattern))
    if part_paths:
        return part_paths

    raise RuntimeError(
        f"bulk JSON not found: {input_path} (also checked split pattern: {pattern})"
    )


def ingest_file(index_name: str, path: str, bulk_max_bytes: int) -> int:
    lines: List[str] = []
    lines_bytes = 0
    inserted = 0

    with open(path, "r", encoding="utf-8") as f:
        while True:
            action_line = f.readline()
            if not action_line:
                break
            doc_line = f.readline()
            if not doc_line:
                raise RuntimeError("invalid NDJSON: missing source line for final action line")

            # Validate action line and keep only supported fields.
            action = json.loads(action_line)
            if not isinstance(action, dict) or "index" not in action:
                raise RuntimeError("invalid NDJSON action line: expected {'index': {...}}")
            action_index = action.get("index", {})
            doc_id = str(action_index.get("_id", ""))
            if not doc_id:
                raise RuntimeError("invalid NDJSON action line: '_id' is required")

            action_payload = json.dumps({"index": {"_id": doc_id}}, ensure_ascii=False)
            doc_payload = doc_line.rstrip("\n")
            pair_bytes = len(action_payload.encode("utf-8")) + len(doc_payload.encode("utf-8")) + 2

            if pair_bytes > bulk_max_bytes:
                print(
                    f"[warn] single document payload ({pair_bytes} bytes) exceeds ES_BULK_MAX_BYTES={bulk_max_bytes}; sending alone",
                    file=sys.stderr,
                )

            if lines and (lines_bytes + pair_bytes > bulk_max_bytes):
                inserted += bulk_insert(index_name, lines)
                lines = []
                lines_bytes = 0

            lines.append(action_payload)
            lines.append(doc_payload)
            lines_bytes += pair_bytes

    if lines:
        inserted += bulk_insert(index_name, lines)

    return inserted


def ingest_bulk_json() -> int:
    input_path = env("BULK_JSON_PATH", "data/exports/jawiki.bulk.ndjson")
    bulk_max_bytes = int(env("ES_BULK_MAX_BYTES", str(10 * 1024 * 1024)))
    file_parallel = int(env("ES_FILE_PARALLEL", "2"))
    alias_name = env("WIKI_ALIAS_NAME", "jawiki_current")

    input_paths = resolve_input_paths(input_path)
    if len(input_paths) == 1:
        print(f"[info] input file: {input_paths[0]}")
    else:
        print(f"[info] input files: {len(input_paths)} (first: {input_paths[0]})")

    ensure_index_template()
    index_name = generate_index_name()
    ensure_index(index_name)

    old_targets = sorted(alias_targets(alias_name).keys())
    if old_targets:
        print(f"[info] existing alias targets for {alias_name}: {', '.join(old_targets)}")

    total = 0
    workers = max(1, min(file_parallel, len(input_paths)))
    if workers == 1:
        for path in input_paths:
            count = ingest_file(index_name, path, bulk_max_bytes)
            total += count
            print(f"[info] indexed={total} ({path})")
    else:
        print(f"[info] parallel file workers: {workers}")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(ingest_file, index_name, path, bulk_max_bytes): path
                for path in input_paths
            }
            for future in as_completed(futures):
                path = futures[future]
                count = future.result()
                total += count
                print(f"[info] indexed={total} ({path})")

    if total > 0:
        update_alias(alias_name, index_name)

    print(f"[done] indexed {total} documents into '{index_name}' (alias: {alias_name})")
    return 0


def main() -> int:
    return ingest_bulk_json()


if __name__ == "__main__":
    raise SystemExit(main())
