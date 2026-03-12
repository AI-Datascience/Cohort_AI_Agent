from abc import ABCMeta, abstractmethod
from typing import Dict, List, Tuple, Any
import pandas as pd

# AIエージェントシステムDB用インターフェース
class AgentStorage(metaclass=ABCMeta):
    @abstractmethod
    def read(self, filter:Any, orderBy:List[Tuple]) -> pd.DataFrame:
        raise NotImplementedError()

    @abstractmethod
    def write_one_shot(self, data:Dict|List) -> None:
        raise NotImplementedError()

# AIエージェントシステムClient用インターフェース
class AIgentClient(metaclass=ABCMeta):
    @abstractmethod
    async def collect_tools(self) -> List|None:
        raise NotImplementedError()

    @abstractmethod
    async def complete(self, messages:List, tools:List|None=None) -> str:
        raise NotImplementedError()
    
    @abstractmethod
    def get_modelname(self) -> str:
        raise NotImplementedError()