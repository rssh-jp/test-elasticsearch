# Kibana 集約クエリ（大中小分類 + カテゴリ階層）

## 前提
- Data View は `jawiki_full_current`（Alias）を使う。
- 現在の環境で Alias が未作成の場合は、先に `make reingest-full` を実行する。

## 1. 大分類（taxonomy_l1）の上位件数
Kibana Dev Tools で実行:

```json
GET jawiki_full_current/_search
{
  "size": 0,
  "aggs": {
    "top_major": {
      "terms": {
        "field": "taxonomy_l1",
        "size": 20
      }
    }
  }
}
```

## 2. 中分類（taxonomy_l2）の上位件数
```json
GET jawiki_full_current/_search
{
  "size": 0,
  "aggs": {
    "top_middle": {
      "terms": {
        "field": "taxonomy_l2",
        "size": 30
      }
    }
  }
}
```

## 3. 小分類（taxonomy_l3）の上位件数
```json
GET jawiki_full_current/_search
{
  "size": 0,
  "aggs": {
    "top_minor": {
      "terms": {
        "field": "taxonomy_l3",
        "size": 30
      }
    }
  }
}
```

## 4. 3階層ドリルダウン（大 -> 中 -> 小）
```json
GET jawiki_full_current/_search
{
  "size": 0,
  "aggs": {
    "major": {
      "terms": {
        "field": "taxonomy_l1",
        "size": 15
      },
      "aggs": {
        "middle": {
          "terms": {
            "field": "taxonomy_l2",
            "size": 10
          },
          "aggs": {
            "minor": {
              "terms": {
                "field": "taxonomy_l3",
                "size": 10
              }
            }
          }
        }
      }
    }
  }
}
```

## 5. 指定カテゴリ配下の文書数
例: `日本の都道府県`

```json
GET jawiki_full_current/_count
{
  "query": {
    "term": {
      "categories_all": "日本の都道府県"
    }
  }
}
```

## 6. 大分類ごとの代表記事（top_hits）
```json
GET jawiki_full_current/_search
{
  "size": 0,
  "aggs": {
    "by_major": {
      "terms": {
        "field": "taxonomy_l1",
        "size": 12
      },
      "aggs": {
        "sample_docs": {
          "top_hits": {
            "size": 3,
            "_source": ["page_id", "title", "url", "taxonomy_l1", "taxonomy_l2", "taxonomy_l3", "categories_direct"]
          }
        }
      }
    }
  }
}
```

## 7. 既存カテゴリ階層での詳細分析
既存の `categories_direct / level1 / level2` も併用できます。大分類で絞ってから詳細カテゴリを見ると扱いやすいです。

```json
GET jawiki_full_current/_search
{
  "size": 0,
  "query": {
    "term": {
      "taxonomy_l1": "人物"
    }
  },
  "aggs": {
    "direct_categories": {
      "terms": {
        "field": "categories_direct",
        "size": 30
      }
    }
  }
}
```

## 可視化のおすすめ
- Lens: Horizontal bar + terms(taxonomy_l1)
- Lens: Treemap + terms(taxonomy_l2)
- Data table: taxonomy_l1 / taxonomy_l2 / taxonomy_l3 の順で breakdown

## 運用メモ
- 再投入で Alias は新しい日時付きインデックスに切り替わる。
- Kibana 側は Data View を Alias にしておけば、可視化を作り直さずに追従できる。
