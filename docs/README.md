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
3. データ投入用スクリプトでElasticsearchに全記事をインデックス
4. 独自データ（JSONL）を同一インデックスに追加投入
5. Relevance StudioやKibanaでデータを活用

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
