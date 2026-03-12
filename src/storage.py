from typing  import List, Dict, Tuple, Any
from logging import Logger, getLogger
import pymongo
import pandas as pd
from pandas import json_normalize

from .core._interface import AgentStorage


class MongoWrapper(AgentStorage):
    def __init__(self, MONGODB_CONNECTION_STRING, DB_NAME, COLLECTION_NAME, logger:Logger=getLogger(__name__)):
        super().__init__()
        self.MONGODB_CONNECTION_STRING = MONGODB_CONNECTION_STRING
        self.DB_NAME                   = DB_NAME
        self.COLLECTION_NAME           = COLLECTION_NAME
        self.dbclient                  = pymongo.MongoClient(self.MONGODB_CONNECTION_STRING)
        self.collection                = self.dbclient[self.DB_NAME][self.COLLECTION_NAME]
        self.logger                    = logger

    def read(self, filter:Any, orderBy:List[Tuple]) -> pd.DataFrame:
        cursor = self.collection.find(filter)
        if orderBy:
            mongo_sort = [
                (elem[0], 1 if elem[1].lower() == "asc" else -1)
                for elem in orderBy
            ]
            cursor = cursor.sort(mongo_sort)
        
        pdf_data = json_normalize(list(cursor))
        if len(pdf_data) == 0:
            pdf_data = pd.DataFrame(columns=['user_msg', 'response', 'created'])
        
        return pdf_data

    def write_one_shot(self, data:Dict|List) -> None:
        if isinstance(data, list):
            self.collection.insert_many(data)
        else:
            self.collection.insert_one(data)
        return
    
    def close(self):
        if self.dbclient:
            self.dbclient.close()

    # with文対応 (推奨)
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
