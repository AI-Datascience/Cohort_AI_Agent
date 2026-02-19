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
FUNCTION_NAME="ai-agent-system"                     # セッションプールの名前


# ---------------------------------------------------------
# 準備
# ---------------------------------------------------------
# 拡張機能の追加・更新
az extension add --name containerapp --upgrade --yes

# リソースプロバイダーの登録 (初回のみ必要、念のため実行)
az provider register --namespace Microsoft.App


# ---------------------------------------------------------
# Azure Funcsion Appの作成 (既存RGに追加)
# ---------------------------------------------------------
az functionapp create \
  --name                       $FUNCTION_NAME \
  --resource-group             $RG_NAME       \
  --consumption-plan-location  $LOCATION      \
  --os-type                    linux          \
  --runtime                    python         \
  --runtime-version            3.12           \
  --functions-version          4              \
  --storage-account            $STORAGE_NAME  \
  --assign-identity            "[system]"

