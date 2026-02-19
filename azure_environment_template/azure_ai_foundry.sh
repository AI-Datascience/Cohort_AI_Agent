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
AI_FOUNDRY_NAME="commom-agents-system"              # Azure AI Foundryの名前
AI_PROJECT_NAME="ai-agent-project"                  # Azure AI Foundry Projectの名前
STORAGE_NAME="adinteagentstorage"                   # Azure Storage Accountの名前


# ---------------------------------------------------------
# 準備
# ---------------------------------------------------------
# 拡張機能の追加・更新
az extension add --name containerapp --upgrade --yes

# リソースプロバイダーの登録 (初回のみ必要、念のため実行)
az provider register --namespace Microsoft.App

# AIサービス(Cognitive Services)を操作するための拡張機能のインストール
az extension add --name ml


# ---------------------------------------------------------
# Azure AI Foundryの作成 (既存RGに追加)
# ---------------------------------------------------------
az cognitiveservices account create  \
  --name            $AI_FOUNDRY_NAME \
  --resource-group  $RG_NAME         \
  --location        $LOCATION        \
  --kind            AIServices       \
  --sku             S0               \
  --yes


# ---------------------------------------------------------
# Azure AI Foundry Projectの作成 (既存RGに追加)
# ---------------------------------------------------------
# メモ：
# やり方がよくわからないため、WEBポータルから作成した方が早い
# 
# az ml workspace create                        \
#   --name                     $AI_PROJECT_NAME \
#   --resource-group           $RG_NAME         \
#   --location                 $LOCATION        \
#   --kind                     "hub"            \
#   --public-network-access    Enabled

