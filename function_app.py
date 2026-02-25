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
AI_FOUNDRY_ENDPOINT       = os.environ.get("AI_FOUNDRY_ENDPOINT")
AI_FOUNDRY_API_KEY        = os.environ.get("AI_FOUNDRY_API_KEY")
AI_FOUNDRY_MODEL          = os.environ.get("AI_FOUNDRY_MODEL")
DYNAMICSESSIONS_ENDPOINT  = os.environ.get("DYNAMICSESSIONS_ENDPOINT")
STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
STORAGE_CONTAINER_NAME    = os.environ.get("STORAGE_CONTAINER_NAME")
COHORT_NPZ_SAS_URL        = os.environ.get("COHORT_NPZ_SAS_URL")
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

async def execute_session_pool_setup(user_id:str, session_id:str):
    # 必須ライブラリのインストール
    python_code = """
%pip install                                                \
        numpy sentencepiece protobuf azure-storage-blob     \
        readability-lxml      w3lib               httpx     \
        beautifulsoup4        azure-ai-inference  packaging \
        python-dotenv         openai              polars    \
        sentence-transformers tiktoken            fastembed



import sys
import types
import importlib

importlib.invalidate_caches()

v_path = "transformers.utils.versions"
v_mod  = types.ModuleType(v_path)
v_mod.require_version      = lambda *a, **k: None
v_mod.require_version_core = lambda *a, **k: None
sys.modules[v_path] = v_mod
"""
    # 必須ライブラリのインストール
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)
    return result

async def execute_load_cohort(user_id:str, session_id:str):
    # 必要なファイルの読み込みプログラムを設定
    python_code = f"""
import io
import gc
import asyncio
import aiofiles
import httpx
import numpy as np
import scipy as sp

# Storage AccountのSAS URL
target_sas_url = "{COHORT_NPZ_SAS_URL}"

# httpxの基本設定
limits  = httpx.Limits(max_keepalive_connections=3, max_connections=10)
timeout = httpx.Timeout(600.0, connect=5.0)

# cohort.npz の numpy への読み込み・展開
async def extract_cohort_npz():
    # メモ：
    # Azure Dynamic Sessionsでは /mnt/data/ を一時領域として提供している
    temp_file_path = "/mnt/data/temp_cohort.npz"

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        # GETリクエストを「ストリームモード」で開始
        async with client.stream("GET", target_sas_url) as response:
            # ステータスコードチェック
            response.raise_for_status()
        
            # メモリに溜めず、100MB ずつ直接ファイルへ書き出す
            # (100MB = 100 * 1024 * 1024 bytes)
            async with aiofiles.open(temp_file_path, 'wb') as f:
                async for chunk in response.aiter_bytes(chunk_size=100 * 1024 * 1024):
                    await f.write(chunk)
    
    return True

async def get_cohort(task_npz):
    await task_npz
    temp_file_path = "/mnt/data/temp_cohort.npz"
    with np.load(temp_file_path, allow_pickle=True) as npz:
        np_cohort = sp.sparse.csr_matrix((npz["data"], npz["indices"], npz["indptr"]), shape=tuple(npz["shape"]))

    return np_cohort

async def get_adidlist(task_npz):
    await task_npz
    temp_file_path = "/mnt/data/temp_cohort.npz"
    with np.load(temp_file_path, allow_pickle=True) as npz:
        np_adidlist = npz["adid_list"]

    return np_adidlist

# パフォーマンスのため、平行処理の途中で処理を完了させる
npz_cohort  = asyncio.create_task(extract_cohort_npz())
np_cohort   = asyncio.create_task(get_cohort(npz_cohort))
np_adidlist = asyncio.create_task(get_adidlist(npz_cohort))

print("Successfully retrieved and extracted cohort file.")
"""
    # 計算用準備物の読み込み
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)

    # For Debug
    # npz_cohort  = await poolClient.execute_dynamic_session(user_id, session_id, "await npz_cohort",  timeout=600)
    # np_cohort   = await poolClient.execute_dynamic_session(user_id, session_id, "await np_cohort",   timeout=600)
    # np_adidlist = await poolClient.execute_dynamic_session(user_id, session_id, "await np_adidlist", timeout=600)

    return result

