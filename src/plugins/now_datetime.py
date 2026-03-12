import json
from datetime import datetime
from zoneinfo import ZoneInfo
from logging  import Logger, getLogger

from ._interface import AgentPlugin

class NowDateTimePlugin(AgentPlugin):
    def __init__(self, logger:Logger=getLogger(__name__)):
        self.name   = "NowDateTime"
        self.logger = logger
    
    def get_name(self) -> str:
        return self.name

    def get_tools(self) -> dict:
        tool_def = {
            "type"     : "function",
            "function" : {
                            "name": self.name,
                            "description": (
                                "pythonのdatetimeライブラリを利用して、"
                                "指定されたタイムゾーンの現在時刻を取得します。"
                                "世界各地の時刻や、時差を考慮した日時が必要な場合に使用します。"
                                "また最新のニュース、時事問題、イベント、技術情報など現在の時刻情報に照らし合わせる必要がある場合に使用してください。"
                                ""
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": (
                                            "取得したい地域のIANAタイムゾーンID（例: 'Asia/Tokyo', 'America/New_York', 'UTC', 'Europe/London'）。"
                                            "デフォルトは 'UTC' です。"
                                            ""
                                        ),
                                    }
                                },
                                "required": ["query"]
                            }
                    }
        }
        return tool_def

    async def execute(self, query:str) -> str:

        try:
            query    = query.strip() if query else "UTC"
            now_time = datetime.now(tz=ZoneInfo(query))
            res_dict = {
                "status":     "success",
                "iso_8601":   now_time.isoformat(),
                "timezone":   query,
                "date":       now_time.strftime("%Y-%m-%d"),
                "time":       now_time.strftime("%H:%M:%S"),
                "weekday":    now_time.strftime("%A"),
                "utc_offset": now_time.strftime("%z"),
                "is_dst":     bool(now_time.dst())
            }
        
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

        return json.dumps(res_dict, ensure_ascii=False)