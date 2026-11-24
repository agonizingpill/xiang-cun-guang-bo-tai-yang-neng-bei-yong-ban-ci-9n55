from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Table

from core.db import metadata

# 现用预测模型的单行指针；id 恒为 1，启用新模型时在事务内行锁更新。
model_settings = Table('model_settings', metadata,
    Column('id', Integer, primary_key=True, autoincrement=False),
    Column('active_model', String(64), nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

# 模型启用申请的留痕：trial 的完整依据与两种误差在启用时固化到 SQL，
# 即使 Redis 里的试算记录（ex=86400）过期，备用核算页面仍可查。
model_activations = Table('model_activations', metadata,
    Column('code', String(64), primary_key=True),
    Column('trial_token', String(64), nullable=False, unique=True),
    Column('baseline_model', String(64), nullable=False),
    Column('candidate_model', String(64), nullable=False),
    Column('baseline_mae', Float, nullable=False),
    Column('candidate_mae', Float, nullable=False),
    Column('status', String(32), nullable=False),
    Column('evidence', JSON, nullable=False),
    Column('reviewer', String(160), nullable=False),
    Column('created_at', DateTime, nullable=False),
)
