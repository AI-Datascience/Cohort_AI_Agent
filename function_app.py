import os
import json
import asyncio
import httpx
import numpy as np
from logging  import Logger, getLogger
from dotenv   import load_dotenv
from typing   import List, Dict
from openai   import OpenAI, AsyncOpenAI
import azure.functions as func
from azure.ai.inference.models import SystemMessage, UserMessage

from url_scraper      import URLScraper
from llm_agent        import LlmAgent
from code_interpreter import CodeInterpreter


# .env ファイルを読み込む
load_dotenv()

# 環境変数の取得
AI_FOUNDRY_ENDPOINT      = os.environ.get("AI_FOUNDRY_ENDPOINT")
AI_FOUNDRY_API_KEY       = os.environ.get("AI_FOUNDRY_API_KEY")
AI_FOUNDRY_MODEL         = os.environ.get("AI_FOUNDRY_MODEL")
DYNAMICSESSIONS_ENDPOINT = os.environ.get("DYNAMICSESSIONS_ENDPOINT")
LLM_MAX_TOKENS           = os.environ.get("LLM_MAX_TOKENS")
LLM_TEMPERATURE          = os.environ.get("LLM_TEMPERATURE")
LLM_TOP_P                = os.environ.get("LLM_TOP_P")

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
limits      = httpx.Limits(max_keepalive_connections=20, max_connections=100)
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
# Session Poolクライアントのセットアップ
poolClient  = CodeInterpreter(DYNAMICSESSIONS_ENDPOINT, http_client=http_client)

def get_llm_prompt(lp_dict:Dict):
    messages  = []
    messages.append(SystemMessage(content=(
                    "あなたは商品LPの分析を行うマーケティングの専門家です。\n"
                    "提供された商品情報を分析し、その商品が「最高に輝く具体的なシーン（適合）」と「全く役に立たない、あるいはミスマッチなシーン（不適合）」を洗い出してください。\n\n"
                    
                    "【重要な指示】\n"
                    "出力するキーワードは、ベクトル検索のクエリとして使用されます。\n"
                    "そのため、単一の一般名詞（例：「公園」「オフィス」）は **禁止** です。\n"
                    "必ず **「場所＋状況」** または **「属性＋場所」** の複合キーワード（Micro-Context）を選定してください。\n\n"
                    "抽出する単語は、単なる一般名詞ではなく、「誰が・どこで・何をしているか」がありありと想像できるような、具体的かつ解像度の高いキーワードを選定してください。\n"
                    "特に「場所」に関しては、大分類（例：公園）ではなく、詳細な施設タイプ（例：ドッグラン、親水広場）や、利用目的が明確なスポット名を優先してください。\n\n"
                    "LPに直接記載がなくても、商品の特性から論理的に推測されるシーンは積極的に広げて記述してください。\n\n"
                    "・NG例: 「ジム」「キャンプ」「サラリーマン」\n"
                    "・OK例: 「深夜の24時間ジム」「雨上がりのオートキャンプ場」「満員電車の通勤客」「コンセントのあるカフェ席」\n\n"

                    "【出力形式（厳守）】\n"
                    "回答は必ず以下のJSON形式のみを出力してください。\n"
                    "各単語をキーとし、その関連度の強さ（重み）を 0.0〜1.0 の数値で値として設定してください。\n"
                    "Markdown記法（```json 等）は含めず、生のJSONテキストのみを返してください。\n\n"
                    "・Positive/Negative 共に、確信度の高い上位5〜10個程度を抽出してください。\n\n"
                        
                    """
                    {
                        "positive": {"複合キーワードA": 0.89, "キーワードB": 0.70, ...},
                        "negative": {"複合キーワードC": 0.91, ...},
                    }
                    """
                    "\n\n"
                    
                    "【分析の視点】\n"
                    "--- positive（適合）：商品が必須となる、または魅力を最大化する文脈 ---\n"
                    "1. 具体的な施設・スポット（Places）：\n"
                    "   - 抽象的な「店」「屋外」はNG。\n"
                    "   - 「24時間ジム」「オートキャンプ場」「コワーキングスペース」など、行動が特定できる施設名。\n"
                    "2. 利用シーン・瞬間（Scenes）：\n"
                    "   - 「通勤ラッシュ」「運動後のシャワー」「子供の寝かしつけ」など、具体的なタイムラインや状況。\n"
                    "3. ターゲットの属性・状態（Traits）：\n"
                    "   - 「健康志向」のような広い言葉より、「糖質制限中」「リモートワーク疲れ」など具体的な状態。\n\n"
                    "4. 物理的適合 (Physical Fit):\n"
                    "   - 商品のサイズ、電源、耐久性が、その場所の設備・環境と完璧に噛み合うか。\n"
                    "   - マグネットでくっつく防水Bluetoothスピーカー  →  「ユニットバスの壁面」「雨の日のキャンプのタープ下」"
                    "5. 心理的・行動的適合 (Contextual Fit):\n"
                    "   - その場所にいる人の「特定の悩み」を解決するか。\n"
                    "   - 周囲の音を消すデジタル耳栓  →  「いびきが気になるカプセルホテル」「瞑想に集中したいヨガスタジオの隅」"
                    
                    "--- negative（不適合）：商品の機能が死ぬ、または邪魔になる文脈 ---\n"
                    "1. 阻害要因となる場所（Places）：\n"
                    "   - 商品のスペック（大きさ、音、電源有無など）的に使えない場所（例：図書館、満員電車）。\n"
                    "2. 無意味なシーン（Scenes）：\n"
                    "   - その商品をあえて使う必要がない状況。\n\n"
                    "3. 環境不適合 (Environmental Mismatch):\n"
                    "   - 商品を使うには狭すぎる、暗すぎる、うるさすぎる、静かすぎる場所。\n"
                    "4. マナー・ルール違反 (Social Mismatch):\n"
                    "   - その場所でその商品を使うことが「白い目」で見られる、あるいは禁止されている。\n"
                    
                    "【重み（スコア）の基準】\n"
                    "・1.0に近いほど：その傾向が非常に強い、確信度が高い\n"
                    "・0.0に近いほど：関連性が薄い\n"
                    "・1.0: 完全にその商品の独壇場である（または絶対に使用不可である）。\n"
                    "・0.8: 非常に相性が良い（または強い懸念がある）。\n"
                    "・0.5: 条件による（今回は出力対象外）。\n"
                    "・positiveの場合：適合度の高さ\n"
                    "・negativeの場合：不適合度の高さ（明確に避けるべき度合い）"
                )))
    messages.append(UserMessage(content=json.dumps(lp_dict, indent=4, ensure_ascii=False)))
    return messages

