"""Option Target Calculator API.

POST /api/v1/optiontarget

Projects every option strike to a futures or spot price target and ranks them
by rupee P&L, percentage return, reward-to-risk or a balanced score.

`expiry_date` is DDMMMYY (11AUG26). Note that /api/v1/expiry returns the dashed
form (11-AUG-26), which this endpoint does NOT accept - callers must convert.
"""

import os

from flask import request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from limiter import limiter
from services.option_target_service import get_option_target
from utils.logging import get_logger

from .data_schemas import OptionTargetSchema

logger = get_logger(__name__)

api = Namespace("optiontarget", description="Project option premiums at a price target")

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")


@api.route("/", strict_slashes=False)
class OptionTarget(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Project and rank option strikes for a price target"""
        try:
            data = OptionTargetSchema().load(request.json)

            logger.info(
                "Option target request: %s %s expiry=%s reference=%s target=%s",
                data["underlying"],
                data["exchange"],
                data["expiry_date"],
                data["reference"],
                data["target_price"],
            )

            success, response, status_code = get_option_target(
                underlying=data["underlying"],
                exchange=data["exchange"],
                expiry_date=data["expiry_date"],
                reference=data["reference"],
                target_price=data["target_price"],
                api_key=data["apikey"],
                reference_price=data.get("reference_price"),
                hold_minutes=data["hold_minutes"],
                hold_days=data.get("hold_days"),
                iv_model=data["iv_model"],
                vol_beta=data["vol_beta"],
                vol_shift=data["vol_shift"],
                day_count=data["day_count"],
                strike_count=data["strike_count"],
                side=data["side"],
                lots=data["lots"],
                interest_rate=data["interest_rate"],
                objective=data["objective"],
            )
            return response, status_code

        except ValidationError as err:
            logger.warning("Validation error in option target request: %s", err.messages)
            return {"status": "error", "message": "Validation error", "errors": err.messages}, 400
        except Exception as e:
            logger.exception("Unexpected error in option target endpoint: %s", e)
            return {"status": "error", "message": "An unexpected error occurred"}, 500
