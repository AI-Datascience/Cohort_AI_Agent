import time
import json
import asyncio
from logging import Logger, getLogger

import httpx
from ddgs           import DDGS
from w3lib.encoding import html_to_unicode
from readability    import Document
from bs4            import BeautifulSoup

from ._interface import AgentPlugin

class WebSearchPlugin(AgentPlugin):
    def __init__(self, 
                 MAX_CHARS_PER_SITE:int, 
                 semaphore:asyncio.Semaphore=asyncio.Semaphore(10), 
                 http_client:httpx.AsyncClient|None=None,
                 logger:Logger=getLogger(__name__)
                ):
        self.name               = "WebSearch"
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
                                "Web上の情報を検索し、**検索結果上位10件のWebサイト**からコンテンツを「原文に近い状態」で一括取得するツールです。\n"
                                "Braveで検索を行い、関連性の高い複数のサイトからテキストデータを取得します。\n"
                                "最低限のスクリプト除去等は行いますが、要約処理は行わず、長文のテキストデータをそのまま返します。\n"
                                "\n"
                                "## このツールの役割と使い分け:\n"
                                "このツールは**「情報の網羅性」と「詳細な文脈」**を優先する場合に使用してください。\n"
                                "単一のサイトではなく**最大10件の情報源**を同時に参照するため、多角的な視点での調査が可能です。\n"
                                "（逆に、単に概要や結論だけを知りたい場合は、別の要約ツール(WebSummaryPlugin)を使用してください）\n"
                                "\n"
                                "## 具体的な使用タイミング:\n"
                                "- ユーザーが「詳細に」「詳しく」「徹底的に」調べてほしいと要求した場合。\n"
                                "- ニュースのヘッドラインだけでなく、記事内の細かい発言や数値データ、経緯を知りたい場合。\n"
                                "- 専門的な技術仕様や、法律の条文など、要約すると意味が変わる恐れがある情報を扱う場合。\n"
                                "- 複数の情報源から、微妙なニュアンスの違いを比較検討したい場合。\n"
                                "\n"
                                "## 取得データの仕様:\n"
                                "- 検索ヒットおよびスクレイピングに成功した**上位10サイト**のデータをリスト形式で返却します。\n"
                                "- 返却されるテキスト量は非常に多くなる可能性があります。\n"
                                "- 回答生成時には、情報の偏りを防ぐため可能な限り複数のサイトを参照し、引用元URLを明記してください。\n"
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type":        "string",
                                        "description": (
                                            "検索エンジン(Brave)のアルゴリズムに最適化された検索キーワード。\n"
                                            "ユーザーの質問をそのまま入力するのではなく、以下の戦略に基づいて「最もヒット率が高い単語の組み合わせ」に変換してください。\n"
                                            "\n"
                                            "### 重要: クエリ構築の戦略\n"
                                            "1. **助詞・疑問詞の排除**: 「～は？」「～のやり方」などの自然言語は捨て、名詞と動詞のみにする。\n"
                                            "2. **5W1Hの要素分解（検索意図の具体化）**:\n"
                                            "   質問の意図を5W1Hで分解し、以下の対応表を参考に具体的なキーワードへ変換してください。\n"
                                            "   - **Why (なぜ)**　　　→ '原因' '理由' '背景' 'メカニズム' '動機'\n"
                                            "   - **How (どうやって)**→ '手順' '方法' 'やり方' '解決策' 'チュートリアル' '実装'\n"
                                            "   - **What (なに)**　　 → 'とは' '概要' '仕様' '定義' '特徴'\n"
                                            "   - **Who (だれ)**　　　→ '人物' '開発元' '組織' '経歴' 'プロフィール'\n"
                                            "   - **Where (どこ)**　　→ '場所' '環境' '設定箇所' '国/地域'\n"
                                            "   - **When (いつ)**　　 → 以下の「時間表現の具体化」を参照\n"
                                            "3. **検索意図の補完**: ユーザーが求めている情報の形式を表す単語を追加する。\n"
                                            "   - 方法を知りたい → '手順' 'チュートリアル' '入門'\n"
                                            "   - 理由を知りたい → '原因' 'メカニズム' '背景'\n"
                                            "   - 比較したい　　 → '比較' '違い' 'メリット デメリット'\n"
                                            "   - 正確さが重要　 → '公式' 'ドキュメント' '統計' 'データ'\n"
                                            "4. **時間表現の具体化（重要）**:\n"
                                            "   - 「最新」「最近」「今年」といった相対的な表現は、検索ノイズになります。\n"
                                            "   - **必ず「現在日時」を確認し、具体的な「西暦（数字）」に変換して含めてください。**\n"
                                            "   - 例: 現在が2026年なら、'最新' → '2026' または '2025' と変換する。\n"
                                            "5. **同義語の活用**: 専門用語や、より一般的な言い回しをスペース区切りで並列させる。\n"
                                            ""
                                            "### 変換例 (User -> Search Query):\n"
                                            "- User:'現在の総理大臣は誰？'\n"
                                            "  -> Query:'日本 内閣総理大臣 [現在の西暦]'\n"
                                            "  -> Query:'日本 内閣総理大臣 現在 一覧 経歴'\n"
                                            "- User:'Pythonでスクレイピングしたいんだけど、おすすめのライブラリある？' \n"
                                            "  -> Query:'Python ライブラリ スクレイピング おすすめ 比較 人気 [現在の西暦]'\n"
                                            "  -> Query:'Python Webスクレイピング ライブラリ おすすめ 比較 [現在の西暦]'\n"
                                            "- User:'最近の円安の原因を詳しく知りたい'\n"
                                            "  -> Query:'円安 要因 分析 [現在の西暦]'\n"
                                            "  -> Query:'円安 原因 分析 専門家 解説 メカニズム [現在の西暦]'\n"
                                            "- User:'Dockerが起動しない時の対処法'\n"
                                            "  -> Query:'Docker 起動しない エラー トラブルシューティング ログ 確認方法'\n"
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
                
                def _get_decode_string():
                    # 文字コード特定およびデコード
                    detected_encoding, html_text = html_to_unicode(
                        content_type_header=response.headers.get('content-type'),
                        html_body_str=response.content
                    )

                    # ノイズ除去(本文抽出)
                    readable_doc     = Document(html_text)
                    readable_title   = readable_doc.title()
                    readable_summary = readable_doc.summary()

                    if not readable_summary:
                        return readable_title, None
                
                    # 除去するタグ一覧
                    remove_tags = [
                        # --- スクリプト・スタイル・メタ ---
                        "script", "style", "noscript", "link", "meta",

                        # --- ページ構造（本文以外） ---
                        "header", "footer", "nav", "aside",

                        # --- インタラクティブ・フォーム ---
                        "form", "input", "textarea", "select", "option", "button", "label",
                        "details", "summary",

                        # --- メディア・埋め込み・図表 ---
                        "iframe", "embed", "object", "param",          # 外部埋め込み
                        "video", "audio", "source", "track",           # 動画・音声
                        "canvas", "svg", "map", "area",                # 描画・マップ
                        "figure", "figcaption",                        # 図表とキャプション

                        # --- その他ノイズになりやすいもの ---
                        "dialog"                                       # ポップアップ/モーダル
                    ]
                    soup = BeautifulSoup(readable_summary, 'html.parser')
                    for tag in soup(remove_tags):
                        tag.extract()

                    allowed_tags = {
                        # --- 見出し ---
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',

                        # --- 文章ブロック ---
                        'p', 'br', 'hr', 'div',  # divはunwrap対象にしてもいいが、段落代わりのサイトもあるのでpと同列に扱う手もある

                        # --- リスト（定義リストを追加！） ---
                        'ul', 'ol', 'li', 
                        'dl', 'dt', 'dd',        # ★重要: 用語と定義のペア

                        # --- 表組み ---
                        'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',

                        # --- 引用・コード ---
                        'blockquote', 'pre', 'code',

                        # --- インライン要素（意味を変えるもの） ---
                        'b', 'strong', 'i', 'em', 
                        'sub', 'sup',            # 上付き・下付き（化学式や注釈用）
                        'del', 'ins'             # 訂正線（情報の更新前後がわかるように）
                    }
                    for tag in list(soup.find_all(True)):
                        tag.attrs = {}
                        if tag.name not in allowed_tags:
                            tag.unwrap()
                    
                    text       = str(soup)
                    lines      = [line.strip() for line in text.splitlines() if line.strip()]
                    clean_text = "\n".join(lines)

                    return readable_title, clean_text
                
                loop        = asyncio.get_running_loop()
                title, body = await loop.run_in_executor(
                                        None, # Noneを指定するとデフォルトのThreadPoolExecutorが使われる
                                        _get_decode_string
                                    )

                # WEBサイトから文字列が取得できなかった場合
                if not body:
                    return None

                if len(body) > self.MAX_CHARS_PER_SITE:
                    body = body[:self.MAX_CHARS_PER_SITE] + "..." # 省略記号をつける

                res_dict = {
                    "title": title if title else webinfo["title"],
                    "url":   webinfo["href"],
                    "body":  body,
                }
            
            except Exception as e:
                self.logger.warning(f"Warning: {e}")
                return None
            
            return res_dict

        
        self.logger.info(f"取得したURLへのスクレイピング開始")
        time_start = time.perf_counter()
        tasks      = [_fetch_web(webinfo) for webinfo in results]
        res_list   = await asyncio.gather(*tasks)
        res_dict   = [elem for elem in res_list if elem is not None]
        self.logger.info(f"取得したURLへのスクレイピング完了: {time.perf_counter() - time_start}")
        
        if not res_dict:
            return "Error: 検索結果から有効な本文を取得できませんでした。"
        else:
            self.logger.info(f"有効な取得サイト数: {len(res_dict)}")

        return json.dumps(res_dict[:10], ensure_ascii=False)