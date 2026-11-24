import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['JWT_SECRET'] = 'test-secret'

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fakeredis
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
import core.db as db
import core.queueing as queueing

# 单连接内存 SQLite，保证各事务看到同一份数据；fakeredis 替代 Redis 服务。
# 必须在导入任何领域模型（它们会把 Table 注册到 core.db.metadata）之前替换引擎。
db.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
queueing.broker = fakeredis.FakeRedis(decode_responses=True)

from core.app import app  # noqa: E402
from core.db import transaction  # noqa: E402
from domain.learning import dispatch  # noqa: E402
from domain.model_control_model import model_settings  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402


@pytest.fixture()
def broker():
    queueing.broker.flushall()
    yield queueing.broker


@pytest.fixture()
def client(broker):
    db.metadata.drop_all(db.engine)
    db.metadata.create_all(db.engine)
    with TestClient(app) as c:
        seed()
        yield c


def headers(role):
    token = jwt.encode({'sub': f'{role}@demo.test', 'role': role,
                        'exp': datetime.now(timezone.utc) + timedelta(hours=1)},
                       os.environ['JWT_SECRET'], algorithm='HS256')
    return {'Authorization': f'Bearer {token}'}


def compute(client, token, role='reviewer'):
    r = client.get(f'/compute/{token}', headers=headers(role))
    assert r.status_code == 200, r.text
    return r.json()


def drain(until=None):
    """模拟 worker 消费队列；until(token) 返回 True 时把该任务留在队列里不消费。"""
    while (raw := queueing.broker.rpop(queueing.QUEUE)) is not None:
        job = json.loads(raw)
        if until is not None and until(job['token']):
            queueing.broker.lpush(queueing.QUEUE, raw)
            break
        status = {'owner': job['owner'], 'input': job['input']}
        try:
            status.update(status='ready', result=dispatch(job['input']))
        except ValueError as exc:
            status.update(status='rejected', detail=str(exc))
        queueing.broker.set(f'compute:{job["token"]}', json.dumps(status, allow_nan=False), ex=86400)


def run_trial(client, candidate='persistence'):
    r = client.post('/reserves/trial-models', headers=headers('operator'),
                    json={'airslot': 'AIR-17', 'meter': 'BAT-04', 'clear': True,
                          'candidate_model': candidate})
    assert r.status_code == 200, r.text
    return r.json()['token']


def set_active(model):
    with transaction() as conn:
        conn.execute(model_settings.update().values(active_model=model))


def test_full_enable_flow_and_persisted_evidence(client):
    assert client.get('/reserves/active-model', headers=headers('viewer')).json() == \
           {'active_model': 'fourier'}

    token = run_trial(client)
    drain()
    stats = compute(client, token)
    assert stats['result']['candidate_wins'] is True
    assert stats['result']['candidate_mae'] < stats['result']['baseline_mae']

    # 作业员无权启用。
    assert client.post('/reserves/enable-model', headers=headers('operator'),
                       json={'token': token}).status_code == 403

    enabled = client.post('/reserves/enable-model', headers=headers('reviewer'),
                          json={'token': token})
    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body['code'] == 'MA-01' and body['status'] == '已启用'
    assert body['candidate_model'] == 'persistence'
    assert client.get('/reserves/active-model', headers=headers('viewer')).json() == \
           {'active_model': 'persistence'}

    # 启用后的新预测请求使用新模型，并随备用评估落库。
    fr = client.post('/reserves/forecast-wh', headers=headers('operator'),
                     json={'airslot': 'AIR-17', 'meter': 'BAT-04', 'clear': True})
    drain()
    assert compute(client, fr.json()['token'])['result']['model'] == 'persistence'
    saved = client.post('/reserves/save-reserve', headers=headers('operator'),
                        json={'code': 'RV-24', 'token': fr.json()['token']})
    assert saved.status_code == 200
    rows = client.get('/reserves', headers=headers('viewer')).json()
    assert next(x for x in rows if x['code'] == 'RV-24')['model'] == 'persistence'

    # 启用依据（两种误差）落 SQL。
    activations = client.get('/reserves/model-activations', headers=headers('viewer')).json()
    assert len(activations) == 1
    evidence = activations[0]['evidence']
    assert evidence['baseline_mae'] == stats['result']['baseline_mae']
    assert evidence['candidate_mae'] == stats['result']['candidate_mae']
    assert evidence['rows_used'] == 48 and evidence['held_out'] == 6


def test_enable_rejected_when_candidate_not_better(client):
    # 当前现用 persistence，候选 fourier 在演示的两日重复方波上误差更大。
    set_active('persistence')
    token = run_trial(client, candidate='fourier')
    drain()
    assert compute(client, token)['result']['candidate_wins'] is False

    rejected = client.post('/reserves/enable-model', headers=headers('reviewer'),
                           json={'token': token})
    assert rejected.status_code == 409
    assert '误差' in rejected.json()['detail']
    assert client.get('/reserves/active-model', headers=headers('viewer')).json() == \
           {'active_model': 'persistence'}
    assert client.get('/reserves/model-activations', headers=headers('viewer')).json() == []


