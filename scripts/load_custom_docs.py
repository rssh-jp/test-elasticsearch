#!/usr/bin/env python3
import base64
import json
import os
import sys
from urllib.error import HTTPError
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List


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
    req.add_header("User-Agent", "test-elasticsearch-custom-loader/1.0 (+local)")

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


def resource_exists(path: str) -> bool:
    try:
        es_request("HEAD", path)
        return True
    except HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def resolve_write_index_from_alias(alias_name: str) -> str:
    try:
        raw = es_request("GET", f"/_alias/{alias_name}")
    except HTTPError as exc:
        if exc.code == 404:
            return ""
        raise

    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict) or not parsed:
        return ""

    for index_name, meta in parsed.items():
        aliases = meta.get("aliases", {})
        alias_meta = aliases.get(alias_name, {})
        if alias_meta.get("is_write_index") is True:
            return index_name

    if len(parsed) == 1:
        return next(iter(parsed.keys()))

    return ""


def ensure_index(index_name: str) -> None:
    if resource_exists(f"/{index_name}"):
        return

    payload = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "title": {"type": "text", "analyzer": "cjk"},
                "body": {"type": "text", "analyzer": "cjk"},
                "url": {"type": "keyword"},
                "page_id": {"type": "integer"},
                "fetched_at": {"type": "date"},
                "categories_direct": {"type": "keyword"},
                "categories_level1": {"type": "keyword"},
                "categories_level2": {"type": "keyword"},
                "categories_all": {"type": "keyword"},
                "taxonomy_l1": {"type": "keyword"},
                "taxonomy_l2": {"type": "keyword"},
                "taxonomy_l3": {"type": "keyword"},
            }
        },
    }
    es_request("PUT", f"/{index_name}", json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def bulk_index(index_name: str, docs: List[Dict[str, object]]) -> None:
    lines: List[str] = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_index": index_name, "_id": doc["page_id"]}}))
        lines.append(json.dumps(doc, ensure_ascii=False))

    payload = ("\n".join(lines) + "\n").encode("utf-8")
    result = es_request("POST", "/_bulk", payload)
    parsed = json.loads(result.decode("utf-8"))
    if parsed.get("errors"):
        print("[error] bulk indexing had errors", file=sys.stderr)
        raise RuntimeError("bulk indexing failed")


def main() -> int:
    alias_name = env("WIKI_FULL_ALIAS_NAME", "jawiki_full_current")
    fallback_index = env("WIKI_FULL_INDEX_NAME", "jawiki_full")
    data_path = env("CUSTOM_DATA_PATH", "/home/araumi/prj/github/test-elasticsearch/data/custom/custom_docs.jsonl")
    bulk_size = int(env("CUSTOM_BULK_SIZE", "200"))

    if not os.path.exists(data_path):
        print(f"[warn] custom data file not found: {data_path}")
        print("[warn] skip custom docs ingest")
        return 0

    target_index = alias_name
    resolved_write_index = resolve_write_index_from_alias(alias_name)
    if resolved_write_index:
        print(f"[info] alias write index: {alias_name} -> {resolved_write_index}")
    else:
        ensure_index(fallback_index)
        target_index = fallback_index
        print(f"[warn] alias not found: {alias_name}; fallback index: {fallback_index}")

    batch: List[Dict[str, object]] = []
    total = 0
    now = datetime.now(timezone.utc).isoformat()

    with open(data_path, "r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            s = line.strip()
            if not s:
                continue

            try:
                item = json.loads(s)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSON at line {line_no}: {exc}") from exc

            if "page_id" not in item or "title" not in item or "body" not in item:
                raise RuntimeError(f"missing required fields at line {line_no}: page_id,title,body")

            doc = {
                "page_id": int(item["page_id"]),
                "title": str(item["title"]),
                "body": str(item["body"]),
                "url": str(item.get("url", "")),
                "fetched_at": str(item.get("fetched_at", now)),
                "taxonomy_l1": str(item.get("taxonomy_l1", "その他")),
                "taxonomy_l2": str(item.get("taxonomy_l2", "未分類")),
                "taxonomy_l3": str(item.get("taxonomy_l3", str(item["title"]))),
            }
            batch.append(doc)

            if len(batch) >= bulk_size:
                bulk_index(target_index, batch)
                total += len(batch)
                batch.clear()
                print(f"[info] custom indexed={total}")

    if batch:
        bulk_index(target_index, batch)
        total += len(batch)

    print(f"[done] custom indexed {total} docs into '{target_index}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
