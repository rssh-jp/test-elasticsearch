# test-elasticsearch

`docker-compose` でローカル Elasticsearch を起動し、Elastic Relevance Studio を同時に利用できる最小構成です。
日本語 Wikipedia から取得したデータを Elasticsearch に投入できます。

## 含まれる構成

- `elasticsearch` (single-node)
- `kibana`
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
- `make download-dump` はホスト上の `data/dumps/` へ保存し、`make ingest-full` はそのローカルファイルを使って投入します。
- `make ingest-full` / `make reingest-full` は、`jawiki_full_YYYYMMDD_HHMMSS` のような日時付きインデックスを新規作成し、Alias（既定: `jawiki_full_current`）を最新へ切り替えます。過去インデックスは削除しません。
- `make ingest-custom` はAlias（`jawiki_full_current`）経由で最新インデックスへ独自データを追加します。
- Kibana は `kibana_system` ユーザーで接続します。`es-setup` サービスが起動時に `kibana_system` のパスワードを設定します。