async def get_analysis_data(lp_url:str):
    # LPのWEBデータ取得
    lp_dict       = await url_scraper.fetch_web(lp_url)
    # 商品シーンを生成
    analysis_data = await llmClient.complete(get_llm_prompt(lp_dict))
    return analysis_data

async def execute_load_cohort(user_id:str, session_id:str):
    # 必要なファイルの読み込みプログラムを設定
    python_code = """
import io
import asyncio
import httpx
import numpy as np
import scipy as sp

# Storage AccountのSAS URL
target_sas_url   = "{COHORT_SAS_URL}"

# 対象ファイルの読み込み
limits  = httpx.Limits(max_keepalive_connections=3, max_connections=10)
timeout = httpx.Timeout(600.0, connect=5.0)
async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
    response = await client.get(target_sas_url)

# ステータスコードチェック
response.raise_for_status()

# cohort.npz のnumpyへの展開
def extract_cohort_npz(target_bytes):
    npz         = np.load(io.BytesIO(target_bytes), allow_pickle=True)
    np_cohort   = sp.sparse.csr_matrix((npz["data"], npz["indices"], npz["indptr"]), shape=tuple(npz["shape"]))
    np_adidlist = npz["adid_list"]
    np_codelist = npz["business_codelist"]
    return np_cohort, np_adidlist, np_codelist

np_cohort, np_adidlist, np_codelist = await asyncio.to_thread(extract_cohort_npz, response.content)
print("Successfully retrieved and extracted cohort file.")
"""
    # 計算用準備物の読み込み
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_load_caption(user_id:str, session_id:str):
    # 必要なファイルの読み込みプログラムを設定
    python_code = """
import io
import asyncio
import httpx
import numpy as np
import scipy as sp

# Storage AccountのSAS URL
target_sas_url   = "{CAPTION_SAS_URL}"

# 対象ファイルの読み込み
limits  = httpx.Limits(max_keepalive_connections=3, max_connections=10)
timeout = httpx.Timeout(600.0, connect=5.0)
async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
    response = await client.get(target_sas_url)

# ステータスコードチェック
response.raise_for_status()

# cohort_caption_matrix.npz のnumpyへの展開
def extract_caption_npz(target_bytes):
    npz              = np.load(io.BytesIO(target_bytes), allow_pickle=True)
    spots_matrix     = npz["data"]
    relational_spots = npz["business_placelist"]
    dict_code2name   = npz["dict_code2name"].item()
    return spots_matrix, relational_spots, dict_code2name

spots_matrix, relational_spots, dict_code2name = await asyncio.to_thread(extract_caption_npz, response.content)
print("Successfully retrieved and extracted caption file.")
"""
    # 計算用準備物の読み込み
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_load_caption(user_id:str, session_id:str):
    # 必要なファイルの読み込みプログラムを設定
    python_code = """
import io
import asyncio
import httpx
import numpy as np
import scipy as sp

# Storage AccountのSAS URL
target_sas_url   = "{CAPTION_SAS_URL}"

# 対象ファイルの読み込み
limits  = httpx.Limits(max_keepalive_connections=3, max_connections=10)
timeout = httpx.Timeout(600.0, connect=5.0)
async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
    response = await client.get(target_sas_url)

# ステータスコードチェック
response.raise_for_status()

# cohort_caption_matrix.npz のnumpyへの展開
def extract_caption_npz(target_bytes):
    npz              = np.load(io.BytesIO(target_bytes), allow_pickle=True)
    spots_matrix     = npz["data"]
    relational_spots = npz["business_placelist"]
    dict_code2name   = npz["dict_code2name"].item()
    return spots_matrix, relational_spots, dict_code2name

spots_matrix, relational_spots, dict_code2name = await asyncio.to_thread(extract_caption_npz, response.content)
print("Successfully retrieved and extracted caption file.")
"""
    # 計算用準備物の読み込み
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_generate_embeddings(user_id:str, session_id:str, gene_scene:List|Dict):
    items_positive   = list(gene_scene['positive'].items())
    items_negative   = list(gene_scene['negative'].items())
    lp_keywords      = [key for key, val in items_positive] + [ key for key, val in items_negative]

    # 商品シーンを埋め込みベクトルへ変換するプログラムを設定
    python_code = """
import asyncio
from sentence_transformers import SentenceTransformer

# 商品シーンを埋め込みベクトルへと変換
def convert_scene2embed():
    model     = SentenceTransformer('cl-nagoya/ruri-v3-130m')
    lp_matrix = model.encode({lp_keywords}, normalize_embeddings=True)  # キーワード数M × 512
    return lp_matrix

lp_matrix = await asyncio.to_thread(convert_scene2embed)
print("Successfully convert scene to embedding vector.")
"""
    # 商品シーン → 埋め込みベクトル
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_calculate_correlation(user_id:str, session_id:str, gene_scene:List|Dict):
    items_positive   = list(gene_scene['positive'].items())
    items_negative   = list(gene_scene['negative'].items())
    lp_weights       = [val for key, val in items_positive] + [-val for key, val in items_negative]

    total_weight     = np.sum(np.abs(lp_weights))
    lp_weights       = np.array(lp_weights) / total_weight
    lp_vector        = lp_weights.reshape(1, -1)            # 1 × キーワード数M

    # WEBデータ埋め込みベクトルとキャプション行列から相関ベクトルを計算するプログラムを設定
    python_code = """
import asyncio

# WEBデータ埋め込みベクトルとキャプション行列から相関ベクトルを計算
def calculate_correlation():
    lp_vector      = {lp_vector}
    lp_matrix      = await lp_matrix
    spots_matrix   = await spots_matrix
    lp_coefficient = lp_vector @ lp_matrix @ spots_matrix.T  # LPのスポット係数
    return lp_coefficient

lp_coefficient = await asyncio.to_thread(calculate_correlation)
print("Successfully calculate correlation vector.")
"""
    # 商品シーン → 埋め込みベクトル
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_calculate_score(user_id:str, session_id:str, gene_scene:List|Dict):
    items_positive   = list(gene_scene['positive'].items())
    items_negative   = list(gene_scene['negative'].items())
    lp_weights       = [val for key, val in items_positive] + [-val for key, val in items_negative]

    total_weight     = np.sum(np.abs(lp_weights))
    lp_weights       = np.array(lp_weights) / total_weight
    lp_vector        = lp_weights.reshape(1, -1)            # 1 × キーワード数M

    # WEBデータ埋め込みベクトルとキャプション行列から相関ベクトルを計算するプログラムを設定
    python_code = """
MAX_RECORDS      = 10000
CORRECT_MEAN     = np.mean(lp_coefficient)
CORRECT_STDDEV   = np.std( lp_coefficient)

# ADID毎のスコアを算出
np_scored        = (np_cohort @ lp_coefficient.T).flatten()
indices          = np.argsort(np_scored)[::-1][:MAX_RECORDS]
sorted_adidlist  = np_adidlist[indices]
sorted_spots     = relational_spots
sorted_targets   = np_cohort[indices, :]
sorted_scored    = np_scored[indices]

# 閾値以上のスポットを理由とする
REASON_THRESHOLD = 0.03
np_threshold     = lp_coefficient > REASON_THRESHOLD
np_reasons       = sorted_targets.multiply(np_threshold)
np_reasons.eliminate_zeros()

lp_coefficient = await asyncio.to_thread(calculate_correlation)
print("Successfully calculate correlation vector.")
"""
    # 商品シーン → 埋め込みベクトル
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_store_dataframe(user_id:str, session_id:str, gene_scene:List|Dict):
    items_positive   = list(gene_scene['positive'].items())
    items_negative   = list(gene_scene['negative'].items())
    lp_weights       = [val for key, val in items_positive] + [-val for key, val in items_negative]

    total_weight     = np.sum(np.abs(lp_weights))
    lp_weights       = np.array(lp_weights) / total_weight
    lp_vector        = lp_weights.reshape(1, -1)            # 1 × キーワード数M

    # WEBデータ埋め込みベクトルとキャプション行列から相関ベクトルを計算するプログラムを設定
    python_code = """
import asyncio

pldf_reasons     = pl.DataFrame({
    						'ADID'           : sorted_adidlist[np_reasons.row], 
                            'cohort_caption' : sorted_spots[np_reasons.col], 
                            'score'          : sorted_scored[np_reasons.row], 
                            'value'          : np_reasons.data
                        })\
						.filter(pl.col('value') > 0)\
                        .group_by(pl.col('ADID'), maintain_order=True)\
                        .agg(
                            pl.col('score').first(),
                            pl.col('cohort_caption').str.join(', ').alias('reasons')
                        )\
                        .select(['ADID', 'score', 'reasons'])

print("Successfully calculate correlation vector.")
"""
    # 商品シーン → 埋め込みベクトル
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

