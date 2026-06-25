SHELL := /bin/bash  # 使用するシェルを指定（ここではbash）
COMPOSE := docker compose  # docker-composeコマンドのエイリアスを定義
SERVICES := elasticsearch kibana esrs-server esrs-worker esrs-server-mcp mysql  # 同時起動・停止したいDockerサービス一覧
PYTHON := python3  # 使用するPythonのバージョン（ここではpython3）
DUMP_PATH ?= /home/araumi/prj/github/test-elasticsearch/data/dumps/jawiki-latest-pages-articles-multistream.xml.bz2  # 日本語Wikipediaのダンプファイルの保存パス
WIKI_DUMP_URL ?= https://dumps.wikimedia.org/jawiki/latest/jawiki-latest-pages-articles-multistream.xml.bz2  # 日本語WikipediaのダンプファイルをダウンロードするURL
MYSQL_HOST ?= localhost  # MySQLデータベースのホスト名
MYSQL_PORT ?= 3307  # MySQLデータベースのポート番号
MYSQL_USER ?= wikiuser  # MySQLデータベースに接続するユーザー名
MYSQL_PASSWORD ?= wikipassword  # MySQLデータベースに接続するパスワード
MYSQL_DATABASE ?= jawiki  # 使用するMySQLデータベース名
TOKEN_GRAPH_DIR ?= tools  # token-graph.html用のHTTPサーバーを起動するディレクトリ
TOKEN_GRAPH_PORT ?= 8080  # token-graph.html用のHTTPサーバーが実行されるポート番号
TOKEN_GRAPH_PID_FILE ?= /tmp/test-elasticsearch-token-graph.pid  # プロセスIDファイルの保存パス
TOKEN_GRAPH_LOG_FILE ?= /tmp/test-elasticsearch-token-graph.log  # HTTPサーバーのログファイルの保存パス

.PHONY: help setup up down restart ps logs download-dump clean dump-to-mysql mysql-to-es ingest-via-mysql create-jawiki-template connect-mysql mysql-to-bulk-json bulk-json-to-es ingest-via-bulk-json token-graph-up token-graph-down

help: ## ヘルプを表示
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

setup: ## 必要な Python パッケージをインストール
	pip install pymysql --break-system-packages

up: ## Elasticsearch + MySQL + Relevance Studio を起動
	$(COMPOSE) up -d $(SERVICES)

down: ## サービスを停止
	$(COMPOSE) down

build: ## イメージをビルド（キャッシュなし）
	$(COMPOSE) build --no-cache

restart: down up ## サービスを再起動

ps: ## コンテナ状態を表示
	$(COMPOSE) ps

logs: ## 主要サービスのログを表示
	$(COMPOSE) logs -f --tail=200 $(SERVICES)

token-graph-up: ## token-graph.html 用のHTTPサーバーを起動（バックグラウンド）
	@if [[ -f "$(TOKEN_GRAPH_PID_FILE)" ]]; then \
		pid="$$(cat "$(TOKEN_GRAPH_PID_FILE)")"; \
		cmd="$$(ps -p "$$pid" -o args= 2>/dev/null || true)"; \
		if [[ -n "$$cmd" ]] && [[ "$$cmd" == *"$(PYTHON) -m http.server $(TOKEN_GRAPH_PORT)"* ]]; then \
			echo "token graph server is already running (pid=$$pid)"; \
			exit 0; \
		fi; \
		rm -f "$(TOKEN_GRAPH_PID_FILE)"; \
	fi
	@cd "$(TOKEN_GRAPH_DIR)" && nohup $(PYTHON) -m http.server $(TOKEN_GRAPH_PORT) >"$(TOKEN_GRAPH_LOG_FILE)" 2>&1 & echo $$! >"$(TOKEN_GRAPH_PID_FILE)"
	@echo "token graph server started: http://localhost:$(TOKEN_GRAPH_PORT)/token-graph.html"
	@echo "pid file: $(TOKEN_GRAPH_PID_FILE)"
	@echo "log file: $(TOKEN_GRAPH_LOG_FILE)"

token-graph-down: ## token-graph.html 用のHTTPサーバーを停止
	@if [[ -f "$(TOKEN_GRAPH_PID_FILE)" ]]; then \
		pid="$$(cat "$(TOKEN_GRAPH_PID_FILE)")"; \
		cmd="$$(ps -p "$$pid" -o args= 2>/dev/null || true)"; \
		if [[ -n "$$cmd" ]] && [[ "$$cmd" == *"$(PYTHON) -m http.server $(TOKEN_GRAPH_PORT)"* ]]; then \
			kill "$$pid"; \
			echo "token graph server stopped (pid=$$pid)"; \
			rm -f "$(TOKEN_GRAPH_PID_FILE)"; \
		else \
			rm -f "$(TOKEN_GRAPH_PID_FILE)"; \
			echo "token graph pid file was stale and has been removed"; \
		fi; \
	else \
		echo "token graph server is not running (pid file not found)"; \
	fi

download-dump: ## 日本語Wikipediaダンプをダウンロード
	mkdir -p /home/araumi/prj/github/test-elasticsearch/data/dumps
	wget -q --show-progress -O $(DUMP_PATH) "$(WIKI_DUMP_URL)"

