import os
import json
import uuid
import asyncio
import aiofiles
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from logging  import Logger, getLogger
from dotenv   import load_dotenv
from openai   import OpenAI, AsyncOpenAI
import azure.functions as func
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.storage.blob.aio    import BlobServiceClient

from src import BasicClient
from src import MongoWrapper
from src import run_one_shot
from src.plugins.now_datetime     import NowDateTimePlugin
from src.plugins.web_search       import WebSearchPlugin
from src.plugins.web_summary      import WebSummaryPlugin
from src.plugins.fetch_url        import FetchUrl
# from src.plugins.code_interpreter import CodeInterpreterPlugin
from url_scraper import URLScraper


# .env ファイルを読み込む
load_dotenv()

# 環境変数の取得
AI_FOUNDRY_ENDPOINT       = os.environ.get("AI_FOUNDRY_ENDPOINT")
AI_FOUNDRY_API_KEY        = os.environ.get("AI_FOUNDRY_API_KEY")
AI_FOUNDRY_MODEL          = os.environ.get("AI_FOUNDRY_MODEL")
DATABRICKS_INSTANCE       = os.environ.get("DATABRICKS_INSTANCE")
DATABRICKS_TOKEN          = os.environ.get("DATABRICKS_TOKEN")
DATABRICKS_JOB_ID         = os.environ.get("DATABRICKS_JOB_ID")
LLM_MAX_TOKENS            = os.environ.get("LLM_MAX_TOKENS")
LLM_TEMPERATURE           = os.environ.get("LLM_TEMPERATURE")
LLM_TOP_P                 = os.environ.get("LLM_TOP_P")
# 現在はDataBaseまでは必要ない
# MONGODB_CONNECTION_STRING = os.environ.get("MONGODB_CONNECTION_STRING")
# DB_NAME                   = os.environ.get("DB_NAME")
# COLLECTION_NAME           = os.environ.get("COLLECTION_NAME")
STORAGE_CONNECTION_STRING = os.environ.get("AzureWebJobsStorage")
STORAGE_CONTAINER_NAME    = os.environ.get("STORAGE_CONTAINER_NAME")
STORE_BLOB_PATH           = os.environ.get("STORE_BLOB_PATH")

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
limits       = httpx.Limits(max_keepalive_connections=20, max_connections=300)
timeout      = httpx.Timeout(300.0, connect=5.0)
http_client  = httpx.AsyncClient(limits=limits, timeout=timeout)

# 関数アプリのセットアップ
app          = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
# LLMクライアントのセットアップ(Normal)
llmClient    = BasicClient(AI_FOUNDRY_MODEL, max_tokens=int(LLM_MAX_TOKENS), temperature=float(LLM_TEMPERATURE), top_p=float(LLM_TOP_P))
llmClient.configure(AI_FOUNDRY_ENDPOINT, AI_FOUNDRY_API_KEY, http_client)
# ツールのセットアップ
semaphore    = asyncio.Semaphore(10)
fetchUrl     = FetchUrl(2000, semaphore, http_client)
llmClient.register(NowDateTimePlugin())
llmClient.register(WebSummaryPlugin(2000, semaphore, http_client))
llmClient.register(WebSearchPlugin( 2000, semaphore, http_client))
llmClient.register(fetchUrl)
# MongoDBのセットアップ
# mongoStorage = MongoWrapper(MONGODB_CONNECTION_STRING, DB_NAME, COLLECTION_NAME)

async def generate_report(project:str, user_id:str, chat_id:str, promptFile:str, words:list|str):
    if isinstance(words, str):
        words = [words]
    
    system_msg = (
        "You are a helpful assistant.\n"
        "\n"
        "# Current Context\n"
        f"- **Current Date & Time** : {datetime.now(tz=ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d (%A) %H:%M:%S %Z')}\n"
        "- **Current Location**    : Asia/Tokyo\n"
        "\n"
        "# Critical Instruction: Knowledge Cutoff & Tool Usage\n"
        "Your internal training data is outdated and does NOT contain the latest real-time information.\n"
        "Be aware that your internal information is years old for your users.\n"
        "To avoid hallucinations and provide accurate answers, follow these rules:\n"
        "\n"
        "1. **Always Prioritize Tools** : If the user asks about current events, news, weather, or specific time-sensitive information, YOU MUST use the provided tools instead of relying on your internal knowledge.\n"
        "2. **Time Queries**            : For questions about current time in other cities, convert the location to an IANA Timezone ID(e.g., 'Asia/Tokyo', 'America/New_York')\n"
        "3. **Language**                : Respond in the language used by the user (primarily Japanese).\n"
        "\n"
    )
    async with aiofiles.open(f"src/prompt/{promptFile}", mode='r', encoding='utf-8') as f:
        user_msg = await f.read()
        user_msg = user_msg.replace("【WORD_LIST】", ", ".join(words))
    
    param_dict = {
            # 現在はDataBaseまでは必要ない
            # 'db'     : mongoStorage,
            'agent'  : llmClient,
            'sysmsg' : system_msg,
            'usrmsg' : user_msg,
        }
    reply_text = await run_one_shot(param_dict, user_id, chat_id)

    TARGET_PATH         = STORE_BLOB_PATH + f'{project}/user_id={user_id}/chat_id={chat_id}/{"".join(words)}_{str(uuid.uuid4())}.md'
    blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
    blob_client         = blob_service_client.get_blob_client(container=STORAGE_CONTAINER_NAME, blob=TARGET_PATH)
    await blob_client.upload_blob(
        reply_text, 
        overwrite=True, 
        max_concurrency=4
    )

    return TARGET_PATH