async def execute_load_caption(user_id:str, session_id:str):
    # 必要なファイルの読み込みプログラムを設定
    python_code = f"""
import io
import asyncio
import aiofiles
import httpx
import numpy as np
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient

# 環境変数の設定
STORAGE_CONNECTION_STRING = "{STORAGE_CONNECTION_STRING}"
STORAGE_CONTAINER_NAME    = "{STORAGE_CONTAINER_NAME}"
STORAGE_BLOB_PATH         = "cohort_caption_matrix.npz"

# cohort_caption_matrix.npz の numpy への読み込み・展開
async def extract_caption_npz():
    # メモ：
    # Azure Dynamic Sessionsでは /mnt/data/ を一時領域として提供している
    temp_file_path = "/mnt/data/temp_caption.npz"

    # blobクライアントによる非同期io
    async with AsyncBlobServiceClient.from_connection_string(
        STORAGE_CONNECTION_STRING,
        max_single_get_size=100 * 1024 * 1024,
        max_chunk_get_size=100 * 1024 * 1024
    ) as blob_service_client:
        blob_client = blob_service_client.get_blob_client(container=STORAGE_CONTAINER_NAME, blob=STORAGE_BLOB_PATH)
        stream      = await blob_client.download_blob()

        async with aiofiles.open(temp_file_path, 'wb') as f:
            async for chunk in stream.chunks():
                await f.write(chunk)

    return True

async def get_spots_matrix(task_npz):
    await task_npz
    temp_file_path = "/mnt/data/temp_caption.npz"
    with np.load(temp_file_path, allow_pickle=True) as npz:
        spots_matrix = npz["data"]

    return spots_matrix

async def get_relational_spots(task_npz):
    await task_npz
    temp_file_path = "/mnt/data/temp_caption.npz"
    with np.load(temp_file_path, allow_pickle=True) as npz:
        relational_spots = npz["business_placelist"]
    
    return relational_spots

# パフォーマンスのため、平行処理の途中で処理を完了させる
npz_caption      = asyncio.create_task(extract_caption_npz())
spots_matrix     = asyncio.create_task(get_spots_matrix(npz_caption))
relational_spots = asyncio.create_task(get_relational_spots(npz_caption))

print("Successfully retrieved and extracted caption file.")
"""
    # 計算用準備物の読み込み
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)

    # For Debug
    # npz_caption      = await poolClient.execute_dynamic_session(user_id, session_id, "await npz_caption",      timeout=600)
    # spots_matrix     = await poolClient.execute_dynamic_session(user_id, session_id, "await spots_matrix",     timeout=600)
    # relational_spots = await poolClient.execute_dynamic_session(user_id, session_id, "await relational_spots", timeout=600)

    return result

async def execute_generate_embeddings(user_id:str, session_id:str, gene_scene:List|Dict):
    items_positive   = list(gene_scene['positive'].items())
    items_negative   = list(gene_scene['negative'].items())
    lp_keywords      = [key for key, val in items_positive] + [ key for key, val in items_negative]
    json_keywards    = json.dumps(lp_keywords, ensure_ascii=False, separators=(',', ':'))

    # 商品シーンを埋め込みベクトルへ変換するプログラムを設定
    python_code = f"""
import gc
import asyncio
from sentence_transformers import SentenceTransformer

# 商品シーンを埋め込みベクトルへと変換
def convert_scene2embed():
    model     = SentenceTransformer('cl-nagoya/ruri-v3-130m')
    lp_matrix = model.encode({json_keywards}, normalize_embeddings=True)  # キーワード数M × 512

    # メモリ管理
    del model
    gc.collect()
    return lp_matrix

# パフォーマンスのため、平行処理の途中で処理を完了させる
lp_matrix = asyncio.create_task(asyncio.to_thread(convert_scene2embed))

print("Successfully convert scene to embedding vector.")
"""
    # 商品シーン to 埋め込みベクトル
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)

    # For Debug
    # lp_matrix = await poolClient.execute_dynamic_session(user_id, session_id, "await lp_matrix", timeout=600)

    return result

async def execute_calculate_correlation(user_id:str, session_id:str, gene_scene:List|Dict):
    items_positive   = list(gene_scene['positive'].items())
    items_negative   = list(gene_scene['negative'].items())
    lp_weights       = [val for key, val in items_positive] + [-val for key, val in items_negative]

    total_weight     = np.sum(np.abs(lp_weights))
    lp_weights       = np.array(lp_weights) / total_weight
    lp_vector_list   = lp_weights.tolist()                 # キーワード数M のリスト

    # WEBデータ埋め込みベクトルとキャプション行列から相関ベクトルを計算するプログラムを設定
    python_code = f"""
import gc
import asyncio
import numpy as np

# WEBデータ埋め込みベクトルとキャプション行列から相関ベクトルを計算
async def calculate_correlation():
    lp_vector          = np.array({lp_vector_list}).reshape(1, -1)
    tmp_lp_matrix      = await lp_matrix
    tmp_spots_matrix   = await spots_matrix

    # await非対応な処理部分のラッパー
    def _tmp_calc(vec_lp, mat_lp, mat_spots):
        return vec_lp @ mat_lp @ mat_spots.T

    # LPのスポット係数を算出
    lp_coefficient     = await asyncio.to_thread(_tmp_calc, lp_vector, tmp_lp_matrix, tmp_spots_matrix)

    # メモリ管理
    del lp_vector, tmp_lp_matrix, tmp_spots_matrix
    gc.collect()
    return lp_coefficient

# パフォーマンスのため、平行処理の途中で処理を完了させる
lp_coefficient = asyncio.create_task(calculate_correlation())

print("Successfully calculate correlation vector.")
"""
    # 商品シーン → 埋め込みベクトル
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)

    # For Debug
    # lp_coefficient = await poolClient.execute_dynamic_session(user_id, session_id, "await lp_coefficient", timeout=600)

    return result

