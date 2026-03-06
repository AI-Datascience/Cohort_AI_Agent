import json
import ast
from typing import List, Dict
from openai import OpenAI, AsyncOpenAI


class LlmAgent:
    def __init__(self, llmClient:AsyncOpenAI, model_name:str, max_tokens:int, temperature:float, top_p:float):
        self.llm         = llmClient
        self.model_name  = model_name
        self.max_tokens  = max_tokens
        self.temperature = temperature
        self.top_p       = top_p
    
    def parse_content(self, content:str) -> List|Dict:
        try:
            # 1. まずは高速で標準的な json ライブラリを試す
            parsed_data = json.loads(content)
        except json.JSONDecodeError:
            try:
                # 2. JSONで失敗したら（シングルクォート等が原因）、Python形式として解析する
                # ast.literal_eval は安全に文字列をリスト/辞書に変換
                parsed_data = ast.literal_eval(content)
            except Exception:
                # どっちも無理なら、辞書型としてパックする
                parsed_data = {"text": content}
        
        # リストや辞書型でない場合、辞書型としてパックする
        if not isinstance(parsed_data, (list, dict)):
            parsed_data = {"text": str(parsed_data)}

        return parsed_data
    
    async def complete(self, prompts:List):
        # レスポンス速度重視のため
        # 軽量なLLMを利用することとした(MAX_TOKENS・TEMPERATURE・TOP_P等のオプション対応不可なモデル)
        # response = await self.llm.chat.completions.create(
        #                         model=self.model_name,
        #                         messages=prompts,
        #                         tools=None,
        #                         tool_choice=None,
        #                         max_tokens=self.max_tokens,
        #                         temperature=self.temperature,
        #                         top_p=self.top_p
        #                     )
        response = await self.llm.chat.completions.create(
                                model=self.model_name,
                                messages=prompts
                            )
        
        reply_content = response.choices[0].message.content
        reply_data    = self.parse_content(reply_content)
        return reply_data