dump-to-mysql: ## Wikiダンプ -> MySQL へ投入
	WIKI_DUMP_PATH=$(DUMP_PATH) \
	WIKI_MAX_DOC_CHARS=$${WIKI_MAX_DOC_CHARS:-5000} \
	MYSQL_BULK_SIZE=$${MYSQL_BULK_SIZE:-500} \
	MYSQL_HOST=$(MYSQL_HOST) \
	MYSQL_PORT=$(MYSQL_PORT) \
	MYSQL_USER=$(MYSQL_USER) \
	MYSQL_PASSWORD=$(MYSQL_PASSWORD) \
	MYSQL_DATABASE=$(MYSQL_DATABASE) \
	$(PYTHON) scripts/dump_to_mysql.py

mysql-to-es: ## MySQL -> Elasticsearch へ投入（jawiki-YYYYMMDD インデックス）
	ES_URL=$${ES_URL:-http://localhost:9200} \
	WIKI_ALIAS_NAME=$${WIKI_ALIAS_NAME:-jawiki_current} \
	ES_BULK_SIZE=$${ES_BULK_SIZE:-500} \
	ES_PARALLEL=$${ES_PARALLEL:-4} \
	ES_MAX_INFLIGHT=$${ES_MAX_INFLIGHT:-4} \
	ES_HTTP_TIMEOUT=$${ES_HTTP_TIMEOUT:-60} \
	ES_HTTP_RETRIES=$${ES_HTTP_RETRIES:-5} \
	MYSQL_HOST=$(MYSQL_HOST) \
	MYSQL_PORT=$(MYSQL_PORT) \
	MYSQL_USER=$(MYSQL_USER) \
	MYSQL_PASSWORD=$(MYSQL_PASSWORD) \
	MYSQL_DATABASE=$(MYSQL_DATABASE) \
	$(PYTHON) scripts/mysql_to_es.py

mysql-to-bulk-json: ## MySQL -> ES bulk互換NDJSONを生成
	BULK_JSON_PATH=$${BULK_JSON_PATH:-data/exports/jawiki.bulk.ndjson} \
	MYSQL_EXPORT_FETCH_SIZE=$${MYSQL_EXPORT_FETCH_SIZE:-1000} \
	MYSQL_EXPORT_LIMIT=$${MYSQL_EXPORT_LIMIT:-0} \
	MYSQL_EXPORT_MAX_FILE_BYTES=$${MYSQL_EXPORT_MAX_FILE_BYTES:-104857600} \
	MYSQL_HOST=$(MYSQL_HOST) \
	MYSQL_PORT=$(MYSQL_PORT) \
	MYSQL_USER=$(MYSQL_USER) \
	MYSQL_PASSWORD=$(MYSQL_PASSWORD) \
	MYSQL_DATABASE=$(MYSQL_DATABASE) \
	$(PYTHON) scripts/mysql_to_bulk_json.py

bulk-json-to-es: ## ES bulk互換NDJSON -> Elasticsearch へ投入
	ES_URL=$${ES_URL:-http://localhost:9200} \
	WIKI_ALIAS_NAME=$${WIKI_ALIAS_NAME:-jawiki_current} \
	WIKI_INDEX_TEMPLATE_NAME=$${WIKI_INDEX_TEMPLATE_NAME:-jawiki-template} \
	WIKI_INDEX_TEMPLATE_PATH=$${WIKI_INDEX_TEMPLATE_PATH:-resources/elastic/jawiki-index-template.json} \
	BULK_JSON_PATH=$${BULK_JSON_PATH:-data/exports/jawiki.bulk.ndjson} \
	ES_BULK_MAX_BYTES=$${ES_BULK_MAX_BYTES:-10485760} \
	ES_FILE_PARALLEL=$${ES_FILE_PARALLEL:-8} \
	ES_HTTP_TIMEOUT=$${ES_HTTP_TIMEOUT:-60} \
	ES_HTTP_RETRIES=$${ES_HTTP_RETRIES:-5} \
	$(PYTHON) scripts/bulk_json_to_es.py

ingest-via-bulk-json: mysql-to-bulk-json bulk-json-to-es ## MySQL抽出とES投入を分離実行

connect-mysql: ## MySQL に接続
	mysql --protocol=TCP -h $(MYSQL_HOST) -P $(MYSQL_PORT) -u $(MYSQL_USER) -p$(MYSQL_PASSWORD) $(MYSQL_DATABASE)

create-jawiki-template: ## resources/elastic から jawiki-* の index template を作成/更新
	ES_URL=$${ES_URL:-http://localhost:9200} \
	WIKI_INDEX_TEMPLATE_NAME=$${WIKI_INDEX_TEMPLATE_NAME:-jawiki-template} \
	WIKI_INDEX_TEMPLATE_PATH=$${WIKI_INDEX_TEMPLATE_PATH:-resources/elastic/jawiki-index-template.json} \
	$(PYTHON) scripts/create_index_template.py

ingest-via-mysql: dump-to-mysql mysql-to-es ## dump-to-mysql + mysql-to-es を順に実行

clean: ## 停止してボリュームを削除
	$(COMPOSE) down -v --remove-orphans