async def execute_calculate_score(user_id:str, session_id:str):
    # 相関ベクトルとコホート行列からADID毎のスコアを算出するプログラムを設定
    python_code = f"""
import gc
import asyncio
import numpy as np

# 相関ベクトルとコホート行列からADID毎のスコアを算出
async def calculate_score():
    MAX_RECORDS          = {MAX_ADID_NUMBER}
    tmp_lp_coefficient   = await lp_coefficient
    tmp_np_cohort        = await np_cohort

    # await非対応な処理部分のラッパー
    def _tmp_calc(mat_cohort, vec_lp):
        np_scored = (mat_cohort @ vec_lp).flatten()
        indices   = np.argsort(np_scored)[::-1][:MAX_RECORDS]
        return np_scored, indices

    # ADID毎のスコアを算出
    np_scored, indices = await asyncio.to_thread(_tmp_calc, tmp_np_cohort, tmp_lp_coefficient.T)

    # メモリ管理
    del tmp_lp_coefficient, tmp_np_cohort
    gc.collect()
    return np_scored, indices

async def get_sorted_adidlist(task_adid_score):
    np_scored, indices = await task_adid_score
    tmp_np_adidlist    = await np_adidlist
    sorted_adidlist    = tmp_np_adidlist[indices]

    # メモリ管理
    del np_scored, indices, tmp_np_adidlist
    gc.collect()
    return sorted_adidlist

async def get_sorted_spots(task_adid_score):
    sorted_spots       = await relational_spots
    return sorted_spots

async def get_sorted_targets(task_adid_score):
    np_scored, indices = await task_adid_score
    tmp_np_cohort      = await np_cohort
    sorted_targets     = tmp_np_cohort[indices, :]

    # メモリ管理
    del np_scored, indices, tmp_np_cohort
    gc.collect()
    return sorted_targets

async def get_sorted_scored(task_adid_score):
    np_scored, indices = await task_adid_score
    sorted_scored      = np_scored[indices]

    # メモリ管理
    del np_scored, indices
    gc.collect()
    return sorted_scored

# パフォーマンスのため、平行処理の途中で処理を完了させる
adid_score       = asyncio.create_task(calculate_score())
sorted_adidlist  = asyncio.create_task(get_sorted_adidlist(adid_score))
sorted_spots     = asyncio.create_task(get_sorted_spots(adid_score))
sorted_targets   = asyncio.create_task(get_sorted_targets(adid_score))
sorted_scored    = asyncio.create_task(get_sorted_scored(adid_score))

print("Successfully calculate adid score.")
"""
    # 相関ベクトルとコホート行列からADID毎のスコアを算出
    result = await poolClient.execute_dynamic_session(user_id, session_id, python_code, timeout=600)

    # For Debug
    # adid_score      = await poolClient.execute_dynamic_session(user_id, session_id, "await adid_score",      timeout=600)
    # sorted_adidlist = await poolClient.execute_dynamic_session(user_id, session_id, "await sorted_adidlist", timeout=600)
    # sorted_spots    = await poolClient.execute_dynamic_session(user_id, session_id, "await sorted_spots",    timeout=600)
    # sorted_targets  = await poolClient.execute_dynamic_session(user_id, session_id, "await sorted_targets",  timeout=600)
    # sorted_scored   = await poolClient.execute_dynamic_session(user_id, session_id, "await sorted_scored",   timeout=600)

    return result