async def job_kick(task_analysis, project:str, lp_url:str):
    generate_path = await task_analysis
    # Databricks への JobKick
    api_url       = f"https://{DATABRICKS_INSTANCE.rstrip('/')}/api/2.1/jobs/run-now"
    headers       = {
                        "Authorization"  : f"Bearer {DATABRICKS_TOKEN}",
                        "Content-Type"   : "application/json"
                    }
    payload       = {
                        "job_id"         : DATABRICKS_JOB_ID,
                        "job_parameters" : {
                                                "PROJECT_NAME"     : project,
                                                "LANDING_PAGE_URL" : lp_url,
                                                "GENERATE_PATH"    : generate_path,
                                            }
                    }
    http_client.post(api_url, headers=headers, json=payload)



def get_llm_prompt(lp_string:dict):
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
    messages.append(UserMessage(content=lp_string))
    return messages

async def get_analysis_data(lp_url:str):
    # LPのWEBデータ取得
    lp_string       = await fetchUrl.execute(lp_url)
    # 商品シーン・ペルソナ・訴求シナリオを生成
    analysis_data   = await llmClient.complete(get_llm_prompt(lp_string))
    return analysis_data

@app.route(route="LPInsightGenerator")
async def LPInsightGenerator(req: func.HttpRequest) -> func.HttpResponse:
    logger = getLogger(__name__)
    logger.info('Python HTTP trigger function processed a request.')

    try:
        # 例
        # {"project": "shanai1", "mode": "light",  "lp_url" : "https://lp.br-lb.com/"}
        # {"project": "shanai2", "mode": "light",  "words"  : "水橋保寿堂製薬 (エマーキット)"}
        # {"project": "shanai3", "mode": "normal", "words"  : "水橋保寿堂製薬 (エマーキット)"}
        # {"project": "shanai4", "mode": "heavy",  "words"  : "水橋保寿堂製薬 (エマーキット)"}

        # リクエストをパースする
        req_body  = req.get_json()
        project   = req_body.get('project')      # 必須
        mode      = req_body.get('mode')         # 必須
        lp_url    = req_body.get('lp_url', None) # オプション
        words     = req_body.get('words',  None) # オプション

        # メモ：
        # deep research機能の付加の要請があった
        # この機能の実装には、複数回のAPIリクエストとWebサーチが必要となる
        # 基本的にはAPIリクエストやWebサーチは非同期的に実行し、同時に複数ユーザーからのリクエストも処理しなければならない
        # 現在は開発者一人のみが利用している状態であるため、不要であるが将来的な拡張性のために導入することとした
        # 
        # 将来的な拡張用
        user_id = str(uuid.uuid4())
        chat_id = str(uuid.uuid4())

        # 動作モードが普通or処理重視
        if mode in {"normal", "heavy"}:
            if isinstance(words, str):
                words = [words]
            
            # 解析対象単語について指定のある場合
            if len(words) != 0:
                task_analysis = asyncio.create_task(generate_report(project, user_id, chat_id, '商品分析.md', words))
            else:
                task_analysis = None
        else:
            task_analysis = None

        # Databricks への JobKick
        task_job      = asyncio.create_task(job_kick(task_analysis, project, lp_url))
        # LPのWEBデータをもとに商品シーンを生成
        task_scenario = asyncio.create_task(get_analysis_data(lp_url))
        # タスクの完了を待つ
        results       = await asyncio.gather(task_job, task_scenario)

        # 終了処理
        results[0].raise_for_status()
        response_payload = {
            "status"  : "success",
            "data"    : results[1],
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