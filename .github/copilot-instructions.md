# Project Guidelines

## コードスタイル
- 既存の実装方針を優先し、Python スクリプトはシンプルな標準ライブラリ中心で書く。
- 変更は必要最小限にし、関係ないリファクタや命名変更は行わない。
- データ投入スクリプト変更時は、環境変数名・インデックス名・Alias 名の既定値を維持する。

## アーキテクチャ
- このリポジトリは Docker Compose で Elasticsearch + Kibana + Relevance Studio を起動し、Python スクリプトで jawiki と独自 JSONL を投入する構成。
- 全量投入は日時付き実インデックスを作成し、Alias（既定: jawiki_current）を切り替える運用。
- 主要境界:
  - 実行基盤: docker-compose.yml
  - 運用コマンド: Makefile
  - 取り込みロジック: scripts/
  - 説明資料: docs/

## ビルドとテスト
- まずは Makefile ターゲットを使う。
- 主要コマンド:
  - `make up`: サービス起動
  - `make download-dump`: jawiki ダンプ取得
  - `make dump-to-mysql`: ダンプ → MySQL 投入
  - `make mysql-to-es`: MySQL → ES 投入（推論エンドポイント初期化含む）
  - `make ingest-via-mysql`: dump-to-mysql + mysql-to-es を順に実行
  - `make down`: 停止
- このリポジトリには自動テスト基盤がないため、変更検証は対象コマンドの実行とログ確認を優先する。

## プロジェクト固有の規約・注意点
- Kibana 接続は kibana_system 前提。compose の es-setup を迂回しない。
- MediaWiki API を使う処理では User-Agent 必須（403 回避）。
- バルク投入ロジック変更時は、既存マッピング互換（categories_* / taxonomy_* 含む）を壊さない。
- 旧インデックスは比較・ロールバック用途で残す前提。削除処理は明示依頼がない限り追加しない。

## 参照ドキュメント
- 全体セットアップとコマンド: README.md
- データ構造・再投入フロー・分類方針: docs/README.md
- Kibana 集約クエリ例: docs/kibana-category-queries.md
