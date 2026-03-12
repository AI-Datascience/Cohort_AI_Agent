import traceback
import asyncio
import json
from datetime import datetime, timedelta, timezone
from logging import Logger, getLogger
from langchain_azure_dynamic_sessions import SessionsPythonREPLTool
from azure.identity                   import DefaultAzureCredential
from azure.storage.blob               import BlobServiceClient, generate_container_sas, BlobSasPermissions
from azure.ai.inference.models        import ChatCompletionsToolDefinition, FunctionDefinition

from ._interface import AgentPlugin

class CodeInterpreterPlugin(AgentPlugin):
    def __init__(self, 
                 pool_endpoint:str, 
                 session_id:str, 
                 blob_service_client:BlobServiceClient, 
                 AZURE_ACCOUNT_NAME:str,
                 AZURE_CONTAINER_NAME:str,
                 logger:Logger=getLogger(__name__)):
        self.name                 = "CodeInterpreter"
        self.tool                 = SessionsPythonREPLTool(
                                        pool_management_endpoint=pool_endpoint, # コンテナプールエンドポイント
                                        session_id=session_id,                  # ユーザーIDや会話IDを指定
                                        credential=DefaultAzureCredential()     # Managed Identityを使用
                                    )
        self.blob_service_client  = blob_service_client
        self.AZURE_ACCOUNT_NAME   = AZURE_ACCOUNT_NAME
        self.AZURE_CONTAINER_NAME = AZURE_CONTAINER_NAME
        self.logger               = logger
    
    def get_name(self) -> str:
        return self.name

    def get_tools(self) -> ChatCompletionsToolDefinition:
        websearch_def = FunctionDefinition(
                            name=self.name,
                            description=(
                                "Pythonコードを実行して、計算、データ分析、グラフ作成、テキスト処理を行います。"
                                "【重要】この環境は「状態維持型（ステートフル）」です。以前の実行で定義した変数やインポートしたライブラリは、"
                                "同じ会話内であれば保持されます。そのため、大きな処理を複数のステップに分けて実行したり、"
                                "エラーが出た際に修正箇所だけを再実行したりすることが可能です。"
                                "複雑な計算や統計処理が必要な場合は、必ずこのツールを使用してください。"
                                ""
                            ),
                            parameters={
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type":        "string",
                                        "description": (
                                            "実行するPythonコード。出力は print() を使用してください。"
                                            "エラーが発生した場合は、エラーメッセージ（Traceback）が返されます。"
                                            "それを見てコードを修正し、再度実行してください。"
                                            ""
                                        ),
                                    }
                                },
                                "required": ["query"]
                            }
                        )
        return ChatCompletionsToolDefinition(function=websearch_def)

    async def execute(self, query:str) -> str:
        self.logger.info(f"{self.name}: 実行開始")
        self.logger.info(f"★Pythonコードを実行中:\n{query[:100]}...")

        now_datetime   = datetime.now(timezone.utc)
        delegation_key = self.blob_service_client.get_user_delegation_key(
                            key_start_time=now_datetime,
                            key_expiry_time=now_datetime + timedelta(hours=1)
                        )
        sas_token      = generate_container_sas(
                            account_name=self.AZURE_ACCOUNT_NAME,
                            container_name=self.AZURE_CONTAINER_NAME,
                            user_delegation_key=delegation_key,
                            permission=BlobSasPermissions(read=True, list=True),
                            expiry=now_datetime + timedelta(hours=1)
                        )

        query = (
            f"token = '{sas_token}'\n"
            f"acc   = '{self.AZURE_ACCOUNT_NAME}'\n"
            f"\n"
            f"{query}"
        )

        try:
            raw_response = self.tool.invoke(query)
            result       = json.loads(self.tool.invoke(query))
        except Exception as e:
            self.logger.error(f"JSONパース失敗: {raw_response}")
        
        return result