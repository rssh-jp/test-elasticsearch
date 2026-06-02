#!/usr/bin/env python3
"""Export wiki_pages from MySQL into ES bulk-compatible NDJSON."""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    print("[error] pymysql is required: pip install pymysql", file=sys.stderr)
    sys.exit(1)


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


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
            "page_id": page_id,
            "title": title,
            "body": body_text,
            "url": url or "",
            "fetched_at": fetched_at_str,
            "categories_direct": json.loads(cats_direct) if cats_direct else [],
            "categories_level1": json.loads(cats_l1) if cats_l1 else [],
            "categories_level2": json.loads(cats_l2) if cats_l2 else [],
            "categories_all": json.loads(cats_all) if cats_all else [],
            "taxonomy_l1": tax_l1 or "",
            "taxonomy_l2": tax_l2 or "",
            "taxonomy_l3": tax_l3 or "",
        }
        docs.append(doc)
    return docs


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


def export_bulk_json() -> int:
    output_path = env("BULK_JSON_PATH", "data/exports/jawiki.bulk.ndjson")
    fetch_size = int(env("MYSQL_EXPORT_FETCH_SIZE", "1000"))
    export_limit = int(env("MYSQL_EXPORT_LIMIT", "0"))
    max_file_bytes = int(env("MYSQL_EXPORT_MAX_FILE_BYTES", str(100 * 1024 * 1024)))

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    conn = get_mysql_conn()
    total = 0
    last_id = 0
    file_doc_count = 0
    part_no = 1
    written_files: List[str] = []

    base_root, base_ext = os.path.splitext(output_path)
    if not base_ext:
        base_ext = ".ndjson"

    def part_path(no: int) -> str:
        if max_file_bytes <= 0:
            return output_path
        return f"{base_root}.part{no:04d}{base_ext}"

    current_path = part_path(part_no)
    out = open(current_path, "w", encoding="utf-8")
    written_files.append(current_path)
    current_bytes = 0

    try:
        while True:
            remaining: Optional[int] = None
            if export_limit > 0:
                remaining = export_limit - total
                if remaining <= 0:
                    break

            limit = fetch_size if remaining is None else min(fetch_size, remaining)

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
                    (last_id, limit),
                )
                rows = cur.fetchall()

            if not rows:
                break

            last_id = rows[-1][0]
            docs = rows_to_docs(rows)
            for doc in docs:
                action = {"index": {"_id": str(doc["page_id"])}}
                action_line = json.dumps(action, ensure_ascii=False) + "\n"
                doc_line = json.dumps(doc, ensure_ascii=False) + "\n"
                pair_bytes = len(action_line.encode("utf-8")) + len(doc_line.encode("utf-8"))

                if max_file_bytes > 0 and file_doc_count > 0 and (current_bytes + pair_bytes > max_file_bytes):
                    out.close()
                    part_no += 1
                    current_path = part_path(part_no)
                    out = open(current_path, "w", encoding="utf-8")
                    written_files.append(current_path)
                    current_bytes = 0
                    file_doc_count = 0
                    print(f"[info] opened next export file: {current_path}")

                out.write(action_line)
                out.write(doc_line)
                current_bytes += pair_bytes
                file_doc_count += 1

            total += len(docs)
            print(f"[info] exported={total}")
    finally:
        out.close()

    conn.close()
    if len(written_files) == 1:
        print(f"[done] exported {total} docs to '{written_files[0]}'")
    else:
        print(f"[done] exported {total} docs into {len(written_files)} files (base: '{output_path}')")
    return 0


def main() -> int:
    return export_bulk_json()


if __name__ == "__main__":
    raise SystemExit(main())
