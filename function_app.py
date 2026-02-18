import os
from logging import getLogger
import azure.functions as func


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="LPInsightGenerator")
def LPInsightGenerator(req: func.HttpRequest) -> func.HttpResponse:
    logger = getLogger(__name__)
    logger.info('Python HTTP trigger function processed a request.')

    try:
        # 環境変数の取得
        AI_FOUNDRY_ENDPOINT       = os.environ.get("AI_FOUNDRY_ENDPOINT")
        AI_FOUNDRY_API_KEY        = os.environ.get("AI_FOUNDRY_API_KEY")
        AI_FOUNDRY_MODEL          = os.environ.get("AI_FOUNDRY_MODEL")
        AI_FOUNDRY_VERSION        = os.environ.get("AI_FOUNDRY_VERSION")
        DYNAMICSESSIONS_ENDPOINT  = os.environ.get("DYNAMICSESSIONS_ENDPOINT")
        MONGODB_CONNECTION_STRING = os.environ.get("MONGODB_CONNECTION_STRING")
        DB_NAME                   = os.environ.get("DB_NAME")
        COLLECTION_NAME           = os.environ.get("COLLECTION_NAME")

        # リクエストをパースする
        req_body = req.get_json()
        user_id  = req_body.get('user_id',  None)
        chat_id  = req_body.get('chat_id',  None)
        user_msg = req_body.get('user_msg', None)

        return func.HttpResponse(f"Success", status_code=200)

    except Exception as e:
        logger.error(f"Error: {e}")
        return func.HttpResponse(f"Error: {e}", status_code=500)