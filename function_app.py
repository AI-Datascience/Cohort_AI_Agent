import os
import json
import asyncio
import httpx
from logging  import Logger, getLogger
from dotenv   import load_dotenv
from typing   import List, Dict
from openai   import OpenAI, AsyncOpenAI
import azure.functions as func
from azure.ai.inference.models import SystemMessage, UserMessage

from url_scraper      import URLScraper
from llm_agent        import LlmAgent


# .env ファイルを読み込む
load_dotenv()

# 環境変数の取得
AI_FOUNDRY_ENDPOINT       = os.environ.get("AI_FOUNDRY_ENDPOINT")
AI_FOUNDRY_API_KEY        = os.environ.get("AI_FOUNDRY_API_KEY")
AI_FOUNDRY_MODEL          = os.environ.get("AI_FOUNDRY_MODEL")
DYNAMICSESSIONS_ENDPOINT  = os.environ.get("DYNAMICSESSIONS_ENDPOINT")
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
STORAGE_CONTAINER_NAME    = os.environ.get("STORAGE_CONTAINER_NAME")
COHORT_NPZ_SAS_URL        = os.environ.get("COHORT_NPZ_SAS_URL")
DATABRICKS_INSTANCE       = os.environ.get("DATABRICKS_INSTANCE")
DATABRICKS_TOKEN          = os.environ.get("DATABRICKS_TOKEN")
DATABRICKS_JOB_ID         = os.environ.get("DATABRICKS_JOB_ID")
LLM_MAX_TOKENS            = os.environ.get("LLM_MAX_TOKENS")
LLM_TEMPERATURE           = os.environ.get("LLM_TEMPERATURE")
LLM_TOP_P                 = os.environ.get("LLM_TOP_P")
MAX_ADID_NUMBER           = os.environ.get("MAX_ADID_NUMBER")

# メモ：
# ユーザーからの要求ごとにhttpxクライアントを作成すると「SNATポート枯渇」が発生する
# 具体的な理由は以下の通り
# 1. Azureの仕様上、一つのインスタンスに割り当て可能なSNATポート数に上限がある
# 2. TCPの仕様上、通信を切断しても数分間はポートが待機状態（TIME_WAIT）になり再利用できない
# 3. このため、大量のアクセスがあるとSNATポートが枯渇してしまい、通信不能になる可能性がある
# 
# 対策として、関数アプリ(@app.route)の外側で共有のhttpxクライアントを作成する
# 関数アプリ全体としてこのクライアントを利用することで、SNATポートの枯渇を防ぐ
# また同時接続数やタイムアウトを明示的に設定することで、相手サーバーやAzureリソースのパンクを防ぐ
# 
limits      = httpx.Limits(max_keepalive_connections=20, max_connections=300)
timeout     = httpx.Timeout(300.0, connect=5.0)
http_client = httpx.AsyncClient(limits=limits, timeout=timeout)

# 関数アプリのセットアップ
app         = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
# URLスクレイパーのセットアップ
url_scraper = URLScraper(semaphore=asyncio.Semaphore(10), http_client=http_client)
# LLMクライアントのセットアップ
openai_cli  = AsyncOpenAI(
                    base_url=AI_FOUNDRY_ENDPOINT,
                    api_key=AI_FOUNDRY_API_KEY,
                    http_client=http_client,
                    max_retries=3
                )
llmClient   = LlmAgent(openai_cli, AI_FOUNDRY_MODEL, int(LLM_MAX_TOKENS), float(LLM_TEMPERATURE), float(LLM_TOP_P))

