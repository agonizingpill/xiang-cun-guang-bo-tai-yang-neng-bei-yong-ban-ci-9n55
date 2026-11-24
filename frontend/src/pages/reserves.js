import {workspace} from '../workspace.js';
import {el, display} from '../dom.js';
import {request} from '../api.js';
import {status} from '../widgets.js';

const MODEL_LABELS = Object.freeze({fourier: 'Fourier 周期拟合（现用）', persistence: '前一天同时刻用量（新）'});
const modelLabel = value => MODEL_LABELS[value] || display(value);

const page = {
  "title": "从两日用量核算备用",
  "module": "备用分钟核算",
  "caption": "备用分钟核算",
  "detail": "两种模型在同一批审签读数、同一段留出六小时上比 MAE；新模型误差更小才可由复核员启用。",
  "path": "/reserves",
  "columns": [
    {"name": "code", "label": "编号"},
    {"name": "airslot", "label": "节目时段"},
    {"name": "plan_wh", "label": "计划备用 Wh"},
    {"name": "fallback", "label": "备用源优先级"},
    {"name": "mae", "label": "时间验证MAE Wh"},
    {"name": "model", "label": "预测模型", "format": modelLabel},
    {"name": "token", "label": "计算号"}
  ],
  "layout": "split",
  "view": "grid",
  "actions": [
    {
      "name": "forecast_wh",
      "label": "计算周期用量与备量",
      "path": "/reserves/forecast-wh",
      "fields": [
        {"name": "airslot", "label": "节目编号", "kind": "text", "initial": "AIR-17"},
        {"name": "meter", "label": "电表", "kind": "text", "initial": "BAT-04"},
        {"name": "clear", "label": "计划日晴天", "kind": "bool", "initial": true}
      ],
      "help": "按当前现用模型排队；已排队任务始终沿用排队时的模型。",
      "roles": ["operator", "reviewer"],
      "success": "已写入"
    },
    {
      "name": "trial_models",
      "label": "双模型同段留出试算",
      "path": "/reserves/trial-models",
      "fields": [
        {"name": "airslot", "label": "节目编号", "kind": "text", "initial": "AIR-17"},
        {"name": "meter", "label": "电表", "kind": "text", "initial": "BAT-04"},
        {"name": "clear", "label": "计划日晴天", "kind": "bool", "initial": true},
        {"name": "candidate_model", "label": "候选新模型", "kind": "select",
         "options": [{"value": "persistence", "label": "前一天同时刻用量（新）"}],
         "initial": "persistence"}
      ],
      "help": "现用模型作对照，同一批审签读数、同一段末六小时各算一个 MAE。",
      "roles": ["operator", "reviewer"],
      "success": "试算已提交"
    },
    {
      "name": "enable_model",
      "label": "复核启用新模型",
      "path": "/reserves/enable-model",
      "fields": [
        {"name": "token", "label": "试算计算号", "kind": "token", "initial": ""}
      ],
      "help": "仅复核员可提交；候选 MAE 严格更小、且对照仍是现用模型时才切换。重试不重复切换。",
      "roles": ["reviewer"],
      "success": "模型已启用"
    },
    {
      "name": "save_reserve",
      "label": "记录备用量评估",
      "path": "/reserves/save-reserve",
      "fields": [
        {"name": "code", "label": "备用评估单", "kind": "text", "initial": "RV-24"},
        {"name": "token", "label": "计算号", "kind": "token", "initial": ""}
      ],
      "help": "按调班时对应的许可情况留下备用评估，模型取任务排队时的快照。",
      "roles": ["operator", "reviewer"],
      "success": "已写入"
    }
  ]
};

export const {title} = page;
export const key = "reserves";

function activationCard(row) {
  const e = row.evidence || {};
  const pairs = [
    ['试算计算号', row.trial_token],
    ['对照模型', modelLabel(row.baseline_model)],
    ['候选模型', modelLabel(row.candidate_model)],
    ['对照 MAE Wh', row.baseline_mae],
    ['候选 MAE Wh', row.candidate_mae],
    ['留出小时', e.held_out],
    ['训练小时', e.training],
    ['审签读数行数', e.rows_used],
    ['读数小时范围', `${display(e.first_hour)}–${display(e.last_hour)}`],
    ['复核员', row.reviewer],
    ['启用时间', row.created_at]
  ];
  return el('article', {class: 'record activation'},
    el('h3', {}, `${row.code} · ${row.status}`),
    el('dl', {}, pairs.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, display(v))])));
}

export async function render(root, role) {
  const badge = el('p', {class: 'status', id: 'active-model'}, '读取现用模型…');
  const evidencePanel = el('section', {class: 'ledger', 'aria-label': '模型启用依据'},
    el('h2', {}, '模型启用依据（试算记录过期后仍可查）'));

  const refreshEvidence = async () => {
    try {
      const rows = await request('/reserves/model-activations');
      const body = rows.length
        ? el('div', {class: 'records stack'}, ...rows.map(activationCard))
        : status('暂无启用记录。');
      evidencePanel.replaceChildren(el('h2', {}, '模型启用依据（试算记录过期后仍可查）'), body);
    } catch (error) {
      evidencePanel.replaceChildren(status(error.message, 'error'));
    }
  };

  await workspace(root, role, page, async refresh => {
    root.append(badge, evidencePanel);
    await refreshEvidence();
    try {
      const current = await request('/reserves/active-model');
      badge.textContent = '现用预测模型：' + modelLabel(current.active_model);
      badge.classList.add('ok');
    } catch (error) {
      badge.textContent = error.message;
      badge.classList.add('error');
    }
    return async () => {
      await refresh();
      await refreshEvidence();
    };
  });
}
