import time
import json
import asyncio
from logging import Logger, getLogger

import httpx
import trafilatura
from ddgs import DDGS

from ._interface import AgentPlugin

class WebSummaryPlugin(AgentPlugin):
    def __init__(self, 
                 MAX_CHARS_PER_SITE:int, 
                 semaphore:asyncio.Semaphore=asyncio.Semaphore(10), 
                 http_client:httpx.AsyncClient|None=None,
                 logger:Logger=getLogger(__name__)
                ):
        self.name               = "WebSummary"
        self.MAX_CHARS_PER_SITE = MAX_CHARS_PER_SITE
        self.semaphore          = semaphore
        self.http_client        = http_client
        self.logger             = logger

        # httpxクライアントが未初期化の場合
        if self.http_client is None:
            limits           = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            timeout          = httpx.Timeout(10.0, connect=5.0)
            self.http_client = httpx.AsyncClient(limits=limits, timeout=timeout)
        
        return
    
    def get_name(self) -> str:
        return self.name

    def get_tools(self) -> dict:
        tool_def = {
            "type"     : "function",
            "function" : {
                            "name": self.name,
                            "description": (
                                "Web上の情報を検索し、Webページの本文を**「構造化されたMarkdown形式」**で取得するツールです。\n"
                                "検索結果から最も信頼性の高いサイトを選定し、広告やサイドバーなどのノイズを強力に除去（Trafilaturaを使用）します。\n"
                                "文章の構造（見出し、リスト、強調）を維持した状態でテキストを抽出するため、要約や要点整理に最適です。\n"
                                "\n"
                                "## このツールの役割と使い分け:\n"
                                "このツールは**「概要の把握」と「事実の確認」**を優先する場合に使用してください。\n"
                                "生データを大量に読む必要がなく、結論や定義を素早く知りたい場合に適しています。\n"
                                "（逆に、細かいニュアンスや複数の意見を比較したい場合は、WebSearchPluginを使用してください）\n"
                                "\n"
                                "## 具体的な使用タイミング:\n"
                                "- ユーザーが「要約して」「概要を教えて」「～とは？」と質問した場合。\n"
                                "- 特定のニュースや出来事の「結論」や「結果」だけを素早く知りたい場合。\n"
                                "- 商品のスペック、映画のあらすじ、言葉の定義など、事実関係が明確な情報を探す場合。\n"
                                "\n"
                                "## 取得データの仕様:\n"
                                "- 検索ヒットおよびスクレイピングに成功した**上位10サイト**のデータをリスト形式で返却します。\n"
                                "- 読みやすさを重視し、Markdown形式（# 見出し, - 箇条書き）に整形されたテキストを返します。\n"
                                "- リンクや画像URLは除去され、テキスト情報に特化しています。\n"
                                "- 回答生成時には、情報の偏りを防ぐため可能な限り複数のサイトを参照し、引用元URLを明記してください。\n"
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type":        "string",
                                        "description": (
                                            "検索エンジン(Brave)のアルゴリズムに最適化された検索キーワード。\n"
                                            "ユーザーの質問を、**「要約・解説記事」**がヒットしやすい単語の組み合わせに変換してください。\n"
                                            "\n"
                                            "### 重要: クエリ構築の戦略\n"
                                            "1. **ターゲットの明確化**: 議論やフォーラムではなく、「Wiki」「ニュース記事」「解説ブログ」がヒットするように誘導する。\n"
                                            "2. **5W1Hの要素分解とキーワード変換**:\n"
                                            "   質問の意図を5W1H1Rで分解し、以下の対応表を参考に具体的なキーワードへ変換してください。\n"
                                            "   - **Why (なぜ)**　　　→ '理由' '背景' 'わかりやすく解説'\n"
                                            "   - **How (どうやって)**→ '使い方' 'チュートリアル' '入門' '手順'\n"
                                            "   - **What (なに)**　　 → 'とは' '意味' '概要' 'Wikipedia' 'スペック'\n"
                                            "   - **Who (だれ)**　　　→ '経歴' 'プロフィール' '人物像'\n"
                                            "   - **Where (どこ)**　　→ '場所' '環境' '設定箇所' '国/地域'\n"
                                            "   - **When (いつ)**　　 → 以下の「時間表現の具体化」を参照\n"
                                            "   - **Result (結果)** 　→ '試合結果' '判決' '結末' 'ネタバレ'\n"
                                            "3. **検索意図の補完**: ユーザーが求めている情報の形式を表す単語を追加する。\n"
                                            "   - 方法を知りたい → '手順' 'チュートリアル' '入門'\n"
                                            "   - 理由を知りたい → '原因' 'メカニズム' '背景'\n"
                                            "   - 比較したい　　 → '比較' '違い' 'メリット デメリット'\n"
                                            "   - 正確さが重要　 → '公式' 'ドキュメント' '統計' 'データ'\n"
                                            "4. **時間表現の具体化（重要）**:\n"
                                            "   - 「最新」「最近」「今年」といった相対的な表現は、検索ノイズになります。\n"
                                            "   - **必ず「現在日時」を確認し、具体的な「西暦（数字）」に変換して含めてください。**\n"
                                            "   - 例: 現在が2026年なら、'最新' → '2026' または '2025' と変換する。\n"
                                            ""
                                            "### 変換例 (User -> Search Query):\n"
                                            "- User:'量子コンピュータって結局なに？' (What)\n"
                                            "  -> Query:'量子コンピュータ とは 仕組み わかりやすく 解説'\n"
                                            "- User:'昨日の侍ジャパンの試合結果は？' (Result)\n"
                                            "  -> Query:'侍ジャパン 試合結果 速報 スコア [現在の西暦]'\n"
                                            "- User:'Pythonの作者は？' (Who)\n"
                                            "  -> Query:'Python 作者 Guido van Rossum 経歴 プロフィール'\n"
                                            "- User:'NISAの始め方を教えて' (How)\n"
                                            "  -> Query:'NISA 始め方 手順 初心者 入門 [現在の西暦]'\n"
                                            "\n"
                                        ),
                                    }
                                },
                                "required": ["query"]
                            }
                    }
        }
        return tool_def

    async def execute(self, query:str) -> str:
        self.logger.info(f"{self.name}: 実行開始")
        self.logger.info(f"★検索を実行中: {query}")
        self.logger.info(f"Braveへの検索開始")
        time_start = time.perf_counter()

        # メモ：
        # brave検索エンジンを利用していたところ、ボットとみなされアクセスエラー(No results found.)が発生するようになった
        # これに対する対策として、try-except構文の利用と極短期間のウェイトを設けることとした
        # 
        def _get_url_list():
            results = []
            with DDGS() as ddgs:
                for page in [1, 2]:
                    try:
                        tmp = ddgs.text(query=query, region='jp-jp', safesearch='moderate', timelimit=None, backend='brave', page=page, max_results=20)
                        results.extend(tmp)
                        self.logger.info(f"Page {page} 取得成功: {len(tmp)}件")
                        time.sleep(0.5)
                    except Exception as e:
                        self.logger.warning(f"Page {page} の取得に失敗しました (backend='brave'): {e}")
                        continue

            return results
        loop    = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, _get_url_list)
        self.logger.info(f"Braveへの検索完了: {time.perf_counter() - time_start}")
        self.logger.info(f"URL取得件数: {len(results)}")
        
        async def _fetch_web(webinfo):
            try:
                async with self.semaphore:
                    # ユーザーエージェントの設定
                    headers  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
                    response = await self.http_client.get(webinfo, headers=headers)
                
                    # ステータスコードチェック
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise httpx.HTTPStatusError(f"Error: Status code {response.status_code}")
                
                    # HTML以外はスキップ
                    content_type = response.headers.get('Content-Type', '').lower()
                    if 'text/html' not in content_type:
                        self.logger.info(f"URL: {webinfo["href"]} is not 'text/html'")
                        return None

                loop       = asyncio.get_running_loop()
                clean_text = await loop.run_in_executor(
                                None, # Noneを指定するとデフォルトのThreadPoolExecutorが使われる
                                lambda: trafilatura.extract(
                                    response.content,
                                    url=webinfo["href"],      # WEBサイトのURL
                                    include_formatting=True,  # Markdown形式有効化
                                    include_links=False,      # URL除去
                                    include_images=False,     # 画像除去
                                    include_comments=False,   # コメント欄除去
                                    include_tables=True,      # タグ情報は残す
                                    with_metadata=False,      # 本文先頭にメタデータを付加しない
                                    deduplicate=True,         # 重複する文章除去
                                    favor_precision=True,     # 精度優先
                                    no_fallback=True,         # フォールバック無効化
                                    output_format='markdown'  # 出力形式
                                )
                            )
                
                if not clean_text:
                    return None
                
                # 文字数制限の適用
                if len(clean_text) > self.MAX_CHARS_PER_SITE:
                    clean_text = clean_text[:self.MAX_CHARS_PER_SITE] + "...\n(以下省略)"
                
                res_dict = {
                    "title": webinfo["title"],
                    "url":   webinfo["href"],
                    "body":  clean_text,
                }
            
            except Exception as e:
                self.logger.warning(f"Warning: {e}")
                return None
            
            return res_dict

        
        self.logger.info(f"取得したURLへのスクレイピング・要約作業開始")
        time_start = time.perf_counter()
        tasks      = [_fetch_web(webinfo) for webinfo in results]
        res_list   = await asyncio.gather(*tasks)
        res_dict   = [elem for elem in res_list if elem is not None]
        self.logger.info(f"取得したURLへのスクレイピング・要約作業完了: {time.perf_counter() - time_start}")
        
        if not res_dict:
            return "Error: 検索結果から有効な本文を取得できませんでした。"
        else:
            self.logger.info(f"有効な取得サイト数: {len(res_dict)}")

        return json.dumps(res_dict[:30], ensure_ascii=False)