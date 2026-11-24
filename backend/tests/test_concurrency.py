"""真实并发验证：两份启用申请同时提交，只接受仍以现用模型为对照的那份。

生产用 MySQL，SELECT ... FOR UPDATE 会串行化两个事务。SQLite 不支持行锁，
这里用互斥体模拟设置行锁的临界区，验证的是锁内业务规则（对照模型校验 + 幂等），
锁机制本身由数据库保证。
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['JWT_SECRET'] = 'test-secret'

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fakeredis
from sqlalchemy import create_engine

import core.db as db
import core.queueing as queueing

tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
db.engine = create_engine(f'sqlite:///{tmp.name}', connect_args={'timeout': 30})
queueing.broker = fakeredis.FakeRedis(decode_responses=True)

from core.db import transaction
from domain import model_control_service as service
from domain.learning import dispatch
from domain.model_control_model import model_activations, model_settings
from scripts.seed_demo import seed

db.metadata.create_all(db.engine)
seed()
with transaction() as conn:
    service.ensure_settings(conn)

reviewer = {'sub': 'reviewer@demo.test', 'role': 'reviewer'}
rows = [dict(hour=h, load_wh=90.0 + (15 if 16 <= h % 24 <= 20 else 0),
             clear=True, verified=True) for h in range(48)]


def make_trial(seed_token):
    payload = {'kind': 'trial', 'window': {'code': 'AIR-17', 'hour': 18, 'offline': True},
               'meter': 'BAT-04', 'rows': rows, 'clear': True,
               'baseline_model': 'fourier', 'candidate_model': 'persistence'}
    result = dispatch(payload)
    queueing.broker.set(f'compute:{seed_token}', json.dumps(
        {'owner': reviewer['sub'], 'status': 'ready', 'input': payload, 'result': result}))
    return seed_token


class Data:
    def __init__(self, token):
        self.token = token


row_lock = threading.Lock()  # 模拟 MySQL 设置行的 FOR UPDATE
outcomes = []
barrier = threading.Barrier(2)


def enable(token):
    barrier.wait()
    with row_lock:  # 临界区等价于 MySQL 上设置行锁持有的整段事务
        try:
            with transaction() as conn:
                outcomes.append(('ok', token, service.enable_model(conn, reviewer, Data(token))['code']))
        except Exception as exc:  # noqa: BLE001 - 收集被拒线程的业务原因
            outcomes.append(('rejected', token, getattr(exc, 'detail', str(exc))))


t1, t2 = make_trial('trial-1'), make_trial('trial-2')
threads = [threading.Thread(target=enable, args=(t1,)),
           threading.Thread(target=enable, args=(t2,))]
for t in threads:
    t.start()
for t in threads:
    t.join()

assert sorted(x[0] for x in outcomes) == ['ok', 'rejected'], outcomes
assert any(x[0] == 'rejected' and '对照模型已不是现用模型' in x[2] for x in outcomes), outcomes
winning = next(x for x in outcomes if x[0] == 'ok')
winning_token, winning_code = winning[1], winning[2]
with transaction() as conn:
    active = conn.execute(model_settings.select().where(model_settings.c.id == 1)).mappings().first()
    assert active['active_model'] == 'persistence'
    count = len(conn.execute(model_activations.select()).mappings().fetchall())
assert count == 1, count

# 同一试算号在锁内重试：幂等返回同一凭据，不产生第二条记录。
with row_lock:
    with transaction() as conn:
        first = service.enable_model(conn, reviewer, Data(winning_token))
        retry = service.enable_model(conn, reviewer, Data(winning_token))
assert retry['code'] == first['code'] == winning_code
with transaction() as conn:
    assert len(conn.execute(model_activations.select()).mappings().fetchall()) == 1

print('CONCURRENCY OK:', outcomes)
os.unlink(tmp.name)