def get_llm_prompt(lp_dict:Dict):
    messages  = []
    messages.append(SystemMessage(content=(
                    "あなたは商品LPやWebデータの分析を行うマーケティングの専門家であり、凄腕のセールスライター兼クリエイティブディレクターです。\n"
                    "提供された商品情報を分析し、以下の【タスク1】と【タスク2】を同時に実行し、指定されたJSON形式で出力してください。\n\n"

                    "【タスク1：商品シーンとペルソナの抽出】\n"
                    "商品が「最高に輝く具体的なシーン（Positive）」と「全く役に立たない、あるいはミスマッチなシーン（Negative）」、および「そのシーンにいる具体的なペルソナ」を洗い出してください。\n"
                    "・抽出するキーワードはベクトル検索のクエリとして使用するため、単一の一般名詞（例：「公園」「オフィス」）は厳禁です。\n"
                    "・必ず「場所＋状況」または「属性＋場所」の複合キーワード（Micro-Context）を選定してください（例：「深夜の24時間ジム」「雨上がりのオートキャンプ場」）。\n"
                    "・Positive/Negative 共に、確信度の高い上位3〜5個程度を抽出してください。スコアは1.0〜0.0で設定してください。\n\n"

                    "【タスク2：訴求シナリオの生成】\n"
                    "タスク1で抽出した「Positiveの上位3つのシーン」と「ペルソナ」のデータを元に、ターゲットの心を強烈に動かす『訴求シナリオ』をMarkdown形式で作成してください。\n"
                    "・構成：1. メインコピー、2. 共感のシナリオ、3. 解決とベネフィット、4. アクションへの誘導\n"
                    "・Negative（不適合）として抽出されたシーンについては、シナリオの最後に『※こんな方にはおすすめしません』という項目で短くまとめてください。\n\n"

                    "【出力形式（厳守）】\n"
                    "回答は必ず以下のJSON形式のみを出力してください。Markdown記法（```json 等）は含めず、生のJSONテキストのみを返してください。\n"
                    "※ `appeal_scenario_markdown` の値には、タスク2で作成したMarkdownテキストを文字列として格納してください（改行は \\n を使用）。\n\n"

                    """
                    {
                        "analysis_data": {
                            "positive": [
                                {
                                    "keyword": "複合キーワード（例：雨の日のタープ下）",
                                    "score": 0.95,
                                    "persona": "具体的なペルソナ",
                                    "reason": "適合する論理的な理由"
                                }
                            ],
                            "negative": [
                                {
                                    "keyword": "複合キーワード",
                                    "score": 0.90,
                                    "persona": "具体的なペルソナ",
                                    "reason": "不適合・邪魔になる理由"
                                }
                            ]
                        },
                        "appeal_scenario_markdown": "### シーン1：〇〇\\n**ターゲット層**: 〇〇\\n\\n1. メインコピー...\\n\\n※こんな方にはおすすめしません..."
                    }
                    """
                )))
    messages.append(UserMessage(content=json.dumps(lp_dict, indent=4, ensure_ascii=False)))
    return messages

async def get_analysis_data(lp_url:str):
    # LPのWEBデータ取得
    lp_dict         = await url_scraper.fetch_web(lp_url)
    # 商品シーン・ペルソナ・訴求シナリオを生成
    analysis_data   = await llmClient.complete(get_llm_prompt(lp_dict))
    return analysis_data

@app.route(route="LPInsightGenerator")
async def LPInsightGenerator(req: func.HttpRequest) -> func.HttpResponse:
    logger = getLogger(__name__)
    logger.info('Python HTTP trigger function processed a request.')

    try:
        # 例
        # {"lp_url": "https://lp.br-lb.com/"}

        # リクエストをパースする
        req_body  = req.get_json()
        lp_url    = req_body.get('lp_url',  None)

        # Databricks への JobKick
        api_url = f"https://{DATABRICKS_INSTANCE.rstrip('/')}/api/2.1/jobs/run-now"
        headers = {
            "Authorization"  : f"Bearer {DATABRICKS_TOKEN}",
            "Content-Type"   : "application/json"
        }
        payload = {
            "job_id"         : DATABRICKS_JOB_ID,
            "job_parameters" : {"LANDING_PAGE_URL": lp_url}
        }
        response = await http_client.post(api_url, headers=headers, json=payload)
        response.raise_for_status()


        # LPのWEBデータをもとに商品シーンを生成
        result = await get_analysis_data(lp_url)

        response_payload = {
            "status"  : "success",
            "data"    : result,
        }
        response_body = json.dumps(response_payload, ensure_ascii=False)

        # 正常終了
        return func.HttpResponse(
            body=response_body,
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logger.error(f"Error: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)