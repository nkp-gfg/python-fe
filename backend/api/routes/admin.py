from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.api.database import get_db
from backend.api.runtime_config import (
    get_multiflight_settings,
    update_multiflight_settings,
)


router = APIRouter(prefix="/admin", tags=["admin"])


class MultiFlightConfigResponse(BaseModel):
    timeoutSeconds: int = Field(..., ge=1, le=60)
    maxAttempts: int = Field(..., ge=1, le=24)
    includeCpaidEndpoint: bool


class MultiFlightConfigUpdate(BaseModel):
    timeoutSeconds: int = Field(..., ge=1, le=60)
    maxAttempts: int = Field(..., ge=1, le=24)
    includeCpaidEndpoint: bool


@router.get("/multiflight-config", response_model=MultiFlightConfigResponse)
def get_multiflight_config():
    return get_multiflight_settings()


@router.put("/multiflight-config", response_model=MultiFlightConfigResponse)
def put_multiflight_config(payload: MultiFlightConfigUpdate):
    return update_multiflight_settings(get_db(), payload.model_dump())
