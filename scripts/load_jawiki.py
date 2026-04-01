#!/usr/bin/env python3
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

WIKI_API = "https://ja.wikipedia.org/w/api.php"
WIKI_EXTRACT_LIMIT = 20


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def wiki_get(params: Dict[str, str]) -> Dict:
    query = urllib.parse.urlencode(params)
    url = f"{WIKI_API}?{query}"
    request = urllib.request.Request(url=url)
    request.add_header("User-Agent", "test-elasticsearch-wiki-loader/1.0 (+local)")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def es_request(method: str, path: str, body: bytes = b"") -> bytes:
    es_url = env("ES_URL", "http://localhost:9200").rstrip("/")
    url = f"{es_url}{path}"

    request = urllib.request.Request(url=url, method=method, data=body)
    request.add_header("Content-Type", "application/json")

    user = os.environ.get("ES_USER", "")
    password = os.environ.get("ES_PASSWORD", "")
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def ensure_index(index_name: str) -> None:
    try:
        es_request("HEAD", f"/{index_name}")
        print(f"[info] index exists: {index_name}")
        return
    except Exception:
        pass

    payload = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "title": {"type": "text", "analyzer": "cjk"},
                "summary": {"type": "text", "analyzer": "cjk"},
                "url": {"type": "keyword"},
                "page_id": {"type": "integer"},
                "fetched_at": {"type": "date"},
            }
        },
    }
    es_request("PUT", f"/{index_name}", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    print(f"[info] index created: {index_name}")


def fetch_random_page_ids(target_count: int) -> List[int]:
    page_ids: List[int] = []
    seen = set()

    while len(page_ids) < target_count:
        remaining = target_count - len(page_ids)
        limit = min(50, remaining)
        data = wiki_get(
            {
                "action": "query",
                "format": "json",
                "list": "random",
                "rnnamespace": "0",
                "rnlimit": str(limit),
            }
        )

        for item in data.get("query", {}).get("random", []):
            pid = int(item["id"])
            if pid not in seen:
                seen.add(pid)
                page_ids.append(pid)

        print(f"[info] fetched random ids: {len(page_ids)}/{target_count}")
        time.sleep(0.2)

    return page_ids


def fetch_pages(page_ids: List[int]) -> List[Dict]:
    if not page_ids:
        return []

    docs: List[Dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for page_id in page_ids:
        data = wiki_get(
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "extracts|info",
                "pageids": str(page_id),
                "explaintext": "1",
                "exchars": "1200",
                "inprop": "url",
                "redirects": "1",
            }
        )

        pages = data.get("query", {}).get("pages", [])
        if not pages:
            continue

        page = pages[0]
        title = page.get("title")
        summary = (page.get("extract") or "").strip()
        if not title or not summary:
            continue

        docs.append(
            {
                "page_id": int(page["pageid"]),
                "title": title,
                "summary": summary,
                "url": page.get("fullurl", ""),
                "fetched_at": now,
            }
        )
        time.sleep(0.05)

    return docs


def bulk_index(index_name: str, docs: List[Dict]) -> None:
    if not docs:
        return

    lines: List[str] = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_index": index_name, "_id": doc["page_id"]}}))
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


def main() -> int:
    index_name = env("WIKI_INDEX_NAME", "jawiki")
    target_count = int(env("WIKI_DOC_COUNT", "200"))
    batch_size = int(env("WIKI_BATCH_SIZE", "50"))
    fetch_chunk_size = min(batch_size, WIKI_EXTRACT_LIMIT)

    if target_count <= 0 or batch_size <= 0:
        print("[error] WIKI_DOC_COUNT / WIKI_BATCH_SIZE must be > 0", file=sys.stderr)
        return 1

    print(
        f"[info] target index={index_name} count={target_count} "
        f"batch={batch_size} fetch_chunk={fetch_chunk_size}"
    )
    ensure_index(index_name)

    page_ids = fetch_random_page_ids(target_count)
    inserted = 0

    for i in range(0, len(page_ids), fetch_chunk_size):
        batch_ids = page_ids[i : i + fetch_chunk_size]
        docs = fetch_pages(batch_ids)
        bulk_index(index_name, docs)
        inserted += len(docs)
        print(f"[info] indexed documents: {inserted}/{target_count}")

    print(f"[done] indexed {inserted} documents into '{index_name}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
