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
ENV_NAME="custom-interpreter-env"                   # Container Apps 環境の名前
POOL_NAME="custom-interpreter-pool"                 # セッションプールの名前

# コンテナ設定
IMAGE_NAME="ghcr.io/leggings8942/self-piston:v1.2"  # Dockerイメージの場所
TARGET_PORT=8000                                    # Listenポート

# レジストリ認証 (ghcr.io用)
# REGISTRY_SERVER="ghcr.io"
# REGISTRY_USER="leggings8942"                        # GitHub ID
# REGISTRY_PASS="ghp_xxxxxxxxxxxx"                    # GitHub PAT (read:packages権限必須)


# ---------------------------------------------------------
# 準備
# ---------------------------------------------------------
# 拡張機能の追加・更新
az extension add --name containerapp --upgrade --yes

# リソースプロバイダーの登録 (初回のみ必要、念のため実行)
az provider register --namespace Microsoft.App


# ---------------------------------------------------------
# Container Apps 環境の作成
# ---------------------------------------------------------
az containerapp env create   \
  --name           $ENV_NAME \
  --resource-group $RG_NAME  \
  --location       $LOCATION \
  --enable-workload-profiles


# ---------------------------------------------------------
# セッションプールの作成 (既存RGに追加)
# ---------------------------------------------------------
# セッションプールの作成
az containerapp sessionpool create    \
  --name              $POOL_NAME      \
  --resource-group    $RG_NAME        \
  --location          $LOCATION       \
  --environment       $ENV_NAME       \
  --container-type    CustomContainer \
  --image             $IMAGE_NAME     \
  --target-port       $TARGET_PORT    \
  --max-sessions      30              \
  --ready-sessions    0               \
  --cooldown-period   300             \
  --network-status    EgressEnabled   \
  --cpu               2.0             \
  --memory            4.0Gi


# 管理エンドポイントを表示
az containerapp sessionpool show                       \
  --name           $POOL_NAME                          \
  --resource-group $RG_NAME                            \
  --query          "properties.poolManagementEndpoint" \
  --output         tsv

