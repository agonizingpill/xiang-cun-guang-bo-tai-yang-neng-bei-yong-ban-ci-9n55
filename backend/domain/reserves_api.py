from fastapi import APIRouter, Depends

from core.auth import actor, allow
from core.audit import record
from core.db import transaction
from core.records import scan
from domain import model_control_service as models
from domain import reserves_service as service
from domain.reserves_model import reserves
from domain.reserves_schemas import EnableModel, ForecastWh, SaveReserve, TrialModels

router = APIRouter()

@router.get('/reserves')
def list_reserves(user=Depends(actor)):
    with transaction() as conn:
        return scan(conn, reserves)


@router.get('/reserves/active-model')
def get_active_model(user=Depends(actor)):
    with transaction() as conn:
        return {'active_model': models.active_model(conn)}


@router.get('/reserves/model-activations')
def list_model_activations(user=Depends(actor)):
    with transaction() as conn:
        return models.list_activations(conn)


@router.post('/reserves/forecast-wh')
def forecast_wh(data: ForecastWh, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.forecast_wh(conn, user, data)
        record(conn, user, 'reserves.forecast_wh')
        return output


@router.post('/reserves/trial-models')
def trial_models(data: TrialModels, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = models.trial_models(conn, user, data)
        record(conn, user, 'reserves.trial_models')
        return output


@router.post('/reserves/enable-model')
def enable_model(data: EnableModel, user=Depends(actor)):
    allow(user, ('reviewer',))
    with transaction() as conn:
        output = models.enable_model(conn, user, data)
        record(conn, user, 'reserves.enable_model')
        return output


@router.post('/reserves/save-reserve')
def save_reserve(data: SaveReserve, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        output = service.save_reserve(conn, user, data)
        record(conn, user, 'reserves.save_reserve')
        return output