@app.route(route="LPInsightGenerator")
async def LPInsightGenerator(req: func.HttpRequest) -> func.HttpResponse:
    logger = getLogger(__name__)
    logger.info('Python HTTP trigger function processed a request.')

    try:
        # 例
        # {"user_id": "001", "lp_url": "https://lp.br-lb.com/"}

        # リクエストをパースする
        req_body  = req.get_json()
        user_id   = req_body.get('user_id', None)
        lp_url    = req_body.get('lp_url',  None)


        # LPのWEBデータをもとに商品シーンを生成
        task_gene_scene   = asyncio.create_task(get_analysis_data(logger, lp_url))
        # 計算用準備物の読み込み
        task_load_cohort  = asyncio.create_task(execute_load_cohort( user_id, 'defaultsession'))
        # 計算用準備物の読み込み
        task_load_caption = asyncio.create_task(execute_load_caption(user_id, 'defaultsession'))

        # 生成された商品シーンの取得・埋め込みベクトルへの変換
        gene_scene        = await task_gene_scene
        task_gene_emb     = asyncio.create_task(execute_generate_embeddings(user_id, 'defaultsession', gene_scene))

        # キャプション毎の相関ベクトルを計算
        task_calc_corr    = asyncio.create_task(execute_calculate_correlation(user_id, 'defaultsession', gene_scene))
        # ADID毎のスコアを計算
        task_calc_score   = asyncio.create_task(execute_calculate_score(user_id, 'defaultsession', gene_scene))
        # 計算結果の整形・保存
        task_store_frame  = asyncio.create_task(execute_store_dataframe(user_id, 'defaultsession', gene_scene))

        # 全てのタスクの完了を待つ
        results = await asyncio.gather(
                                task_load_cohort, task_load_caption, task_gene_emb, 
                                task_calc_corr,   task_calc_score,   task_store_frame
                            )
        return func.HttpResponse(f"Success", status_code=200)

    except Exception as e:
        logger.error(f"Error: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)