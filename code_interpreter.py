import re
import httpx
from azure.identity     import AzureCliCredential, get_bearer_token_provider
from azure.identity.aio import DefaultAzureCredential


class CodeInterpreter:
    def __init__(self, endpoint:str, http_client:httpx.AsyncClient|None=None):
        self.endpoint    = endpoint
        self.http_client = http_client
        self.credential  = DefaultAzureCredential()

        # httpxクライアントが未初期化の場合
        if self.http_client is None:
            limits           = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            timeout          = httpx.Timeout(60.0, connect=5.0)
            self.http_client = httpx.AsyncClient(limits=limits, timeout=timeout)
        
        return

    async def execute_dynamic_session(self, user_id:str, session_id:str, code:str, timeout:int=60):
        # Azure認証トークンの取得
        token = await self.credential.get_token("https://dynamicsessions.io/.default")

        # ============================================================================
        # session pool へ JOB をキューイングする
        # ============================================================================

        # URLの構築
        # Azure Session Pool は以下の形式でリクエストをコンテナに転送する
        # {管理エンドポイント}/{コンテナ内のパス}?identifier={USER_ID}
        # SelfPistonコンテナは /code/execute で待ち受けている
        target_url = f"{self.endpoint}/code/execute"
        headers    = {
                        "Authorization" : f"Bearer {token.token}",
                        "Content-Type"  : "application/json"
                    }
        params     = {
                        "identifier"  : re.sub(r'[^a-zA-Z0-9]', '', f"{user_id}{session_id}").lower(),
                        "api-version" : "2024-02-02-preview",
                    }
        payload    = {
                        "properties": {
                            "codeInputType"    : "inline",
                            "executionType"    : "synchronous",
                            "timeoutInSeconds" : timeout,
                            "code"             : code,
                        }
                    }

        response = await self.http_client.post(target_url, headers=headers, params=params, json=payload)
        response.raise_for_status() # HTTP 4xx, 5xx エラーなら例外を発生させる
        return response.text


