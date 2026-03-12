#!/usr/bin/env bash


# メモ：
# 何度も削除と再構築を繰り返した結果として、そもそもasureポータルで作成すること自体をやめることとした
# 何度やってみてもうまく動作しないためである
# ローカルMAC環境から遠隔で、リソースの生成を行うこととする

# ---------------------------------------------------------
# 各種変数設定
# ---------------------------------------------------------
# 基本設定
RG_NAME="project_aiagent_c9x347ide0cgk3w5"          # 既存のリソースグループ名
LOCATION="japaneast"                                # リージョン (例: eastasia, japaneast)
STORAGE_NAME="adinte-agent-memory"                  # Azure Cosmos DBのアカウント名
DATABASE_NAME="AI_AGENT_SYSTEM"                     # Azure Cosmos DBのデータベース名
COLLECTION_NAME="CHAT_LOGS"                         # Azure Cosmos DBのコレクション名

# ---------------------------------------------------------
# Azure Cosmos DBの作成 (既存RGに追加)
# ---------------------------------------------------------
# cosmosdb のアカウント作成
az cosmosdb create                             \
  --name                      $STORAGE_NAME    \
  --resource-group            $RG_NAME         \
  --locations                 regionName=$LOCATION failoverPriority=0 isZoneRedundant=False \
  --kind                      MongoDB          \
  --server-version            7.0              \
  --capabilities              EnableServerless \
  --public-network-access     ENABLED          \
  --backup-policy-type        Periodic         \
  --backup-redundancy         Local

# database の作成
az cosmosdb mongodb database create            \
  --resource-group            $RG_NAME         \
  --account-name              $STORAGE_NAME    \
  --name                      $DATABASE_NAME

# collection の作成
az cosmosdb mongodb collection create          \
  --resource-group            $RG_NAME         \
  --account-name              $STORAGE_NAME    \
  --database-name             $DATABASE_NAME   \
  --name                      $COLLECTION_NAME \
  --shard                     "partition_key"