async def execute_store_dataframe(user_id:str, session_id:str):
    # メモ：
    # これまでパフォーマンス向上のため、平行処理の途中で処理を完了させてきた
    # しかし、ここでは主にデータの整形・保存が主目的となっている
    # そのためこのブロックでは、平行処理を全て受け止めて処理の完了を保証していることに注意すること

    # 理由となるスポットの抽出・データ整形を行うプログラムを設定
    python_code = f"""
import io
import gc
import asyncio
import numpy  as np
import polars as pl
from azure.storage.blob import BlobServiceClient

# 環境変数の設定
STORAGE_CONNECTION_STRING = "{STORAGE_CONNECTION_STRING}"
STORAGE_CONTAINER_NAME    = "{STORAGE_CONTAINER_NAME}"
STORE_BLOB_PATH           = "calc_result/result_reasons.parquet"

# 閾値以上のスポットを理由とする
REASON_THRESHOLD = 0.04

# 平行タスクを作らずに受け止める
result               = await asyncio.gather(lp_coefficient, sorted_targets, sorted_adidlist, sorted_spots, sorted_scored)
buff_lp_coefficient  = result[0]
buff_sorted_targets  = result[1]
buff_sorted_adidlist = result[2]
buff_sorted_spots    = result[3]
buff_sorted_scored   = result[4]

# メモリ管理
del npz_cohort,    np_cohort,       np_adidlist
del npz_caption,   spots_matrix,    relational_spots
del lp_matrix
del lp_coefficient
del adid_score,    sorted_adidlist, sorted_spots,    sorted_targets, sorted_scored
gc.collect()

# スポットの抽出
np_threshold     = buff_lp_coefficient > REASON_THRESHOLD
np_reasons       = buff_sorted_targets.multiply(np_threshold)
np_reasons.eliminate_zeros()

# メモリ管理
del buff_lp_coefficient, buff_sorted_targets
del np_threshold
gc.collect()

# 遅延評価モードでフレームワークを作成
pldf_reasons     = pl.DataFrame({{
    						'ADID'           : buff_sorted_adidlist[np_reasons.row], 
                            'cohort_caption' : buff_sorted_spots[   np_reasons.col], 
                            'score'          : buff_sorted_scored[  np_reasons.row], 
                            'value'          : np_reasons.data
                        }})\
                        .lazy()

# メモリ管理
del buff_sorted_adidlist, buff_sorted_spots, buff_sorted_scored, np_reasons
gc.collect()

# 実行計画の作成と実行
pldf_reasons     = pldf_reasons\
                        .cast({{
                            'ADID'           : pl.String,
                            'cohort_caption' : pl.String,
                            'score'          : pl.Float32,
                            'value'          : pl.Float32,
                        }})\
						.filter(pl.col('value') > 0)\
                        .group_by(pl.col('ADID'), maintain_order=True)\
                        .agg(
                            pl.col('score').first(),
                            pl.col('cohort_caption').str.join(', ').alias('reasons')
                        )\
                        .select(['ADID', 'score', 'reasons'])\
                        .collect()\
                        .rechunk()

# 一時バッファに計算結果を流し込む
tmp_buffer = io.BytesIO()
pldf_reasons.write_parquet(tmp_buffer, compression="snappy")
tmp_buffer.seek(0)

blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
blob_client         = blob_service_client.get_blob_client(container=STORAGE_CONTAINER_NAME, blob=STORE_BLOB_PATH)
blob_client.upload_blob(
    tmp_buffer, 
    overwrite=True, 
    max_concurrency=4,
    length=tmp_buffer.getbuffer().nbytes
)

print("Successfully store polars dataframe.")
"""
    # 理由となるスポットの抽出・データ整形
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
        task_gene_scene   = asyncio.create_task(get_analysis_data(lp_url))
        # 依存ライブラリのインストール
        await execute_session_pool_setup(user_id, 'defaultsession')
        # 計算用準備物の読み込み
        task_load_cohort  = asyncio.create_task(execute_load_cohort( user_id, 'defaultsession'))
        # 計算用準備物の読み込み
        task_load_caption = asyncio.create_task(execute_load_caption(user_id, 'defaultsession'))

        # 生成された商品シーンの取得・埋め込みベクトルへの変換
        gene_scene        = await task_gene_scene
        task_gene_emb     = asyncio.create_task(execute_generate_embeddings(user_id, 'defaultsession', gene_scene))

        # キャプション毎の相関ベクトルを計算
        await asyncio.gather(task_gene_emb, task_load_caption)
        task_calc_corr    = asyncio.create_task(execute_calculate_correlation(user_id, 'defaultsession', gene_scene))

        # ADID毎のスコアを計算
        await asyncio.gather(task_calc_corr, task_load_cohort)
        task_calc_score   = asyncio.create_task(execute_calculate_score(user_id, 'defaultsession'))

        # 計算結果の整形・保存
        await task_calc_score
        task_store_frame  = asyncio.create_task(execute_store_dataframe(user_id, 'defaultsession'))

        # 正常終了
        await task_store_frame
        return func.HttpResponse(f"Success", status_code=200)

    except Exception as e:
        logger.error(f"Error: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)