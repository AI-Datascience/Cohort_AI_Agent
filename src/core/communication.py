from typing   import cast
from typing   import Dict, List
from datetime import datetime
from zoneinfo import ZoneInfo
from azure.ai.inference.models import SystemMessage, UserMessage, AssistantMessage
import pandas as pd

from ._interface import AgentStorage
from ._interface import AIgentClient


def build_message(sysmsg:str, usrmsg:str, pdf_data:pd.DataFrame) -> List:
    # pdf_dataに期待する構成
    # root
    #  |-- user_msg: string   (nullable = false)
    #  |-- response: string   (nullable = false)
    #  |-- created:  datetime (nullable = false)

    messages = []
    if sysmsg != '':
        messages.append(SystemMessage(content=sysmsg))
    
    pdf_data = pdf_data.sort_values(['created'], ignore_index=True, ascending=True)
    for row in pdf_data.itertuples():
        if getattr(row, 'user_msg'): messages.append(UserMessage(content=getattr(row, 'user_msg')))
        if getattr(row, 'response'): messages.append(AssistantMessage(content=getattr(row, 'response')))
    
    if usrmsg != '':
        messages.append(UserMessage(content=usrmsg))
    
    return messages

async def run_one_shot(param_dict:Dict, user_id:int, chat_id:int) -> str:
    # param_dictに期待する構成
    # dict
    #  |-- db     : AgentStorage,
    #  |-- agent  : AIgentClient,
    #  |-- sysmsg : str,
    #  |-- usrmsg : str,

    # 必須要素のパース
    # db       = cast(AgentStorage, param_dict['db'])
    agent    = cast(AIgentClient, param_dict['agent'])
    sysmsg   = cast(str,          param_dict['sysmsg'])
    usrmsg   = cast(str,          param_dict['usrmsg'])
    # pdf_data = db.read({"user_id": user_id, "chat_id": chat_id}, [("created", "asc")])

    # プロンプトの取得
    messages = build_message(sysmsg, usrmsg, pd.DataFrame([], columns=["created"]))

    # クライアントの実行
    tools    = await agent.collect_tools()
    response = await agent.complete(messages, tools)

    # 現在はDataBaseまでは必要ない
    # 履歴情報の保存
    # new_item = {
    #                 "user_id":      user_id,
    #                 "chat_id":      chat_id,
    #                 "user_msg":     usrmsg,
    #                 "modelname":    agent.get_modelname(),
    #                 "prompt":       messages,
    #                 "response":     response,
    #                 "created":      datetime.now(tz=ZoneInfo('Asia/Tokyo')).strftime("%Y-%m-%d %H:%M:%S.%f")
    #             }
    # db.write_one_shot(new_item)

    return response

