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

class FetchUrl(AgentPlugin):
    def __init__(self, 
                 MAX_CHARS_PER_SITE:int, 
                 semaphore:asyncio.Semaphore=asyncio.Semaphore(10), 
                 http_client:httpx.AsyncClient|None=None,
                 logger:Logger=getLogger(__name__)
                ):
        self.name               = "FetchUrl"
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
                                "指定された特定のURLに直接アクセスし、そのWebページの本文（テキストデータ）をスクレイピングして取得するツールです。\n"
                                "検索エンジンを経由せず、指定されたURLのコンテンツのみを正確に抽出します。\n"
                                "\n"
                                "## このツールの役割と使い分け:\n"
                                "このツールは**「特定のWebページの記載内容を正確に把握したい場合」**に使用してください。\n"
                                "ユーザーから直接URLが提示された場合や、直前のやり取りで登場したURLの詳細な中身を読みたい場合に最適です。\n"
                                "（逆に、キーワードから未知の情報を探す場合はWeb検索ツールを使用してください）\n"
                                "\n"
                                "## 具体的な使用タイミング:\n"
                                "- ユーザーが「このURL（リンク）の記事を要約して」「このサイトに何が書かれているか教えて」と要求した場合。\n"
                                "- ユーザーが特定の公式ドキュメント、企業HP、ニュース記事などのURLを直接指定した場合。\n"
                                "- 他のツール（検索結果など）で得た特定のURLに対して、さらに詳細な本文を確認・検証する必要がある場合。\n"
                                "\n"
                                "## 注意事項:\n"
                                "- 取得に失敗した場合は、存在しないURLか、アクセス制限（ログイン必須など）がかかっている可能性があります。\n"
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": (
                                            "スクレイピング対象の完全なURL（例: 'https://ja.wikipedia.org/wiki/Python'）。\n"
                                            "検索キーワードではなく、必ず 'http://' または 'https://' から始まる有効なURL形式で指定してください。"
                                        ),
                                    }
                                },
                            },
                            "required": ["query"]
                    }
        }
        return tool_def

    async def execute(self, query:str) -> str:
        self.logger.info(f"{self.name}: 実行開始")
        self.logger.info(f"★検索を実行中: {query}")
        self.logger.info(f"httpxでの取得開始")
        
        async def _fetch_web(webinfo):
            try:
                async with self.semaphore:
                    # ユーザーエージェントの設定
                    headers  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
                    response = await self.http_client.get(webinfo, headers=headers)
                
                    # ステータスコードチェック
                    response.raise_for_status()
                    if response.status_code != 200:
                        self.logger.error(f"Error: Status code {response.status_code}")
                        raise httpx.HTTPStatusError(f"Error: Status code {response.status_code}")
                
                    # HTML以外はスキップ
                    content_type = response.headers.get('Content-Type', '').lower()
                    if 'text/html' not in content_type:
                        self.logger.info(f"URL: {webinfo} is not 'text/html'")
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
                    "title": title,
                    "url":   webinfo,
                    "body":  body,
                }
            
            except Exception as e:
                self.logger.warning(f"Warning: {e}")
                return None
            
            return res_dict

        
        self.logger.info(f"取得したURLへのスクレイピング開始")
        time_start = time.perf_counter()
        res_dict   = await _fetch_web(query)
        self.logger.info(f"取得したURLへのスクレイピング完了: {time.perf_counter() - time_start}")
        
        if not res_dict:
            return "Error: 指定URLから有効な本文を取得できませんでした。"
        else:
            self.logger.info(f"取得した文字数: {len(res_dict["body"])}")

        return json.dumps(res_dict, ensure_ascii=False)