from abc import ABCMeta, abstractmethod

# AIエージェントシステムPlugin用インターフェース
class AgentPlugin(metaclass=ABCMeta):
    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError()

    @abstractmethod
    def get_tools(self) -> dict:
        raise NotImplementedError()

    @abstractmethod
    async def execute(self, query:str) -> str:
        raise NotImplementedError()