import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from limiter import limiter
from services.rollingoption_service import get_rolling_option_history
from utils.logging import get_logger

from .data_schemas import RollingOptionSchema

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")

api = Namespace("rollingoption", description="Rolling (spot-relative) option history")

logger = get_logger(__name__)

rolling_option_schema = RollingOptionSchema()


@api.route("/", strict_slashes=False)
class RollingOption(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Get spot-relative option history, including expired contracts."""
        try:
            payload = rolling_option_schema.load(request.json)
        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)

        api_key = payload.pop("apikey")
        ok, response, status = get_rolling_option_history(api_key=api_key, **payload)
        if not ok:
            logger.warning(f"rollingoption request rejected: {response.get('message')}")
        return make_response(jsonify(response), status)
