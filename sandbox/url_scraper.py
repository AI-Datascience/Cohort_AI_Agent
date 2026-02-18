import asyncio
from logging import Logger
import httpx
from w3lib.encoding import html_to_unicode
from readability    import Document
from bs4            import BeautifulSoup


async def fetch_web(logger:Logger, semaphore:asyncio.Semaphore, weburl:str, MAX_CHARS_PER_SITE:int=10000):
    try:
        async with semaphore:
            # ユーザーエージェントの設定
            headers  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0, verify=False) as client:
                response = await client.get(weburl, headers=headers)
        
            # ステータスコードチェック
            response.raise_for_status()
            if response.status_code != 200:
                raise httpx.HTTPStatusError(f"Error: Status code {response.status_code}")
        
            # HTML以外はスキップ
            content_type = response.headers.get('Content-Type', '').lower()
            if 'text/html' not in content_type:
                logger.info(f"URL: {weburl} is not 'text/html'")
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

        if len(body) > MAX_CHARS_PER_SITE:
            body = body[:MAX_CHARS_PER_SITE] + "..." # 省略記号をつける

        res_dict = {
            "url":   weburl,
            "title": title,
            "body":  body,
        }
    
    except Exception as e:
        logger.warning(f"Warning: {e}")
        return None
    
    return res_dict