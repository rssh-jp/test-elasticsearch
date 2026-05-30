#!/usr/bin/env python3
"""Load Japanese Wikipedia dump into MySQL.

Usage:
    python scripts/dump_to_mysql.py [--limit N]

Environment variables:
    WIKI_DUMP_PATH      path to bz2 dump file (default: /data/dumps/jawiki-latest-...)
    WIKI_MAX_DOC_CHARS  truncate body text to this length (default: 5000)
    MYSQL_BULK_SIZE     rows per INSERT batch (default: 500)
    MYSQL_HOST          MySQL host (default: localhost)
    MYSQL_PORT          MySQL port (default: 3306)
    MYSQL_USER          MySQL user (default: wikiuser)
    MYSQL_PASSWORD      MySQL password (default: wikipassword)
    MYSQL_DATABASE      MySQL database name (default: jawiki)
"""
import argparse
import bz2
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, Iterable, List

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    print("[error] pymysql is required: pip install pymysql", file=sys.stderr)
    sys.exit(1)

DEFAULT_DUMP_PATH = "/data/dumps/jawiki-latest-pages-articles-multistream.xml.bz2"

TAXONOMY_RULES = [
    (
        "人物",
        [
            ("政治家", ["政治家", "首相", "大統領", "議員", "政治", "知事", "市長", "内閣"]),
            ("芸能人", ["俳優", "女優", "歌手", "声優", "タレント", "ミュージシャン", "アイドル", "モデル"]),
            ("スポーツ選手", ["野球選手", "サッカー選手", "選手", "力士", "アスリート", "五輪", "オリンピック"]),
            ("学者・研究者", ["学者", "研究者", "科学者", "数学者", "物理学者", "化学者", "生物学者", "教授"]),
            ("作家・芸術家", ["作家", "詩人", "画家", "彫刻家", "写真家", "漫画家", "小説家", "版画家", "デザイナー"]),
            ("宗教家・思想家", ["僧", "司祭", "神学", "哲学者", "思想家"]),
        ],
    ),
    (
        "地理",
        [
            ("国・地域", ["国", "地域", "州", "都道府県", "県", "市", "町", "村", "郡", "行政区", "首都", "自治体"]),
            ("自然地理", ["山", "川", "湖", "海", "島", "半島", "湾", "火山", "砂漠", "森林", "平野", "高原", "盆地"]),
            ("交通地理", ["駅", "空港", "港", "鉄道路線", "道路", "高速道路", "航路"]),
            ("観光地", ["観光地", "景勝地", "名所", "世界遺産"]),
        ],
    ),
    (
        "生物",
        [
            ("動物", ["動物", "哺乳類", "鳥類", "魚類", "昆虫", "爬虫類", "両生類", "甲殻類", "軟体動物"]),
            ("植物", ["植物", "樹木", "花", "草本", "菌類", "藻類", "シダ植物", "被子植物"]),
            ("微生物", ["細菌", "ウイルス", "微生物", "真菌", "病原体"]),
            ("分類・生態", ["分類学", "生態", "絶滅危惧", "外来種"]),
        ],
    ),
    (
        "建築・施設",
        [
            ("建築物", ["建築", "ビル", "塔", "城", "寺", "神社", "教会", "住宅", "博物館", "劇場", "宮殿", "庁舎"]),
            ("交通施設", ["駅舎", "空港", "港湾", "バスターミナル", "インターチェンジ"]),
            ("インフラ", ["橋", "ダム", "トンネル", "発電所", "浄水場", "下水道", "送電"]),
            ("文化施設", ["図書館", "美術館", "博物館", "ホール", "スタジアム"]),
        ],
    ),
    (
        "書物・作品",
        [
            ("文学", ["小説", "詩", "文学", "書物", "文庫", "叢書", "随筆", "戯曲"]),
            ("映像・舞台", ["映画", "ドラマ", "アニメ", "舞台", "演劇", "特撮", "ドキュメンタリー"]),
            ("音楽", ["楽曲", "アルバム", "オペラ", "交響曲", "シングル", "合唱曲"]),
            ("ゲーム", ["ゲーム", "コンピュータゲーム", "アーケードゲーム", "ボードゲーム"]),
            ("漫画・出版", ["漫画", "雑誌", "出版社", "コミック"]),
        ],
    ),
    (
        "組織・制度",
        [
            ("企業", ["企業", "会社", "株式会社", "メーカー", "銀行", "証券", "保険", "商社"]),
            ("教育機関", ["大学", "学校", "高校", "中学校", "小学校", "研究所", "学部", "学科"]),
            ("政治・行政組織", ["政府", "省", "庁", "自治体", "政党", "国際機関", "議会", "委員会"]),
            ("法律・制度", ["法律", "条例", "制度", "憲法", "規則", "政令", "判例"]),
            ("軍事・治安組織", ["軍", "自衛隊", "警察", "消防", "海軍", "空軍"]),
        ],
    ),
    (
        "歴史・事件",
        [
            ("戦争・紛争", ["戦争", "内戦", "紛争", "戦闘", "軍事", "会戦", "遠征"]),
            ("事件・災害", ["事件", "事故", "災害", "地震", "津波", "火災", "噴火", "台風"]),
            ("時代・王朝", ["時代", "王朝", "幕府", "文明", "元号", "中世", "近世", "近代"]),
            ("歴史人物・史跡", ["史跡", "遺跡", "古墳", "城跡"]),
        ],
    ),
    (
        "科学・技術",
        [
            ("自然科学", ["物理学", "化学", "生物学", "天文学", "地質学", "気象学", "海洋学"]),
            ("工学・情報", ["工学", "情報", "コンピュータ", "ソフトウェア", "インターネット", "人工知能", "AI", "機械学習", "データベース"]),
            ("医療", ["医学", "医療", "病気", "疾患", "薬", "治療", "診断", "外科", "内科"]),
            ("数学・統計", ["数学", "統計", "確率", "代数", "幾何", "解析"]),
        ],
    ),
    (
        "宗教・思想",
        [
            ("宗教", ["宗教", "神道", "仏教", "キリスト教", "イスラム教", "寺院", "神社"]),
            ("哲学・思想", ["哲学", "思想", "倫理", "心理学", "認識論", "形而上学"]),
            ("神話・伝承", ["神話", "伝承", "民話", "伝説"]),
        ],
    ),
    (
        "経済・社会",
        [
            ("経済", ["経済", "金融", "市場", "株式", "為替", "インフレ", "GDP", "財政"]),
            ("社会", ["社会", "人口", "雇用", "福祉", "教育政策", "ジェンダー", "移民"]),
            ("統計・指標", ["統計", "指数", "国勢調査", "指標"]),
        ],
    ),
    (
        "生活・文化",
        [
            ("食文化", ["料理", "食品", "飲料", "酒", "茶", "菓子", "レシピ"]),
            ("ファッション", ["服飾", "ファッション", "衣装", "ブランド"]),
            ("趣味・娯楽", ["趣味", "娯楽", "玩具", "コレクション", "旅行"]),
            ("祭り・行事", ["祭", "祭り", "行事", "年中行事", "祝祭"]),
        ],
    ),
]


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def normalize_category_name(name: str) -> str:
    cleaned = name.replace("_", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def extract_categories_from_wikitext(text: str) -> List[str]:
    matches = re.findall(r"\[\[(?:Category|カテゴリ):([^\]|]+)(?:\|[^\]]*)?\]\]", text)
    return dedupe_keep_order(normalize_category_name(m) for m in matches if normalize_category_name(m))


def classify_taxonomy(title: str, direct_categories: List[str], all_categories: List[str]) -> Dict[str, str]:
    corpus = " ".join([title] + all_categories)
    for l1, medium_rules in TAXONOMY_RULES:
        for l2, keywords in medium_rules:
            matched = [kw for kw in keywords if kw in corpus]
            if not matched:
                continue

            l3 = ""
            for cat in direct_categories:
                if any(kw in cat for kw in matched):
                    l3 = cat
                    break
            if not l3:
                for cat in all_categories:
                    if any(kw in cat for kw in matched):
                        l3 = cat
                        break
            if not l3:
                l3 = direct_categories[0] if direct_categories else l2

            return {"taxonomy_l1": l1, "taxonomy_l2": l2, "taxonomy_l3": l3}

    fallback_l3 = direct_categories[0] if direct_categories else "未分類"
    return {"taxonomy_l1": "その他", "taxonomy_l2": "未分類", "taxonomy_l3": fallback_l3}


def wiki_url_from_title(title: str) -> str:
    import urllib.parse
    return "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def strip_wikitext(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*>.*?</ref>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)

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


def iter_pages_from_dump(dump_path: str) -> Iterable[Dict[str, str]]:
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
            yield {
                "title": page_title,
                "page_id": page_id,
                "text": page_text,
                "ns": page_ns,
                "redirect": "1" if has_redirect else "0",
            }


def iter_articles_from_dump(dump_path: str) -> Iterable[Dict[str, str]]:
    for page in iter_pages_from_dump(dump_path):
        if page["ns"] != "0":
            continue
        if page["redirect"] == "1":
            continue
        if not page["title"] or not page["page_id"] or not page["text"]:
            continue
        yield page


def category_name_from_page_title(title: str) -> str:
    if ":" in title:
        _, name = title.split(":", 1)
        return normalize_category_name(name)
    return normalize_category_name(title)


def build_category_parent_map(dump_path: str) -> Dict[str, List[str]]:
    parent_map: Dict[str, List[str]] = {}
    scanned = 0
    mapped = 0
    for page in iter_pages_from_dump(dump_path):
        scanned += 1
        if page["ns"] != "14":
            continue
        if page["redirect"] == "1":
            continue
        if not page["title"] or not page["text"]:
            continue

        cat = category_name_from_page_title(page["title"])
        parents = extract_categories_from_wikitext(page["text"])
        if cat and parents:
            parent_map[cat] = parents
            mapped += 1

        if scanned % 100000 == 0:
            print(f"[info] category-scan pages={scanned} mapped={mapped}")

    print(f"[info] category parent map built: {mapped} categories")
    return parent_map


def resolve_category_hierarchy(
    direct_categories: List[str],
    parent_map: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    level1: List[str] = []
    level2: List[str] = []

    for c in direct_categories:
        p1 = parent_map.get(c, [])
        level1.extend(p1)
        for p in p1:
            level2.extend(parent_map.get(p, []))

    level1_d = dedupe_keep_order(v for v in level1 if v not in direct_categories)
    level2_d = dedupe_keep_order(v for v in level2 if v not in direct_categories and v not in level1_d)
    all_cats = dedupe_keep_order(direct_categories + level1_d + level2_d)
    return {
        "categories_direct": direct_categories,
        "categories_level1": level1_d,
        "categories_level2": level2_d,
        "categories_all": all_cats,
    }


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


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS wiki_pages (
                page_id      INT UNSIGNED NOT NULL,
                title        VARCHAR(500) NOT NULL,
                body         LONGTEXT,
                url          VARCHAR(1000),
                fetched_at   DATETIME,
                categories_direct JSON,
                categories_level1 JSON,
                categories_level2 JSON,
                categories_all    JSON,
                taxonomy_l1  VARCHAR(100),
                taxonomy_l2  VARCHAR(100),
                taxonomy_l3  VARCHAR(200),
                PRIMARY KEY (page_id),
                INDEX idx_taxonomy_l1 (taxonomy_l1),
                INDEX idx_taxonomy_l2 (taxonomy_l2)
            ) ENGINE=InnoDB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
    conn.commit()


def batch_insert(conn, rows: list) -> None:
    sql = """
        INSERT INTO wiki_pages
            (page_id, title, body, url, fetched_at,
             categories_direct, categories_level1, categories_level2, categories_all,
             taxonomy_l1, taxonomy_l2, taxonomy_l3)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            title              = VALUES(title),
            body               = VALUES(body),
            url                = VALUES(url),
            fetched_at         = VALUES(fetched_at),
            categories_direct  = VALUES(categories_direct),
            categories_level1  = VALUES(categories_level1),
            categories_level2  = VALUES(categories_level2),
            categories_all     = VALUES(categories_all),
            taxonomy_l1        = VALUES(taxonomy_l1),
            taxonomy_l2        = VALUES(taxonomy_l2),
            taxonomy_l3        = VALUES(taxonomy_l3)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def run_ingest(limit: int) -> int:
    dump_path = env("WIKI_DUMP_PATH", DEFAULT_DUMP_PATH)
    bulk_size = int(env("MYSQL_BULK_SIZE", "500"))
    max_chars = int(env("WIKI_MAX_DOC_CHARS", "5000"))

    conn = get_mysql_conn()
    ensure_table(conn)
    print("[info] table ensured: wiki_pages")

    # 1パス: 記事をバッファリングしながらカテゴリマップを同時構築
    # (ダンプ内の順序: ns=0 記事 → ns=14 カテゴリ)
    buffered: list = []
    category_parent_map: Dict[str, List[str]] = {}
    scanned = 0
    cat_mapped = 0

    for page in iter_pages_from_dump(dump_path):
        scanned += 1

        if page["ns"] == "0" and page["redirect"] == "0" \
                and page["title"] and page["page_id"] and page["text"]:
            cleaned = strip_wikitext(page["text"])
            if cleaned:
                direct_cats = extract_categories_from_wikitext(page["text"])
                buffered.append((
                    int(page["page_id"]),
                    page["title"],
                    cleaned[:max_chars],
                    wiki_url_from_title(page["title"]),
                    direct_cats,
                ))

        elif page["ns"] == "14" and page["redirect"] == "0" \
                and page["title"] and page["text"]:
            cat = category_name_from_page_title(page["title"])
            parents = extract_categories_from_wikitext(page["text"])
            if cat and parents:
                category_parent_map[cat] = parents
                cat_mapped += 1

        if scanned % 100000 == 0:
            print(f"[info] scan pages={scanned} articles_buffered={len(buffered)} categories={cat_mapped}")

    print(f"[info] scan complete: pages={scanned} articles={len(buffered)} categories={cat_mapped}")

    # バッファから MySQL へ挿入
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    batch: list = []
    total = 0

    articles = buffered[:limit] if limit > 0 else buffered
    for page_id, title, body, url, direct_cats in articles:
        hierarchy = resolve_category_hierarchy(direct_cats, category_parent_map)
        taxonomy = classify_taxonomy(
            title,
            hierarchy["categories_direct"],
            hierarchy["categories_all"],
        )
        row = (
            page_id,
            title,
            body,
            url,
            now,
            json.dumps(hierarchy["categories_direct"], ensure_ascii=False),
            json.dumps(hierarchy["categories_level1"], ensure_ascii=False),
            json.dumps(hierarchy["categories_level2"], ensure_ascii=False),
            json.dumps(hierarchy["categories_all"], ensure_ascii=False),
            taxonomy["taxonomy_l1"],
            taxonomy["taxonomy_l2"],
            taxonomy["taxonomy_l3"],
        )
        batch.append(row)
        if len(batch) >= bulk_size:
            batch_insert(conn, batch)
            total += len(batch)
            batch.clear()
            print(f"[info] inserted={total}")

    if batch:
        batch_insert(conn, batch)
        total += len(batch)

    conn.close()
    print(f"[done] total {total} rows inserted into wiki_pages")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Load Wikipedia dump into MySQL")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N rows (0 = no limit)")
    args = parser.parse_args()
    return run_ingest(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
