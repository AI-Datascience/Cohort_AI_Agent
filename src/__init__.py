# src/mylib/__init__.py

# バージョン情報
__version__ = "0.1.0"

# ユーザーに使わせたいクラスだけを公開
from .client             import BasicClient
from .storage            import MongoWrapper
from .core.communication import build_message, run_one_shot
from .plugins.web_search import WebSearchPlugin

# 名前空間を汚さないために、内部モジュールは __all__ で制御
__all__ = ["BasicClient", "MongoWrapper", "build_message", "run_one_shot", "WebSearchPlugin"]