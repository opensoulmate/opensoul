"""SQLite persistence for DownloadPlugin 任务注册表（RV-①b-⑥-3）——插件任务行唯一落盘口。

修前（RV-① A 组定性+live --pre 硬证）：DownloadPlugin 的 _running/_paused 全在
进程内存（每插件类独立 dict），_track/pause/resume_download/cancel 只改内存——
插件任务面（/api/download/tasks plugin_tasks 列表真源、pause 续传上下文）随
service 重启全清，而半成品字节本就在盘（dest 持久）=重启后半成品无人认领、
续传上下文（url/dest/resume）丢。本模块把任务行落 SQLite：任务状态每次变更随写
随落盘（写穿），首触懒回载，重启任务不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、续传上下文（url/dest/resume）整体 json.dumps 落 payload
列（dict 列禁裸存）。

测试隔离：DOWNLOAD_PLUGIN_TASKS_DB 环境变量覆盖库路径（DOWNLOAD_TASKS_DB/
VEIN_UPLOAD_DB/A2A_TASKS_DB 同款先例），缺省 <repo>/data/download_plugin_tasks.db；
库路径每次连接按 env 解析（模块级 manager 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（与 download_tasks/a2a_tasks 同族状态快照表）：(plugin, task_id) 复合
PRIMARY KEY，同任务每次变更 upsert 覆盖旧行=「当前态」语义（一个任务一行）；
plugin 列=插件类名（与「每插件类独立注册表」同轴命名空间，禁跨插件共享行）。

诚实边界：
①写失败不阻断传输本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——传输
  已发生，为落盘失败炸掉下载不成比例；代价是故障窗口内该态只活内存、重启丢
  （如实记，非谎报 durable）。
②回载失败=空注册表起步+logger.error 可见，行真源仍在库下次触碰重试；不在回载
  故障上发明降级判法。
③行内单条不可解（payload 坏行/state 值非法）跳过该行并 logger.error 可见，不因
  单行坏数据丢整库任务（禁静默丢：跳过必留日志真因）。
④进程手柄（_RunningDownload.proc）不持久化也无法持久化——重启后在途任务的子
  进程已随 service 进程消亡，回载侧如实把 'downloading' 行降为 'paused'（续传
  上下文齐备走真续传口），禁谎报 downloading（async 结果面 _async_results「记录
  若活过重启会对着已消失的下载谎报」同款纪律）。
⑤无老化/裁剪面：表只增不减（任务行被 upsert 覆盖不膨胀；cancel/终态删行口在
  DownloadPlugin 侧真消费本模块 delete()，任务清即删行）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "download_plugin_tasks"

_COLUMNS = (
    "plugin",
    "task_id",
    "state",
    "payload",
)


def default_db_path() -> str:
    """库路径：DOWNLOAD_PLUGIN_TASKS_DB 环境变量优先（测试隔离），缺省
    <repo>/data/download_plugin_tasks.db。"""
    env = os.environ.get("DOWNLOAD_PLUGIN_TASKS_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "download_plugin_tasks.db")


class DownloadPluginTaskPersistence:
    """插件任务行持久化真源（单表 download_plugin_tasks：upsert 写穿 + load_all 懒回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 manager 单例跨用例存活，测试面每用例 tmp
        # 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④⑥-2 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "plugin TEXT NOT NULL, "
            "task_id TEXT NOT NULL, "
            "state TEXT NOT NULL, "
            "payload TEXT NOT NULL, "
            "PRIMARY KEY (plugin, task_id))"
        )
        return conn

    def upsert(self, *, plugin: str, task_id: str, state: str, payload: str) -> None:
        """写穿一行任务当前态（状态快照表语义：同 (plugin, task_id) upsert 覆盖=当前态）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（DownloadPlugin
        侧=记账旁路纪律：logger.error 不阻断传输本体）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(plugin, task_id) DO UPDATE SET "
                    "state=excluded.state, payload=excluded.payload",
                    (str(plugin), str(task_id), str(state), str(payload)),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, *, plugin: str, task_id: str) -> None:
        """删一行任务（cancel/终态口真消费；幂等：不存在=无操作不抛）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"DELETE FROM {_TABLE} WHERE plugin = ? AND task_id = ?",
                    (str(plugin), str(task_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def load_all(self, plugin: str | None = None) -> list[dict]:
        """回载任务行（json.loads payload 后与列合并的 dict 列表，rowid 升序=稳定回载序）。

        plugin 参数=按插件类名过滤（每插件类只回载自己的注册表行）；None=全量
        （取证/探针回读口）。

        回载只读不建库：库文件缺失=空任务起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④⑥-2 同纪律）。
        单行不可解（json 坏行/非对象/缺列）跳过该行并 logger.error 可见（诚实边界③）。
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
                if plugin is None:
                    rows = conn.execute(
                        f"SELECT plugin, task_id, state, payload FROM {_TABLE} ORDER BY rowid"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT plugin, task_id, state, payload FROM {_TABLE}"
                        " WHERE plugin = ? ORDER BY rowid",
                        (str(plugin),),
                    ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row_plugin, row_task_id, row_state, payload in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as e:
                logger.error(f"download plugin 任务行 payload 不可解跳过: {e}")
                continue
            if not isinstance(data, dict):
                logger.error("download plugin 任务行 payload 非对象跳过")
                continue
            out.append(
                {
                    "plugin": row_plugin,
                    "task_id": row_task_id,
                    "state": row_state,
                    "url": data.get("url"),
                    "dest": data.get("dest"),
                    "resume": data.get("resume"),
                }
            )
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
