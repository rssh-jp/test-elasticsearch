#!/usr/bin/env python3
"""Load data from MySQL into Elasticsearch.

Reads all rows from the wiki_pages table, creates a date-stamped index
(jawiki-YYYYMMDD), bulk-inserts all documents, then switches the alias
to the new index once all data has been loaded.

Usage:
    python scripts/mysql_to_es.py

Environment variables:
    ES_URL            Elasticsearch URL (default: http://localhost:9200)
    ES_USER           Elasticsearch user (default: elastic)
    ES_PASSWORD       Elasticsearch password (default: changeme)
    WIKI_ALIAS_NAME   Alias name to switch on completion (default: jawiki_current)
    WIKI_INDEX_TEMPLATE_NAME  Index template name (default: jawiki-template)
    WIKI_INDEX_TEMPLATE_PATH  Index template JSON path (default: resources/elastic/jawiki-index-template.json)
    ES_BULK_SIZE      Documents per bulk request (default: 200)
    ES_HTTP_TIMEOUT   HTTP timeout seconds (default: 60)
    ES_HTTP_RETRIES   Retry count for transient HTTP failures (default: 5)
    ES_MAX_INFLIGHT   Max in-flight bulk requests (default: ES_PARALLEL * 2)
    MYSQL_HOST        MySQL host (default: localhost)
    MYSQL_PORT        MySQL port (default: 3306)
    MYSQL_USER        MySQL user (default: wikiuser)
    MYSQL_PASSWORD    MySQL password (default: wikipassword)
    MYSQL_DATABASE    MySQL database name (default: jawiki)
"""
import base64
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

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    print("[error] pymysql is required: pip install pymysql", file=sys.stderr)
    sys.exit(1)


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# HTTP / Elasticsearch helpers
# ---------------------------------------------------------------------------

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
        req.add_header("User-Agent", "test-elasticsearch-mysql-to-es/1.0 (+local)")

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


def es_request(method: str, path: str, body: bytes = b"") -> bytes:
    es_url = env("ES_URL", "http://localhost:9200").rstrip("/")
    es_user = env("ES_USER", "")
    es_password = env("ES_PASSWORD", "")
    return http_request(method, f"{es_url}{path}", body=body, auth_user=es_user, auth_password=es_password)


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


def ensure_index(index_name: str) -> None:
    if resource_exists(f"/{index_name}"):
        print(f"[info] index exists: {index_name}")
        return

    # Mappings/settings are supplied by the jawiki-* index template.
    es_request("PUT", f"/{index_name}")
    print(f"[info] index created: {index_name}")


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


def bulk_index(index_name: str, docs: List[Dict]) -> None:
    if not docs:
        return

    lines: List[str] = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_index": index_name, "_id": str(doc["page_id"])}}))
        lines.append(json.dumps(doc, ensure_ascii=False))

    payload = ("\n".join(lines) + "\n").encode("utf-8")
    result = es_request("POST", "/_bulk", payload)
    parsed = json.loads(result.decode("utf-8"))
    if parsed.get("errors"):
        print("[error] bulk indexing had errors", file=sys.stderr)
        for item in parsed.get("items", [])[:5]:
            status = item.get("index", {}).get("status")
            if status and status >= 300:
                print(item, file=sys.stderr)
        raise RuntimeError("bulk indexing failed")


# ---------------------------------------------------------------------------
# MySQL helper
# ---------------------------------------------------------------------------

def get_mysql_conn():
    return pymysql.connect(
        host=env("MYSQL_HOST", "localhost"),
        port=int(env("MYSQL_PORT", "3306")),
        user=env("MYSQL_USER", "wikiuser"),
        password=env("MYSQL_PASSWORD", "wikipassword"),
        database=env("MYSQL_DATABASE", "jawiki"),
        charset="utf8mb4",
        connect_timeout=30,
        cursorclass=pymysql.cursors.Cursor,
    )


# ---------------------------------------------------------------------------
# Main ingest
# ---------------------------------------------------------------------------

def rows_to_docs(rows: list) -> List[Dict]:
    docs: List[Dict] = []
    for row in rows:
        (
            page_id, title, body, url, fetched_at,
            cats_direct, cats_l1, cats_l2, cats_all,
            tax_l1, tax_l2, tax_l3,
        ) = row

        if isinstance(fetched_at, datetime):
            fetched_at_str = fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            fetched_at_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        body_text = body or ""
        doc: Dict = {
            "page_id":             page_id,
            "title":               title,
            "body":                body_text,
            "url":                 url or "",
            "fetched_at":          fetched_at_str,
            "categories_direct":   json.loads(cats_direct)  if cats_direct else [],
            "categories_level1":   json.loads(cats_l1)      if cats_l1     else [],
            "categories_level2":   json.loads(cats_l2)      if cats_l2     else [],
            "categories_all":      json.loads(cats_all)     if cats_all    else [],
            "taxonomy_l1":         tax_l1 or "",
            "taxonomy_l2":         tax_l2 or "",
            "taxonomy_l3":         tax_l3 or "",
        }
        docs.append(doc)
    return docs


def run_ingest() -> int:
    alias_name  = env("WIKI_ALIAS_NAME", "jawiki_current")
    bulk_size   = int(env("ES_BULK_SIZE", "200"))
    parallelism = int(env("ES_PARALLEL", "4"))
    max_inflight = int(env("ES_MAX_INFLIGHT", str(max(2, parallelism * 2))))

    conn = get_mysql_conn()
    ensure_index_template()
    index_name = generate_index_name()
    ensure_index(index_name)

    old_targets = sorted(alias_targets(alias_name).keys())
    if old_targets:
        print(f"[info] existing alias targets for {alias_name}: {', '.join(old_targets)}")

    total = 0
    last_id = 0

    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {}

        while True:
            # カーソル方式: OFFSET の代わりに page_id > last_id で取得
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT page_id, title, body, url, fetched_at,
                           categories_direct, categories_level1, categories_level2, categories_all,
                           taxonomy_l1, taxonomy_l2, taxonomy_l3
                    FROM wiki_pages
                    WHERE page_id > %s
                    ORDER BY page_id
                    LIMIT %s
                    """,
                    (last_id, bulk_size),
                )
                rows = cur.fetchall()

            if not rows:
                break

            last_id = rows[-1][0]
            docs = rows_to_docs(rows)
            future = executor.submit(bulk_index, index_name, docs)
            futures[future] = len(docs)

            # Keep in-flight requests bounded to avoid overloading Elasticsearch.
            while len(futures) >= max_inflight:
                done_future = next(as_completed(futures))
                done_future.result()
                total += futures.pop(done_future)
                print(f"[info] indexed={total}")

            # 完了済みの future を回収してカウント
            done = [f for f in list(futures) if f.done()]
            for f in done:
                f.result()  # 例外があればここで raise
                total += futures.pop(f)
                print(f"[info] indexed={total}")

        # 残りの future を全部回収
        for f in as_completed(futures):
            f.result()
            total += futures[f]
            print(f"[info] indexed={total}")

    conn.close()

    if total > 0:
        update_alias(alias_name, index_name)

    print(f"[done] indexed {total} documents into '{index_name}' (alias: {alias_name})")
    return 0


def main() -> int:
    return run_ingest()


if __name__ == "__main__":
    raise SystemExit(main())
