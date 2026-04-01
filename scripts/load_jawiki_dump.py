#!/usr/bin/env python3
import argparse
import base64
import bz2
import html
import json
import os
import re
import shutil
import sys
from urllib.error import HTTPError
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, Iterable, List

DEFAULT_DUMP_URL = (
    "https://dumps.wikimedia.org/jawiki/latest/"
    "jawiki-latest-pages-articles-multistream.xml.bz2"
)
DEFAULT_DUMP_PATH = "/data/dumps/jawiki-latest-pages-articles-multistream.xml.bz2"


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def wiki_url_from_title(title: str) -> str:
    return "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


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
    req.add_header("User-Agent", "test-elasticsearch-wiki-dump-loader/1.0 (+local)")

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


def generate_timestamped_index_name(index_prefix: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for seq in range(1000):
        suffix = "" if seq == 0 else f"_{seq}"
        candidate = f"{index_prefix}_{ts}{suffix}"
        if not resource_exists(f"/{candidate}"):
            return candidate
    raise RuntimeError("failed to generate unique index name")


def update_alias(alias_name: str, target_index: str) -> None:
    actions: List[Dict[str, object]] = []
    for index_name in sorted(alias_targets(alias_name).keys()):
        actions.append({"remove": {"index": index_name, "alias": alias_name}})
    actions.append({"add": {"index": target_index, "alias": alias_name, "is_write_index": True}})
    payload = {"actions": actions}
    es_request("POST", "/_aliases", json.dumps(payload).encode("utf-8"))
    print(f"[info] alias updated: {alias_name} -> {target_index}")


def ensure_index(index_name: str) -> None:
    if resource_exists(f"/{index_name}"):
        print(f"[info] index exists: {index_name}")
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
            }
        },
    }
    es_request("PUT", f"/{index_name}", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    print(f"[info] index created: {index_name}")


def strip_wikitext(text: str) -> str:
    # Keep processing lightweight and deterministic for large dump streaming.
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*>.*?</ref>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove templates roughly; repeated pass handles many nested cases.
    for _ in range(3):
        new_text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
        if new_text == text:
            break
        text = new_text

    text = re.sub(r"\[\[(?:File|ファイル|Image|Category|カテゴリ):[^\]]+\]\]", " ", text)
    text = re.sub(r"\[https?://[^\s\]]+\s*([^\]]*)\]", r" \1 ", text)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r" \2 ", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r" \1 ", text)

    text = re.sub(r"^=+\s*(.*?)\s*=+$", r" \1 ", text, flags=re.MULTILINE)
    text = text.replace("'''", " ").replace("''", " ")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def iter_articles_from_dump(dump_path: str) -> Iterable[Dict[str, str]]:
    with bz2.open(dump_path, "rb") as fp:
        context = ET.iterparse(fp, events=("end",))
        for _, elem in context:
            if local_name(elem.tag) != "page":
                continue

            page_title = ""
            page_id = ""
            page_ns = ""
            page_text = ""
            has_redirect = False

            for child in list(elem):
                name = local_name(child.tag)
                if name == "title":
                    page_title = child.text or ""
                elif name == "id" and not page_id:
                    page_id = child.text or ""
                elif name == "ns":
                    page_ns = child.text or ""
                elif name == "redirect":
                    has_redirect = True
                elif name == "revision":
                    for rev_child in list(child):
                        if local_name(rev_child.tag) == "text":
                            page_text = rev_child.text or ""

            elem.clear()

            if page_ns != "0":
                continue
            if has_redirect:
                continue
            if not page_title or not page_id or not page_text:
                continue

            yield {
                "title": page_title,
                "page_id": page_id,
                "text": page_text,
            }


def bulk_index(index_name: str, docs: List[Dict[str, object]]) -> None:
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


def download_dump(url: str, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"[info] dump already exists: {path} ({size_mb:.1f} MB)")
        return

    tmp_path = f"{path}.part"
    print(f"[info] downloading dump: {url}")
    req = urllib.request.Request(url=url)
    req.add_header("User-Agent", "test-elasticsearch-wiki-dump-loader/1.0 (+local)")

    with urllib.request.urlopen(req, timeout=120) as res, open(tmp_path, "wb") as out:
        shutil.copyfileobj(res, out, length=1024 * 1024)

    os.replace(tmp_path, path)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"[info] dump downloaded: {path} ({size_mb:.1f} MB)")


def run_ingest(limit: int) -> int:
    index_prefix = env("WIKI_FULL_INDEX_PREFIX", "jawiki_full")
    alias_name = env("WIKI_FULL_ALIAS_NAME", "jawiki_full_current")
    dump_url = env("WIKI_DUMP_URL", DEFAULT_DUMP_URL)
    dump_path = env("WIKI_DUMP_PATH", DEFAULT_DUMP_PATH)
    bulk_size = int(env("WIKI_FULL_BULK_SIZE", "200"))
    max_chars = int(env("WIKI_MAX_DOC_CHARS", "5000"))
    index_name = generate_timestamped_index_name(index_prefix)

    download_dump(dump_url, dump_path)
    ensure_index(index_name)

    old_alias_targets = sorted(alias_targets(alias_name).keys())
    if old_alias_targets:
        print(f"[info] existing alias targets for {alias_name}: {', '.join(old_alias_targets)}")

    now = datetime.now(timezone.utc).isoformat()
    batch: List[Dict[str, object]] = []
    total_indexed = 0
    scanned = 0

    for page in iter_articles_from_dump(dump_path):
        scanned += 1
        cleaned = strip_wikitext(page["text"])
        if not cleaned:
            continue

        if limit > 0 and total_indexed >= limit:
            break

        doc = {
            "page_id": int(page["page_id"]),
            "title": page["title"],
            "body": cleaned[:max_chars],
            "url": wiki_url_from_title(page["title"]),
            "fetched_at": now,
        }
        batch.append(doc)

        if limit > 0 and (total_indexed + len(batch)) >= limit:
            remain = limit - total_indexed
            if remain > 0:
                batch = batch[:remain]
                bulk_index(index_name, batch)
                total_indexed += len(batch)
                print(f"[info] indexed={total_indexed} scanned={scanned}")
            batch.clear()
            break

        if len(batch) >= bulk_size:
            bulk_index(index_name, batch)
            total_indexed += len(batch)
            batch.clear()
            print(f"[info] indexed={total_indexed} scanned={scanned}")

    if batch and (limit <= 0 or total_indexed < limit):
        if limit > 0:
            remaining = max(0, limit - total_indexed)
            if remaining == 0:
                batch = []
            else:
                batch = batch[:remaining]
        if batch:
            bulk_index(index_name, batch)
            total_indexed += len(batch)

    if total_indexed > 0:
        update_alias(alias_name, index_name)

    print(f"[done] indexed {total_indexed} documents into '{index_name}' (alias: {alias_name})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Load full Japanese Wikipedia dump into Elasticsearch")
    parser.add_argument("--download-only", action="store_true", help="Download dump file only")
    parser.add_argument("--limit", type=int, default=0, help="Stop after indexing N docs (0 means no limit)")
    args = parser.parse_args()

    dump_url = env("WIKI_DUMP_URL", DEFAULT_DUMP_URL)
    dump_path = env("WIKI_DUMP_PATH", DEFAULT_DUMP_PATH)

    if args.download_only:
        download_dump(dump_url, dump_path)
        return 0

    return run_ingest(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
