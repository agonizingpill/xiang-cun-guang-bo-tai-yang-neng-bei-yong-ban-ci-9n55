from math import pi, cos, sin

from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

# 模型注册表。fourier 为现用模型（24 小时 Fourier 基 + OLS）；
# persistence 为新模型：拿前一天同一时刻的审签用量做预测（24 小时滞后基准）。
MODELS = ('fourier', 'persistence')
DEFAULT_MODEL = 'fourier'
HOLD_OUT = 6
PERIOD = 24


def _design(hour, clear):
    angle = 2 * pi * hour / PERIOD
    return [sin(angle), cos(angle), sin(2 * angle), cos(2 * angle), int(clear)]


def _fourier(rows, window, clear):
    train, held = rows[:-HOLD_OUT], rows[-HOLD_OUT:]
    model = LinearRegression()
    model.fit([_design(x['hour'], x['clear']) for x in train],
              [x['load_wh'] for x in train])
    actual = [x['load_wh'] for x in held]
    predicted = model.predict([_design(x['hour'], x['clear']) for x in held])
    target = float(model.predict([_design(window['hour'], clear)])[0])
    mae = float(mean_absolute_error(actual, predicted))
    return {'mae': round(mae, 3), 'plan_wh': round(max(target, 0) + mae, 3),
            'held_out': len(held), 'period': PERIOD, 'training': len(train)}


def _lag_row(rows, hour):
    # 前一天同一时刻：同一时钟小时（hour 对 24 取模）、且早于当前整点的最近一条审签读数。
    same_clock = [x for x in rows if x['hour'] < hour and x['hour'] % PERIOD == hour % PERIOD]
    if not same_clock:
        raise ValueError('前一天同一时刻缺少审签读数，无法做滞后预测')
    return max(same_clock, key=lambda x: x['hour'])


def _persistence(rows, window, clear):
    train, held = rows[:-HOLD_OUT], rows[-HOLD_OUT:]
    # 留出段误差：每个留出整点都取其前一天同一时刻的审签读数作为预测。
    actual = [x['load_wh'] for x in held]
    predicted = [_lag_row(rows, x['hour'])['load_wh'] for x in held]
    # 节目时段给的是时钟小时，预测目标在计划日（次日）：换算成绝对小时后再取前一天同时刻。
    target_hour = PERIOD + (window['hour'] % PERIOD)
    target = _lag_row(rows, target_hour)['load_wh']
    mae = float(mean_absolute_error(actual, predicted))
    return {'mae': round(mae, 3), 'plan_wh': round(max(target, 0) + mae, 3),
            'held_out': len(held), 'period': PERIOD, 'training': len(train),
            'lag_hours': PERIOD}


def _run(model_name, rows, window, clear):
    if model_name == 'fourier':
        return _fourier(rows, window, clear)
    if model_name == 'persistence':
        return _persistence(rows, window, clear)
    raise ValueError(f'未知模型 {model_name}')


def calculate(data):
    """预测任务（kind=forecast）。按入队时固化的 model 快照计算。"""
    rows = data['rows']
    model_name = data.get('model', DEFAULT_MODEL)
    stats = _run(model_name, rows, data['window'], data['clear'])
    stats['model'] = model_name
    return stats


def run_trial(data):
    """模型对比试算（kind=trial）：两个模型用同一批审签读数、同一段留出小时，各自给出 MAE。"""
    rows = data['rows']
    baseline = data['baseline_model']
    candidate = data['candidate_model']
    if baseline not in MODELS or candidate not in MODELS:
        raise ValueError('试算模型不在注册表')
    if baseline == candidate:
        raise ValueError('对照模型与候选模型必须不同')
    baseline_stats = _run(baseline, rows, data['window'], data['clear'])
    candidate_stats = _run(candidate, rows, data['window'], data['clear'])
    return {
        'kind': 'trial',
        'meter': data['meter'],
        'baseline_model': baseline,
        'candidate_model': candidate,
        'baseline_mae': baseline_stats['mae'],
        'candidate_mae': candidate_stats['mae'],
        'candidate_wins': candidate_stats['mae'] < baseline_stats['mae'],
        'held_out': baseline_stats['held_out'],
        'training': baseline_stats['training'],
        'period': PERIOD,
        'window': data['window'],
        'rows_used': len(rows),
        'first_hour': rows[0]['hour'],
        'last_hour': rows[-1]['hour'],
        'clear': data['clear'],
        'baseline_plan_wh': baseline_stats['plan_wh'],
        'candidate_plan_wh': candidate_stats['plan_wh'],
    }


def dispatch(job_input):
    """worker 入口：按任务类型分发；老的预测任务（无 kind）保持原行为。"""
    if job_input.get('kind') == 'trial':
        return run_trial(job_input)
    return calculate(job_input)
