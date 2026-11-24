import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from math import pi, sin

import pytest

from domain.learning import calculate, dispatch, run_trial


def make_rows(day_two_shift=0.0, bump_first_day_evening=False):
    """48 小时审签读数：方波基形（18 点档 135，21 点档 153，其余 90）。"""
    def base(hour):
        clock = hour % 24
        if clock == 21:
            return 153.0
        if 16 <= clock <= 19 or clock in (22, 23):
            return 135.0
        return 90.0

    rows = []
    for hour in range(48):
        value = base(hour)
        if hour >= 24:
            value += day_two_shift
        if bump_first_day_evening and hour < 24 and 16 <= (hour % 24) <= 21:
            value += 30
        rows.append({'code': f'WH-{hour:02d}', 'meter': 'BAT-04', 'hour': hour,
                     'load_wh': value, 'clear': True, 'verified': True})
    return rows


def smooth_rows_with_transient_bump():
    """光滑正弦基形 + 第一天晚间一次性凸起：Fourier 能滤掉凸起，滞后基准会把凸起带到留出段。"""
    rows = []
    for hour in range(48):
        value = 100 + 30 * sin(2 * pi * hour / 24) + 12 * sin(4 * pi * hour / 24 + 0.7)
        if hour < 24 and 16 <= (hour % 24) <= 21:
            value += 35
        rows.append({'code': f'WH-{hour:02d}', 'meter': 'BAT-04', 'hour': hour,
                     'load_wh': value, 'clear': True, 'verified': True})
    return rows


WINDOW = {'code': 'AIR-17', 'title': '晚间村讯', 'hour': 18, 'license': '本地插播', 'offline': True}


def trial_input(rows, baseline='fourier', candidate='persistence'):
    return {'kind': 'trial', 'window': WINDOW, 'meter': 'BAT-04', 'rows': rows, 'clear': True,
            'baseline_model': baseline, 'candidate_model': candidate}


def test_persistence_beats_fourier_on_repeated_square_wave():
    # 两日形态完全一致：滞后基准逐点全中（MAE=0），Fourier 难以拟合方波。
    result = run_trial(trial_input(make_rows()))
    assert result['baseline_model'] == 'fourier'
    assert result['candidate_model'] == 'persistence'
    assert result['candidate_mae'] == 0.0
    assert result['candidate_mae'] < result['baseline_mae']
    assert result['candidate_wins'] is True
    assert result['held_out'] == 6 and result['rows_used'] == 48


def test_fourier_beats_persistence_when_first_day_has_extra_evening_bump():
    # 第一天晚间有一次性凸起、第二天恢复：滞后基准把凸起带到留出段，误差比 Fourier 更大。
    result = run_trial(trial_input(smooth_rows_with_transient_bump()))
    assert result['candidate_wins'] is False
    assert result['candidate_mae'] > result['baseline_mae']


def test_calculate_snapshots_model_name():
    rows = make_rows()
    persistence = calculate({'kind': 'forecast', 'window': WINDOW, 'rows': rows,
                             'clear': True, 'model': 'persistence'})
    fourier = calculate({'kind': 'forecast', 'window': WINDOW, 'rows': rows,
                         'clear': True, 'model': 'fourier'})
    assert persistence['model'] == 'persistence'
    assert fourier['model'] == 'fourier'
    # 目标在计划日 18 点（绝对小时 42），滞后读数即第一天 18 点的 135。
    assert persistence['plan_wh'] == pytest.approx(135.0)


def test_legacy_forecast_payload_defaults_to_fourier():
    result = calculate({'window': WINDOW, 'rows': make_rows(), 'clear': True})
    assert result['model'] == 'fourier'


def test_dispatch_routes_by_kind():
    payload = trial_input(make_rows())
    assert dispatch(payload) == run_trial(payload)
    forecast = dispatch({'window': WINDOW, 'rows': make_rows(), 'clear': True})
    assert forecast['model'] == 'fourier'


def test_persistence_requires_prior_same_clock_reading():
    # 读数只覆盖第二天（24-47），留出 42 点找不到前一天同时刻。
    rows = make_rows()[24:]
    with pytest.raises(ValueError, match='前一天同一时刻'):
        calculate({'kind': 'forecast', 'window': WINDOW, 'rows': rows,
                   'clear': True, 'model': 'persistence'})


def test_trial_rejects_same_model_or_unknown_model():
    rows = make_rows()
    with pytest.raises(ValueError, match='必须不同'):
        run_trial(trial_input(rows, baseline='fourier', candidate='fourier'))
    with pytest.raises(ValueError, match='不在注册表'):
        run_trial(trial_input(rows, baseline='fourier', candidate='nope'))
