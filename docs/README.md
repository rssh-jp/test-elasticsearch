# プロジェクト概要

## 目的
- ローカル環境でElasticsearchを立ち上げ、日本語Wikipedia（jawiki）の全データを投入する。
- jpwikiデータに独自データを追加して、同じ手順で何度でも再投入できる状態にする。
- 投入したデータを使い、以下のツールで検索・分析・可視化を行う。
  - Relevance Studio
  - Kibana（集約クエリや可視化）

## 主な手順
1. Docker ComposeでElasticsearch・Kibana・データ投入用ツールを起動
2. Wikipediaダンプデータ（jawiki-latest-pages-articles-multistream.xml.bz2）をダウンロード
3. データ投入用スクリプトでElasticsearchに全記事をインデックス（カテゴリ階層を付与）
4. 独自データ（JSONL）を同一インデックスに追加投入
5. Relevance StudioやKibanaでデータを活用

## 取り込みデータの範囲
- 現在の全量投入は `jawiki-latest-pages-articles-multistream` を使用
- 取り込み対象は本文名前空間（ns=0）の非リダイレクト記事
- 編集履歴、Talkページ、添付ファイル実体などは取り込まない

## カテゴリ階層フィールド
- `categories_direct`: 記事に直接付与されているカテゴリ
- `categories_level1`: 直接カテゴリの親カテゴリ
- `categories_level2`: 親カテゴリのさらに親（孫カテゴリ相当）
- `categories_all`: 上記の統合

## 大中小分類フィールド（用途向け分類）
- `taxonomy_l1`: 大分類（例: 人物 / 地理 / 生物 / 建築・施設 / 書物・作品 など）
- `taxonomy_l2`: 中分類（例: 政治家 / 国・地域 / 動物 / 建築物 / 文学 など）
- `taxonomy_l3`: 小分類（カテゴリ名ベース）

この分類はカテゴリ文字列を使ったヒューリスティックです。業務用途に合わせてルール追加・調整できます。

集約クエリ例は [docs/kibana-category-queries.md](docs/kibana-category-queries.md) を参照。

カテゴリ階層を最新ロジックで反映するには、再投入を実行してください。
- `make reingest-full`

## 再投入フロー（繰り返し実行可能）
1. まずサービスを起動
  - make up
2. ダンプが未取得なら取得
  - make download-dump
3. 全量 + 独自データを入れ直す
  - make reingest-full

`make reingest-full` は次を順に実行します。
- `make ingest-full`（日時付き実インデックスを新規作成してjpwikiダンプを全量投入）
- `make ingest-custom`（独自JSONLをAlias経由で追加投入）

## インデックス運用方針（Alias経由アクセス）
- 実インデックスは日時付きで作成
  - 例: `jawiki_full_20260402_103015`
- 参照・追加入力はAliasでアクセス
  - 既定Alias: `jawiki_full_current`
- `make reingest-full` 実行時は、Aliasを最新の日時付きインデックスへ切り替え
- 旧インデックスは削除しない（比較検証やロールバックに利用可能）

## 独自データの置き場所
- パス: `data/custom/custom_docs.jsonl`
- 形式: 1行1JSON（JSONL）
- 必須項目: `page_id`, `title`, `body`
- 任意項目: `url`, `fetched_at`

例:

```json
{"page_id": 900000001, "title": "社内ナレッジ: 検索運用", "body": "運用ルールやFAQをここに記載", "url": "https://example.local/wiki/ops"}
{"page_id": 900000002, "title": "社内ナレッジ: 商品辞書", "body": "同義語辞書や表記ゆれルール", "url": "https://example.local/wiki/dict"}
```

## 想定ユースケース
- 日本語Wikipediaを使った全文検索・ランキング評価
- jpwiki + 独自データのハイブリッド検索評価
- Kibanaでの集約クエリ・可視化
- Relevance Studioでの検索体験のチューニング
