import ast
import json
import asyncio
from typing  import List, Dict
from logging import Logger, getLogger
from httpx   import AsyncClient
from openai  import OpenAI, AsyncOpenAI
from azure.ai.inference.models import ChatResponseMessage
from azure.ai.inference.models import ToolMessage

from .core._interface    import AIgentClient
from .plugins._interface import AgentPlugin

class BasicClient(AIgentClient):
    def __init__(self, AI_FOUNDRY_MODEL:str, max_tokens:int|None=None, temperature:float|None=None, top_p:float|None=None, logger:Logger=getLogger(__name__)):
        super().__init__()
        self.AI_FOUNDRY_MODEL               = AI_FOUNDRY_MODEL
        self.max_tokens                     = max_tokens
        self.temperature                    = temperature
        self.top_p                          = top_p
        self.logger                         = logger
        self.llm:AsyncOpenAI|None           = None
        self.plugins:Dict[str, AgentPlugin] = {}

    def configure(self, endpoint:str, api_key:str, http_client:AsyncClient|None=None):
        if self.llm is None:
            self.llm = AsyncOpenAI(
                    base_url=endpoint,
                    api_key=api_key,
                    http_client=http_client,
                    max_retries=3
                )
        return self.llm
    
    def register(self, plugin: AgentPlugin):
        self.plugins[plugin.get_name()] = plugin
        return self
    
    async def collect_tools(self) -> List|None:
        all_tools = []
        for plugin in self.plugins:
            # プラグインが get_tools メソッドを持っていたら呼ぶ
            if hasattr(self.plugins[plugin], "get_tools"):
                all_tools.append(self.plugins[plugin].get_tools())
        
        all_tools = all_tools if all_tools else None
        return all_tools
    
    async def use_tools(self, depth:int, messages:List, tools:List, resmsg:ChatResponseMessage) -> str:
        # 破壊的な変更
        messages.append(resmsg)

        async def _execute_plugin(call):
            tool_name = call.function.name
            raw_args  = call.function.arguments

            raw_args  = json.loads(raw_args)
            response  = await self.plugins[tool_name].execute(**raw_args)
            tool_msg  = ToolMessage(tool_call_id=call.id, content=response)
            return tool_msg

        tool_tasks = [asyncio.create_task(_execute_plugin(call)) for call in resmsg.tool_calls]
        tool_msgs  = await asyncio.gather(*tool_tasks)
        messages.extend(tool_msgs)

        # 一定以上の深さまでしか対応しない
        # マルチステップ実行
        if depth > 0:
            response   = await self.llm.chat.completions.create(
                messages=messages,
                tools=tools,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                model=self.AI_FOUNDRY_MODEL
            )

            if response.choices[0].message.tool_calls is not None:
                self.logger.info(f"call tools: {response.choices[0].message.tool_calls}")
                response = await self.use_tools(depth - 1, messages, tools, response.choices[0].message)
        else:
            response   = await self.llm.chat.completions.create(
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                model=self.AI_FOUNDRY_MODEL
            )


        return response
    
    def content2text(self, content:str) -> str:
        try:
            # 1. まずは高速で標準的な json ライブラリを試す
            parsed_data = json.loads(content)
        except json.JSONDecodeError:
            try:
                # 2. JSONで失敗したら（シングルクォート等が原因）、Python形式として解析する
                # ast.literal_eval は安全に文字列をリスト/辞書に変換
                parsed_data = ast.literal_eval(content)
            except Exception:
                # どっちも無理なら、ただの文字列として扱う
                parsed_data = content
        
        if isinstance(parsed_data, list):
            extracted_text = ""
            for item in parsed_data:
                # 辞書型で、かつ type が text のものだけ抽出
                if isinstance(item, dict) and item.get('type') == 'text':
                    text_val = item.get('text', '')
                    # 引用メタデータっぽいJSON文字列は除外する
                    if not text_val.strip().startswith('{"reference_ids"'):
                        extracted_text += text_val
            
            # テキストが抽出できていれば、それを最終回答とする
            if extracted_text:
                final_content = extracted_text
        
        else:
            final_content = parsed_data

        return final_content

    async def complete(self, messages:List, tools:List|None=None) -> str:
        response  = await self.llm.chat.completions.create(
            messages=messages,
            tools=tools,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            model=self.AI_FOUNDRY_MODEL
        )
        if response.choices[0].message.tool_calls is not None:
             self.logger.info(f"call tools: {response.choices[0].message.tool_calls}")
             response = await self.use_tools(3, messages, tools, response.choices[0].message)

        reply_content = response.choices[0].message.content
        reply_text    = self.content2text(reply_content)
        return reply_text
    
    def get_modelname(self) -> str:
        return self.AI_FOUNDRY_MODEL
    
    def close(self):
        if self.llm is not None:
            self.llm.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
