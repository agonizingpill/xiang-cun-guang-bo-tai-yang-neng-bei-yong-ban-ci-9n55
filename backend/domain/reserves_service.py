from core.queueing import submit, result
from core.records import get, insert, need, scan
from domain.airslots_model import airslots
from domain.battery_logs_model import battery_logs
from domain.model_control_service import active_model
from domain.reserves_model import reserves


def forecast_wh(conn, user, data):
    slot = get(conn, airslots, data.airslot)
    rows = sorted([x for x in scan(conn, battery_logs) if x['meter'] == data.meter and x['verified']],
                  key=lambda x: x['hour'])
    need(len(rows) >= 36 and all(y['hour'] - x['hour'] == 1 for x, y in zip(rows, rows[1:])),
         '要有连续三十六小时审签读数')
    # 入队即固化模型快照：已排队任务始终用排队时的模型，启用新模型不影响在途任务。
    model = active_model(conn)
    return submit(user, {'kind': 'forecast', 'window': slot, 'meter': data.meter, 'rows': rows,
                         'clear': data.clear, 'model': model})


def save_reserve(conn, user, data):
    run = result(user, data.token)
    need(run['input'].get('kind', 'forecast') == 'forecast', '该计算号不是备用预测任务')
    window = run['input']['window']
    stats = run['result']
    need(get(conn, airslots, window['code'])['hour'] == window['hour'], '节目时间已变')
    return insert(conn, reserves, code=data.code, airslot=window['code'], plan_wh=stats['plan_wh'],
                  fallback='本地优先' if window['offline'] else '受控中继', mae=stats['mae'],
                  model=stats['model'], token=data.token)
