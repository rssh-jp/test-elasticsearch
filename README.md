# test-elasticsearch

`docker-compose` でローカル Elasticsearch を起動し、Elastic Relevance Studio を同時に利用できる最小構成です。
日本語 Wikipedia から取得したデータを Elasticsearch に投入できます。

## 含まれる構成

- `elasticsearch` (single-node)
- `kibana`
- `mysql` (Wiki データの中間ストア)
- `esrs-server` (Relevance Studio UI/API)
- `esrs-worker` (バックグラウンド処理)
- `esrs-server-mcp` (MCP endpoint)
- `wiki-loader` (日本語 Wikipedia からデータ投入)

## 前提

- Docker / Docker Compose が使えること
- 空きメモリに余裕があること（Elasticsearch 用に最低 2GB 程度推奨）

## 使い方

環境変数を調整したい場合のみ `.env.example` を `.env` にコピーして編集してください。

```bash
cp .env.example .env
```

起動:

```bash
make up
```

日本語 Wikipedia データ投入:

```bash
make ingest
```

日本語 Wikipedia ダンプの取得（ホストに保存）:

```bash
make download-dump
```

日本語 Wikipedia ダンプの全量投入（ローカル保存済みダンプを利用）:

```bash
make ingest-full
```

起動 + 投入を一括実行:

```bash
make bootstrap
```

停止:

```bash
make down
```

ボリュームごと削除:

```bash
make clean
```

## アクセス先

- Elasticsearch: `http://localhost:9200`
- Kibana: `http://localhost:5601`
- Relevance Studio: `http://localhost:4096`
- MCP endpoint: `http://localhost:4200/mcp`

## 補足

- Relevance Studio は公式リポジトリ `elastic/relevance-studio` の Dockerfile を利用してビルドします。
- Wikipedia 取り込みスクリプトは MediaWiki API (`ja.wikipedia.org`) のランダム記事から要約を収集して、`jawiki` インデックスへ bulk 投入します。
- 全量取り込みは Wikimedia dump (`jawiki-latest-pages-articles-multistream.xml.bz2`) を使って `jawiki_full` インデックスへ投入します。完了まで長時間かかり、ディスク容量も大きく消費します。

## 多段投入フロー（Wiki → MySQL → Elasticsearch）

Wiki ダンプを MySQL に取り込んでから Elasticsearch へ投入する 2 段階パイプラインです。

```bash
# ステップ1: Wikiダンプ → MySQL（テーブル自動作成・UPSERT）
make dump-to-mysql

# ステップ2: MySQL → Elasticsearch（jawiki-YYYYMMDD インデックス作成 + エイリアス切替）
make mysql-to-es

# 2ステップを一括実行
make ingest-via-mysql
```

- `make dump-to-mysql` は `wiki_pages` テーブルを作成し、カテゴリ階層・分類付きで全記事を INSERT します。
- `make mysql-to-es` は `jawiki-YYYYMMDD` 形式のインデックスを新規作成して全量 bulk insert し、
  完了後にエイリアス（既定: `jawiki_current`）を切り替えます。
- ホストから実行する場合は `pip install pymysql` が必要です。

### MySQL 接続先

| 設定 | 既定値 |
|------|--------|
| ホスト | `localhost` |
| ポート | `3306` |
| ユーザー | `wikiuser` |
| パスワード | `wikipassword` |
| データベース | `jawiki` |

環境変数 `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` で上書き可能です。
- `make download-dump` はホスト上の `data/dumps/` へ保存し、`make ingest-full` はそのローカルファイルを使って投入します。
- `make ingest-full` / `make reingest-full` は、`jawiki_full_YYYYMMDD_HHMMSS` のような日時付きインデックスを新規作成し、Alias（既定: `jawiki_full_current`）を最新へ切り替えます。過去インデックスは削除しません。
- `make ingest-custom` はAlias（`jawiki_full_current`）経由で最新インデックスへ独自データを追加します。
- 全量投入ではカテゴリ階層フィールド（`categories_direct`, `categories_level1`, `categories_level2`, `categories_all`）も付与します。
- Kibana は `kibana_system` ユーザーで接続します。`es-setup` サービスが起動時に `kibana_system` のパスワードを設定します。
