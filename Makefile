SHELL := /bin/bash
COMPOSE := docker compose
SERVICES := elasticsearch kibana esrs-server esrs-worker esrs-server-mcp
PYTHON := python3
DUMP_PATH ?= /home/araumi/prj/github/test-elasticsearch/data/dumps/jawiki-latest-pages-articles-multistream.xml.bz2
CUSTOM_DATA_PATH ?= /home/araumi/prj/github/test-elasticsearch/data/custom/custom_docs.jsonl

.PHONY: help up down restart ps logs ingest ingest-full ingest-custom download-dump reingest-full bootstrap clean

help:
	@echo "Targets:"
	@echo "  make up         - Elasticsearch + Relevance Studio を起動"
	@echo "  make ingest     - 日本語Wikipediaのデータを Elasticsearch に投入"
	@echo "  make download-dump - 日本語Wikipediaダンプをダウンロード"
	@echo "  make ingest-full   - 日本語Wikipediaダンプを全量投入"
	@echo "  make ingest-custom - 独自JSONLデータを追加投入"
	@echo "  make reingest-full - 新しい日時付きインデックスへ全量再投入 + 独自データ追加"
	@echo "  make bootstrap  - 起動してから Wikipedia データを投入"
	@echo "  Kibana URL      - http://localhost:5601"
	@echo "  make logs       - 主要サービスのログを表示"
	@echo "  make ps         - コンテナ状態を表示"
	@echo "  make down       - 停止"
	@echo "  make clean      - 停止してボリュームを削除"

up:
	$(COMPOSE) up -d $(SERVICES)

down:
	$(COMPOSE) down

restart: down up

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200 $(SERVICES)

ingest:
	$(COMPOSE) --profile tools run --rm wiki-loader

download-dump:
	mkdir -p /home/araumi/prj/github/test-elasticsearch/data/dumps
	WIKI_DUMP_PATH=$(DUMP_PATH) $(PYTHON) scripts/load_jawiki_dump.py --download-only

ingest-full:
	ES_URL=$${ES_URL:-http://localhost:9200} \
	ES_USER=$${LOADER_ES_USER:-elastic} \
	ES_PASSWORD=$${LOADER_ES_PASSWORD:-changeme} \
	WIKI_FULL_INDEX_PREFIX=$${WIKI_FULL_INDEX_PREFIX:-jawiki_full} \
	WIKI_FULL_ALIAS_NAME=$${WIKI_FULL_ALIAS_NAME:-jawiki_full_current} \
	WIKI_DUMP_PATH=$(DUMP_PATH) \
	$(PYTHON) scripts/load_jawiki_dump.py

ingest-custom:
	ES_URL=$${ES_URL:-http://localhost:9200} \
	ES_USER=$${LOADER_ES_USER:-elastic} \
	ES_PASSWORD=$${LOADER_ES_PASSWORD:-changeme} \
	WIKI_FULL_ALIAS_NAME=$${WIKI_FULL_ALIAS_NAME:-jawiki_full_current} \
	WIKI_FULL_INDEX_NAME=$${WIKI_FULL_INDEX_NAME:-jawiki_full} \
	CUSTOM_DATA_PATH=$(CUSTOM_DATA_PATH) \
	$(PYTHON) scripts/load_custom_docs.py

reingest-full: ingest-full ingest-custom

bootstrap: up ingest

clean:
	$(COMPOSE) down -v --remove-orphans
