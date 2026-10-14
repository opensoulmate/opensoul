"""DEC-⑦-⑦ cron 面存储层加轴：hermes cron store（jobs.json）单一读写源。

修前（HEAD 源码实证）两断点：
- _search_cron_jobs 走 db_pool `SELECT * FROM cron_jobs`——全仓零建表零写方
  =幽灵表，查询恒异常→except 空集（wired-but-not-functional，DEC-⑦-⑤
  agents.json 幽灵源同型）；真源=/api/cron 管理面背后的 hermes cron store
  （<hermes home>/cron/jobs.json，hermes CLI 读写的同一文件）。
- 行无租户键、写点（/create）零身份轴、pause/resume/run/delete 按 job_id
  跨租户改删零闸。

本模块是 cron 面单一源（写读同一判法，F4b-㉗ 纪律）：
- cron_row_tenant：行归属判法，自带键缺失/空白→'default' 兜底（键派生故障
  绝不吃掉落库；hermes CLI 外部写方无身份轴不带键→'default'，③b-2 外部
  共享写方同款）。
- stamp_job_tenant：写点行归属落键（/create bind_identity 派生租户）。跨进程
  互斥=hermes 同款 <cron dir>/.jobs.lock flock（cron/jobs.py _jobs_lock_file
  协议同文件），load-patch-atomic-replace，防与 hermes 调度器/CLI 并发写丢更新。
- load_cron_jobs：真源读取。文件缺失=无作业（空列表，真态）；JSON 损坏如实
  抛（禁静默空集——故障必须可见，调用方自行降级）。

诚实边界：
- 租户键写入面只有 opensoul /api/cron/create（hermes CLI create 无租户元数据
  透传口）；CLI 建的作业无键→'default'（外部写方语义）。
- hermes 保存周期对未知键的保留属 hermes 内部行为（load_jobs 原样 dict、
  _apply_skill_fields 返回 copy 不洗键、save_jobs 原样序列化——实测留证）；
  若 hermes 未来改写为白名单序列化，键会静默回落 'default' 兜底（判法保证
  绝不吃掉落库，行为仍正确只是归属退化）。
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "default"

# hermes 跨进程互斥锁文件（<cron dir>/.jobs.lock，hermes cron/jobs.py
# _jobs_lock_file 同文件同语义）。
_LOCK_NAME = ".jobs.lock"
_LOCK_WAIT_S = 5.0
_LOCK_POLL_S = 0.05


class CronTenantDenied(Exception):
    """跨租户改/删 hermes cron 作业被拒（⑦-③④⑤ 同款家族异常）。"""


def cron_jobs_path() -> Path:
    """jobs.json 路径：HERMES_CRON_JOBS_FILE 测试隔离优先（NEST_DB 同款先例）。

    缺省与 hermes 同源：HERMES_HOME/cron/jobs.json → ~/.hermes/cron/jobs.json。
    """
    override = os.environ.get("HERMES_CRON_JOBS_FILE")
    if override:
        return Path(override)
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "cron" / "jobs.json"


def load_cron_jobs() -> list[dict]:
    """读真源作业行。文件缺失=空列表（无作业真态）；JSON 损坏如实抛。"""
    path = cron_jobs_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        raise ValueError(f"malformed cron store: {path} (jobs not a list)")
    return [j for j in jobs if isinstance(j, dict)]


def cron_row_tenant(row: dict) -> str:
    """行归属判法单一源（写读同源，⑦-① _event_row_tenant ~ ⑥ event_row_tenant 同款）。

    自带键缺失/空白→'default' 兜底；键在则按键（strip 后非空串）。
    """
    key = row.get("tenant_id")
    if isinstance(key, str):
        key = key.strip()
        if key:
            return key
    return DEFAULT_TENANT


def cron_job_row(job_id: str) -> dict | None:
    """按 id 取真源行（缺行=None，如实不 fabricate）。"""
    for row in load_cron_jobs():
        if str(row.get("id")) == str(job_id):
            return row
    return None


def require_cron_row_tenant(row: dict, tenant_id: str) -> None:
    """by-ID 改/删闸：行归属租户≠请求者租户→CronTenantDenied（行零改写）。"""
    row_tenant = cron_row_tenant(row)
    if row_tenant != tenant_id:
        raise CronTenantDenied(f"cron job belongs to tenant {row_tenant!r}, not {tenant_id!r}")


def _lock_path() -> Path:
    return cron_jobs_path().parent / _LOCK_NAME


def stamp_job_tenant(job_id: str, tenant_id: str) -> bool:
    """写点行归属：给真源行落 tenant_id 键（load-patch-atomic-replace）。

    跨进程互斥=hermes 同款 .jobs.lock flock（防与调度器/CLI 并发写丢更新；
    自己 load 自己 patch=无需 hermes 的 merge 协议，互斥下读改写原子）。
    行缺失→False 如实（不 fabricate）；锁等待超时如实抛（禁静默不落键）。
    """
    path = cron_jobs_path()
    if not path.exists():
        return False
    lock_file = _lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_file, "a+", encoding="utf-8")
    try:
        deadline = time.monotonic() + _LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"cron store lock busy: {lock_file} (tenant stamp skipped)")
                time.sleep(_LOCK_POLL_S)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            raise ValueError(f"malformed cron store: {path} (jobs not a list)")
        hit = None
        for row in jobs:
            if isinstance(row, dict) and str(row.get("id")) == str(job_id):
                hit = row
                break
        if hit is None:
            return False
        hit["tenant_id"] = tenant_id
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".jobs_stamp_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            fd.close()