def test_stale_baseline_application_rejected_and_only_one_activation(client):
    # 两份申请都以 fourier 为对照同时排队；先启用 T1 后，T2 的对照已不是现用模型。
    t1 = run_trial(client)
    t2 = run_trial(client)
    drain()
    set_active('fourier')

    first = client.post('/reserves/enable-model', headers=headers('reviewer'), json={'token': t1})
    assert first.status_code == 200
    second = client.post('/reserves/enable-model', headers=headers('reviewer'), json={'token': t2})
    assert second.status_code == 409
    assert '对照模型已不是现用模型' in second.json()['detail']
    assert client.get('/reserves/active-model', headers=headers('viewer')).json() == \
           {'active_model': 'persistence'}


def test_retry_enable_is_idempotent_with_same_credential(client):
    token = run_trial(client)
    drain()
    set_active('fourier')

    first = client.post('/reserves/enable-model', headers=headers('reviewer'),
                        json={'token': token}).json()
    retry = client.post('/reserves/enable-model', headers=headers('reviewer'),
                        json={'token': token})
    assert retry.status_code == 200
    assert retry.json()['code'] == first['code']

    rows = client.get('/reserves/model-activations', headers=headers('reviewer')).json()
    assert sum(1 for x in rows if x['trial_token'] == token) == 1
    assert client.get('/reserves/active-model', headers=headers('viewer')).json() == \
           {'active_model': 'persistence'}


def test_queued_forecast_keeps_model_snapshot_across_switch(client):
    set_active('fourier')
    # 预测先排队（fourier 快照），不消费。
    queued = client.post('/reserves/forecast-wh', headers=headers('operator'),
                         json={'airslot': 'AIR-17', 'meter': 'BAT-04', 'clear': True})
    forecast_token = queued.json()['token']

    # 试算单独消费并启用 persistence；drain 把预测任务原样留在队列。
    trial_token = run_trial(client)
    drain(until=lambda t: t == forecast_token)
    assert json.loads(queueing.broker.get(f'compute:{trial_token}'))['status'] == 'ready'
    assert client.post('/reserves/enable-model', headers=headers('reviewer'),
                       json={'token': trial_token}).status_code == 200

    # 切换后才消费那条在途预测：仍按排队时的 fourier 计算。
    drain()
    assert compute(client, forecast_token)['result']['model'] == 'fourier'

    # 新入队的请求用启用后的 persistence。
    fresh = client.post('/reserves/forecast-wh', headers=headers('operator'),
                        json={'airslot': 'AIR-17', 'meter': 'BAT-04', 'clear': True})
    drain()
    assert compute(client, fresh.json()['token'])['result']['model'] == 'persistence'


def test_evidence_remains_after_trial_record_expires(client):
    token = run_trial(client)
    drain()
    set_active('fourier')
    enabled = client.post('/reserves/enable-model', headers=headers('reviewer'),
                          json={'token': token}).json()

    queueing.broker.delete(f'compute:{token}')  # 模拟 Redis 试算记录 24 小时过期
    assert client.get(f'/compute/{token}', headers=headers('reviewer')).status_code == 404

    rows = client.get('/reserves/model-activations', headers=headers('viewer')).json()
    mine = next(x for x in rows if x['code'] == enabled['code'])
    assert mine['evidence']['baseline_model'] == 'fourier'
    assert mine['evidence']['candidate_model'] == 'persistence'
    assert mine['evidence']['baseline_mae'] >= 0 and mine['evidence']['candidate_mae'] >= 0
    assert mine['evidence']['rows_used'] == 48


def test_token_kind_cross_use_rejected(client):
    forecast = client.post('/reserves/forecast-wh', headers=headers('operator'),
                           json={'airslot': 'AIR-17', 'meter': 'BAT-04', 'clear': True})
    trial_token = run_trial(client)
    drain()
    bad_enable = client.post('/reserves/enable-model', headers=headers('reviewer'),
                             json={'token': forecast.json()['token']})
    assert bad_enable.status_code == 409 and '试算' in bad_enable.json()['detail']
    bad_save = client.post('/reserves/save-reserve', headers=headers('operator'),
                           json={'code': 'RV-X', 'token': trial_token})
    assert bad_save.status_code == 409 and '预测' in bad_save.json()['detail']


def test_viewer_is_read_only(client):
    assert client.post('/reserves/trial-models', headers=headers('viewer'),
                       json={'airslot': 'AIR-17', 'meter': 'BAT-04', 'clear': True,
                             'candidate_model': 'persistence'}).status_code == 403
    assert client.get('/reserves', headers=headers('viewer')).status_code == 200
    assert client.get('/reserves/model-activations', headers=headers('viewer')).status_code == 200
