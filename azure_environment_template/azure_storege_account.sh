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
STORAGE_NAME="adinteagentstorage"                   # Azure Storage Accountの名前


# ---------------------------------------------------------
# 準備
# ---------------------------------------------------------
# 拡張機能の追加・更新
az extension add --name containerapp --upgrade --yes

# リソースプロバイダーの登録 (初回のみ必要、念のため実行)
az provider register --namespace Microsoft.App


# ---------------------------------------------------------
# Azure Storage Accountの作成 (既存RGに追加)
# ---------------------------------------------------------
az storage account create                   \
  --name                      $STORAGE_NAME \
  --resource-group            $RG_NAME      \
  --location                  $LOCATION     \
  --sku                       Standard_LRS  \
  --kind                      StorageV2     \
  --min-tls-version           TLS1_2        \
  --https-only                true          \
  --allow-blob-public-access  false         \
  --access-tier               Hot

