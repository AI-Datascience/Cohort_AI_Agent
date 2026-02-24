import os
import json
import requests
from dotenv             import load_dotenv
from pprint             import pprint
from azure.identity     import DefaultAzureCredential
# from azure.identity.aio import DefaultAzureCredential

# .env ファイルを読み込む
load_dotenv()

# 環境変数の取得
DYNAMICSESSIONS_ENDPOINT = os.environ.get("DYNAMICSESSIONS_ENDPOINT")

# Azure認証トークンの取得
credential = DefaultAzureCredential()
token      = credential.get_token("https://dynamicsessions.io/.default")


target_url = f"{DYNAMICSESSIONS_ENDPOINT}/code/execute"
headers    = {
                "Authorization" : f"Bearer {token.token}",
                "Content-Type"  : "application/json"
            }
params     = {
                "identifier"  : "ajgieovnidwo",
                "api-version" : "2024-02-02-preview",
            }
payload    = {
                "properties": {
                    "codeInputType": "inline",
                    "executionType": "synchronous",
                    "code": "print('Hello, world!')"
                }
            }

response = requests.post(target_url, headers=headers, params=params, json=payload)
response.raise_for_status()
pprint(json.loads(response.text))