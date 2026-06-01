SHELL := /bin/bash
COMPOSE := docker compose
SERVICES := elasticsearch kibana esrs-server esrs-worker esrs-server-mcp mysql
PYTHON := python3
DUMP_PATH ?= /home/araumi/prj/github/test-elasticsearch/data/dumps/jawiki-latest-pages-articles-multistream.xml.bz2
WIKI_DUMP_URL ?= https://dumps.wikimedia.org/jawiki/latest/jawiki-latest-pages-articles-multistream.xml.bz2
MYSQL_HOST ?= localhost
MYSQL_PORT ?= 3307
MYSQL_USER ?= wikiuser
MYSQL_PASSWORD ?= wikipassword
MYSQL_DATABASE ?= jawiki

.PHONY: help setup up down restart ps logs download-dump clean dump-to-mysql mysql-to-es ingest-via-mysql create-jawiki-template connect-mysql

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
