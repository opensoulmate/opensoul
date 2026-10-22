"""SQLite persistence for RPAExecutor._templates（RV-①b-⑦-3）——RPA 用户模板唯一落盘口。

修前（RV-① A 组定性）：RPAExecutor._templates 全在进程内存（dict），只有构造期
builtin 种子+create_template/delete_template 只改内存——用户自建模板（/templates
列表/单查真源、create_from_template 实例化源）随 service 重启全清。本模块把模板行
落 SQLite：模板每次变更随写随落盘（写穿），首触懒回载，重启用户模板不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、模板本体（含 actions/variables/tags 嵌套）整体
json.dumps 落 payload 列（dict 列禁裸存）。

测试隔离：LIMB_TEMPLATES_DB 环境变量覆盖库路径（LIMB_TASKS_DB/LIMB_HISTORY_DB/
GLAND_USAGE_DB 同款先例），缺省 <repo>/data/limb_templates.db；库路径每次连接按
env 解析（模块级 executor 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（与 limb_tasks 同族「状态快照表」）：limb_templates 是**当前态快照表**——
template_id=PRIMARY KEY，同模板每次变更 upsert 覆盖旧行=「当前态」语义（一个
模板一行）；echo_history/limb_history 是追加型历史表故禁 REPLACE。两者纪律不混用。

诚实边界：
①写失败不阻断模板本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——模板
  动作已生效，为落盘失败炸掉创建不成比例；代价是故障窗口内该态只活内存、重启丢
  （如实记，非谎报 durable）。
②回载失败=空模板起步+logger.error 可见，行真源仍在库下次触碰重试（RV-①b-②④
  语义）；不在回载故障上发明降级判法。
③行内单条不可解（payload 坏行/字段缺失）跳过该行并 logger.error 可见，不因
  单行坏数据丢整库模板（禁静默丢：跳过必留日志真因）。
④builtin 种子目录不进本表（代码=真源，create/delete 双口 builtin 恒拒写）；回载
  只补缺不覆盖=即使库里出现 builtin id 行也不洗掉代码种子（RPAExecutor 侧语义）。
⑤无老化/裁剪面：表只增不减（模板行被 upsert 覆盖不膨胀；delete_template 删行口
  在 RPAExecutor 侧真消费本模块 delete()，删模板即删行）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

_TABLE = "limb_templates"

_COLUMNS = (
    "template_id",
    "tenant_id",
    "category",
    "payload",
)


def default_db_path() -> str:
    """库路径：LIMB_TEMPLATES_DB 环境变量优先（测试隔离），缺省 <repo>/data/limb_templates.db。"""
    env = os.environ.get("LIMB_TEMPLATES_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "limb_templates.db")


class TemplatePersistence:
    """RPA 用户模板持久化真源（单表 limb_templates：upsert 写穿 + load_all 懒回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 executor 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④ 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "template_id TEXT PRIMARY KEY, "
            "tenant_id TEXT NOT NULL, "
            "category TEXT NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        return conn

    def upsert(self, *, template_id: str, tenant_id: str, category: str, payload: str) -> None:
        """写穿一行模板当前态（状态快照表语义：同 template_id upsert 覆盖=当前态）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（RPAExecutor
        侧=记账旁路纪律：logger.error 不阻断模板本体）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(template_id) DO UPDATE SET "
                    "tenant_id=excluded.tenant_id, category=excluded.category, "
                    "payload=excluded.payload",
                    (str(template_id), str(tenant_id), str(category), str(payload)),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, template_id: str) -> None:
        """删一行模板（delete_template 口真消费；幂等：不存在=无操作不抛）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"DELETE FROM {_TABLE} WHERE template_id = ?", (str(template_id),))
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list:
        """回载全部模板行 payload（json.loads 后的 dict 列表，rowid 升序=稳定回载序）。

        回载只读不建库：库文件缺失=空模板起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④ 同纪律）。
        单行不可解（json 坏行/非对象）跳过该行并 logger.error 可见（诚实边界③）。
        """
        import json
        import logging

        logger = logging.getLogger(__name__)
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(f"SELECT payload FROM {_TABLE} ORDER BY rowid").fetchall()
            finally:
                conn.close()
        out = []
        for (payload,) in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as e:
                logger.error(f"limb template 行 payload 不可解跳过: {e}")
                continue
            if not isinstance(data, dict):
                logger.error("limb template 行 payload 非对象跳过")
                continue
            out.append(data)
        return out

    def count(self) -> int:
        """行数（探针/取证回读口）。库缺失=0。"""
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return 0
            conn = self._connect()
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
            finally:
                conn.close()
        return int(row[0])
