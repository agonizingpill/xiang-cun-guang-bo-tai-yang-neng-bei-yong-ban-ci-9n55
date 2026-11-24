from datetime import datetime, timezone

from sqlalchemy import select

from core.queueing import result, submit
from core.records import get, insert, need, scan
from domain.learning import DEFAULT_MODEL, MODELS
from domain.model_control_model import model_activations, model_settings

SETTINGS_ID = 1


def _now():
    return datetime.now(timezone.utc)


def ensure_settings(conn):
    """保证现用模型指针单行存在；首次启动写入默认模型 fourier。"""
    row = conn.execute(select(model_settings).where(model_settings.c.id == SETTINGS_ID)
                       .with_for_update()).mappings().first()
    if row is None:
        conn.execute(model_settings.insert().values(id=SETTINGS_ID, active_model=DEFAULT_MODEL,
                                                     updated_at=_now()))
        return DEFAULT_MODEL
    return row['active_model']


def active_model(conn):
    row = conn.execute(select(model_settings).where(model_settings.c.id == SETTINGS_ID)).mappings().first()
    return row['active_model'] if row else DEFAULT_MODEL


def _verified_rows(conn, meter):
    from domain.battery_logs_model import battery_logs
    rows = sorted([x for x in scan(conn, battery_logs) if x['meter'] == meter and x['verified']],
                  key=lambda x: x['hour'])
    need(len(rows) >= 36 and all(y['hour'] - x['hour'] == 1 for x, y in zip(rows, rows[1:])),
         '要有连续三十六小时审签读数')
    return rows


def trial_models(conn, user, data):
    """提交一次双模型对比试算。基线取当前现用模型，对照与候选在同一批读数、同一段留出小时上比较。"""
    from domain.airslots_model import airslots
    slot = get(conn, airslots, data.airslot)
    rows = _verified_rows(conn, data.meter)
    baseline = active_model(conn)
    need(data.candidate_model in MODELS, '候选模型不在注册表')
    need(data.candidate_model != baseline, '候选模型与现用模型相同，无需启用')
    payload = {'kind': 'trial', 'window': slot, 'meter': data.meter, 'rows': rows,
               'clear': data.clear, 'baseline_model': baseline,
               'candidate_model': data.candidate_model}
    return submit(user, payload)


def list_activations(conn):
    return [dict(row) for row in
            conn.execute(select(model_activations).order_by(model_activations.c.created_at)).mappings()]


def _next_code(conn):
    total = conn.execute(select(model_activations.c.code)).mappings().fetchall()
    return f'MA-{len(total) + 1:02d}'


def enable_model(conn, user, data):
    """复核员启用新模型。

    规则：
    - 试算必须已完成；
    - 候选 MAE 必须严格小于对照 MAE；
    - 试算的对照模型必须仍是当前现用模型（两份申请并发时，只有仍以现用模型为对照的可生效）；
    - 同一试算重试不重复切换，返回原启用单和原凭据。
    """
    run = result(user, data.token)
    need(run['input'].get('kind') == 'trial', '该计算号不是模型试算任务')
    stats = run['result']

    # 先做不依赖锁的幂等短路：同一 trial token 重试直接返回原单，不重复切换。
    existing = conn.execute(select(model_activations)
                            .where(model_activations.c.trial_token == data.token)).mappings().first()
    if existing is not None:
        return dict(existing)

    # 设置行由应用启动（lifespan 的 ensure_settings）保证存在；请求路径只加锁读取，
    # 避免并发“不存在则插入”在同一主键上相撞。
    current = conn.execute(select(model_settings).where(model_settings.c.id == SETTINGS_ID)
                           .with_for_update()).mappings().first()
    need(current is not None, '模型设置未初始化，请重启服务')

    # 设置行锁内再做一次加锁幂等检查：并发重试同一试算号时，后到者也能读到已提交的原启用单。
    locked_existing = conn.execute(select(model_activations).where(
        model_activations.c.trial_token == data.token).with_for_update()).mappings().first()
    if locked_existing is not None:
        return dict(locked_existing)

    need(stats['baseline_model'] == current['active_model'],
         '对照模型已不是现用模型，此申请不再有效，请以现用模型重新试算')
    need(stats['candidate_mae'] < stats['baseline_mae'],
         '新模型留出段误差未严格小于现用模型，不能启用')

    decided_at = _now()
    evidence = {
        'trial_token': data.token,
        'meter': stats['meter'],
        'window': stats['window'],
        'rows_used': stats['rows_used'],
        'first_hour': stats['first_hour'],
        'last_hour': stats['last_hour'],
        'held_out': stats['held_out'],
        'training': stats['training'],
        'period': stats['period'],
        'clear': stats['clear'],
        'baseline_model': stats['baseline_model'],
        'candidate_model': stats['candidate_model'],
        'baseline_mae': stats['baseline_mae'],
        'candidate_mae': stats['candidate_mae'],
        'baseline_plan_wh': stats['baseline_plan_wh'],
        'candidate_plan_wh': stats['candidate_plan_wh'],
        'decided_at': decided_at.isoformat(),
    }
    code = _next_code(conn)
    insert(conn, model_activations, code=code, trial_token=data.token,
           baseline_model=stats['baseline_model'], candidate_model=stats['candidate_model'],
           baseline_mae=stats['baseline_mae'], candidate_mae=stats['candidate_mae'],
           status='已启用', evidence=evidence, reviewer=user['sub'], created_at=decided_at)
    conn.execute(model_settings.update().where(model_settings.c.id == SETTINGS_ID)
                 .values(active_model=stats['candidate_model'], updated_at=_now()))
    return {'code': code, 'trial_token': data.token,
            'baseline_model': stats['baseline_model'], 'candidate_model': stats['candidate_model'],
            'baseline_mae': stats['baseline_mae'], 'candidate_mae': stats['candidate_mae'],
            'status': '已启用', 'evidence': evidence, 'reviewer': user['sub'],
            'created_at': decided_at.isoformat()}
