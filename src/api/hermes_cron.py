"""Hermes cron job API endpoints.

DEC-⑦-⑦ cron 面加轴：写点行归属（/create bind_identity 派生租户→
stamp_job_tenant 落行键）+by-ID 改/删闸（pause/resume/run 跨租户→403
CronTenantDenied 行零改写；delete 跨租户=零副作用与 not-found 同契约 404
不差分泄露行存在性，⑦-⑥ 同款）。身份与读面判法单一源=src.services.cron_store。

DEC-⑦-⑧ 管理读面加轴+引用判法单一源：/list 按行租户键过滤（过滤先于 total，
加性键 tenant_id=行归属可观测）、/get /history by-ID 归属闸（跨租户=与
not-found 同契约 404 不差分泄露行存在性）；job 引用解析收口 cron_store.
cron_job_row 单一源（精确 ID→独一 name→独一前缀，与 hermes CLI
resolve_job_ref 对齐）——修前闸门只认精确 id，用作业 name 引用可绕写闸
（闸查无行→放行交 CLI→CLI 按名执行=跨租户改删，wired-but-not-functional）。
"""

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user
from src.services.cron_store import (
    CronTenantDenied,
    cron_job_row,
    cron_row_tenant,
    load_cron_jobs,
    require_cron_row_tenant,
    stamp_job_tenant,
)
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def hermes_cron_health():
    """HermesCron health check."""
    return {"status": "ok", "component": "HermesCron"}


class CronJobCreate(BaseModel):
    schedule: str
    prompt: str = ""
    name: str = ""
    deliver: str = ""
    skill: list[str] = []
    workdir: str = ""
    model: str = ""
    provider: str = ""


async def run_hermes_cron(*args: str) -> dict:
    """Run a hermes cron command and return parsed output."""
    proc = await asyncio.create_subprocess_exec(
        "hermes",
        "cron",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    return {
        "output": stdout.decode("utf-8", errors="replace").strip(),
        "error": stderr.decode("utf-8", errors="replace").strip() if proc.returncode != 0 else None,
        "exit_code": proc.returncode,
    }


def _gate_by_id(job_id: str, authorization) -> None:
    """by-ID 改/删闸：行归属租户≠请求者派生租户→CronTenantDenied（行零改写）。

    引用解析=cron_job_row 单一源（精确 ID→独一 name→独一前缀，与 CLI
    resolve_job_ref 对齐——DEC-⑦-⑧ 修前只认精确 id，name 引用绕闸真断点）。
    缺行/歧义照旧交 CLI（既有 not-found/CLI 错误契约不变，且 CLI 对歧义
    name 自身 raise AmbiguousJobReference 拒执行）；行在才闸=不误伤。
    """
    row = cron_job_row(job_id)
    if row is None:
        return
    identity = bind_identity(authorization)
    require_cron_row_tenant(row, identity.tenant_id)


def _visible_jobs(parsed_jobs: list, tenant_id: str) -> list:
    """管理读面过滤单一收口（DEC-⑦-⑧）：CLI 解析行按真源行归属键过滤。

    - parsed 行→真源行匹配走 cron_job_row 单一源（禁第二套判法）。
    - 归属键=cron_row_tenant 单一判法，≠请求者租户→隐藏（三面互斥）。
    - parsed 行匹配不到真源行=归属不可判→隐藏（fail-closed：不可判的行可能
      属他租户，宁缺勿泄）+logger.warning 可见（禁静默）。
    - 加性键 tenant_id=行归属可观测（⑦ faces 同款）；total=过滤后计数
      （过滤先于计数，⑦-① limit 只计可见同款）。
    """
    visible = []
    for job in parsed_jobs:
        row = cron_job_row(str(job.get("id", "")))
        if row is None:
            logger.warning(
                "cron list: parsed job %r has no resolvable store row, hidden",
                job.get("id"),
            )
            continue
        row_tenant = cron_row_tenant(row)
        if row_tenant != tenant_id:
            continue
        visible.append({**job, "tenant_id": row_tenant})
    return visible


@router.get("/list")
async def list_cron_jobs(
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """List Hermes cron jobs.（DEC-⑦-⑧ 按行租户键过滤：恒按 bind_identity
    派生租户过滤，过滤先于 total；加性键 tenant_id）"""
    try:
        result = await run_hermes_cron("list")
        output = result["output"]

        # Parse the table output
        jobs = []
        current_job = {}

        for line in output.split("\n"):
            line = line.strip()
            if not line or line.startswith("┌") or line.startswith("└") or line.startswith("│"):
                if "Scheduled Jobs" in line:
                    continue
                continue

            # New job starts with hex ID
            if len(line) > 12 and line[12] == " " and "[" in line:
                if current_job:
                    jobs.append(current_job)
                job_id = line[:12].strip()
                status = (
                    "active"
                    if "[active]" in line
                    else "paused"
                    if "[paused]" in line
                    else "unknown"
                )
                current_job = {"id": job_id, "status": status}
            elif current_job:
                if line.startswith("Name:"):
                    current_job["name"] = line[5:].strip()
                elif line.startswith("Schedule:"):
                    current_job["schedule"] = line[9:].strip()
                elif line.startswith("Next run:"):
                    current_job["next_run"] = line[9:].strip()
                elif line.startswith("Last run:"):
                    parts = line[9:].strip().split("  ")
                    current_job["last_run"] = parts[0].strip() if parts else ""
                elif line.startswith("Deliver:"):
                    current_job["deliver"] = line[8:].strip()
                elif line.startswith("Prompt:"):
                    current_job["prompt"] = line[7:].strip()

        if current_job:
            jobs.append(current_job)

        identity = bind_identity(authorization)
        jobs = _visible_jobs(jobs, identity.tenant_id)
        return {"jobs": jobs, "total": len(jobs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/pause")
async def pause_cron_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Pause a cron job.（DEC-⑦-⑦ 跨租户改闸：403 行零改写）"""
    try:
        _gate_by_id(job_id, authorization)
    except CronTenantDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        result = await run_hermes_cron("pause", job_id)
        return {"ok": True, "output": result["output"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/resume")
async def resume_cron_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Resume a paused cron job.（DEC-⑦-⑦ 跨租户改闸：403 行零改写）"""
    try:
        _gate_by_id(job_id, authorization)
    except CronTenantDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        result = await run_hermes_cron("resume", job_id)
        return {"ok": True, "output": result["output"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/run")
async def run_cron_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Run a cron job immediately.（DEC-⑦-⑦ 跨租户触发闸：403 零执行）"""
    try:
        _gate_by_id(job_id, authorization)
    except CronTenantDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        result = await run_hermes_cron("run", job_id)
        return {"ok": True, "output": result["output"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{job_id}")
async def delete_cron_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Delete a cron job.（DEC-⑦-⑦ delete 归属闸：跨租户=零副作用与 not-found
    同契约 404 不差分泄露行存在性，⑦-⑥ 同款）"""
    try:
        _gate_by_id(job_id, authorization)
    except CronTenantDenied:
        raise HTTPException(status_code=404, detail="Cron job not found")
    try:
        result = await run_hermes_cron("remove", job_id)
        return {"ok": True, "output": result["output"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_cron_job(
    body: CronJobCreate,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Create a new cron job.（DEC-⑦-⑦ 写点行归属：bind_identity 派生租户→
    stamp_job_tenant 落行键；行 id=创建前后集差（恰 1 个新行才落键，歧义/
    未见=如实不猜不落键→行按 cron_row_tenant 兜底 'default'，键派生故障
    绝不吃掉落库）"""
    try:
        identity = bind_identity(authorization)
        ids_before = {str(j.get("id")) for j in load_cron_jobs()}
        args = ["create"]
        if body.name:
            args += ["--name", body.name]
        if body.deliver:
            args += ["--deliver", body.deliver]
        for s in body.skill:
            args += ["--skill", s]
        if body.workdir:
            args += ["--workdir", body.workdir]
        if body.model:
            args += ["--model", body.model]
        if body.provider:
            args += ["--provider", body.provider]
        args.append(body.schedule)
        if body.prompt:
            args.append(body.prompt)
        result = await run_hermes_cron(*args)
        if result["exit_code"] != 0:
            raise HTTPException(status_code=400, detail=result["error"] or "Failed to create job")
        new_ids = [str(j.get("id")) for j in load_cron_jobs() if str(j.get("id")) not in ids_before]
        job_id = new_ids[0] if len(new_ids) == 1 else None
        tenant_stamped = bool(job_id and stamp_job_tenant(job_id, identity.tenant_id))
        if job_id and not tenant_stamped:
            logger.warning(
                "cron create: tenant stamp missed (job_id=%s tenant=%s)",
                job_id,
                identity.tenant_id,
            )
        return {
            "ok": True,
            "output": result["output"],
            "job_id": job_id,
            "tenant_id": identity.tenant_id,
            "tenant_stamped": tenant_stamped,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}")
async def get_cron_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Get a single cron job by ID/name.（DEC-⑦-⑧ 读面归属闸：跨租户=与
    not-found 同契约 404 不差分泄露行存在性（delete 闸 ⑦-⑦ 同款理由）；
    引用解析=cron_job_row 单一源（精确 ID->独一 name->独一前缀，与写闸同一
    判法；歧义->404 如实不猜，修前前缀静默取首匹配=歧义错配））"""
    try:
        row = cron_job_row(job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        identity = bind_identity(authorization)
        row_tenant = cron_row_tenant(row)
        if row_tenant != identity.tenant_id:
            raise HTTPException(status_code=404, detail="Job not found")
        list_result = await run_hermes_cron("list")
        output = list_result["output"]
        jobs = []
        current_job = {}
        for line in output.split("\n"):
            line = line.strip()
            if not line or line.startswith(("┌", "└", "│")):
                continue
            if len(line) > 12 and line[12] == " " and "[" in line:
                if current_job:
                    jobs.append(current_job)
                jid = line[:12].strip()
                status = (
                    "active"
                    if "[active]" in line
                    else "paused"
                    if "[paused]" in line
                    else "unknown"
                )
                current_job = {"id": jid, "status": status}
            elif current_job:
                if line.startswith("Name:"):
                    current_job["name"] = line[5:].strip()
                elif line.startswith("Schedule:"):
                    current_job["schedule"] = line[9:].strip()
                elif line.startswith("Next run:"):
                    current_job["next_run"] = line[9:].strip()
                elif line.startswith("Last run:"):
                    current_job["last_run"] = line[9:].strip().split("  ")[0].strip()
                elif line.startswith("Deliver:"):
                    current_job["deliver"] = line[8:].strip()
                elif line.startswith("Prompt:"):
                    current_job["prompt"] = line[7:].strip()
        if current_job:
            jobs.append(current_job)
        row_id = str(row.get("id"))
        for j in jobs:
            if j["id"] == row_id:
                return {**j, "tenant_id": row_tenant}
        raise HTTPException(status_code=404, detail="Job not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}/history")
async def get_cron_job_history(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    authorization: str | None = Header(None),
):
    """Get execution history for a cron job.（DEC-⑦-⑧ 读面归属闸：跨租户/缺行
    =与 not-found 同契约 404 不差分泄露行存在性；修前缺行 200 空集与跨租户
    形状可分=存在性差分，同契约收口如实记）"""
    try:
        row = cron_job_row(job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        identity = bind_identity(authorization)
        if cron_row_tenant(row) != identity.tenant_id:
            raise HTTPException(status_code=404, detail="Job not found")
        result = await run_hermes_cron("runs", job_id)
        output = result["output"]
        runs = []
        current_run = {}
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "status=" in line or "Status:" in line:
                if current_run:
                    runs.append(current_run)
                current_run = {"raw": line}
                if "success" in line.lower():
                    current_run["status"] = "success"
                elif "fail" in line.lower():
                    current_run["status"] = "failed"
                else:
                    current_run["status"] = "unknown"
            elif current_run:
                current_run["raw"] = current_run.get("raw", "") + "\n" + line
        if current_run:
            runs.append(current_run)
        return {"runs": runs, "total": len(runs), "raw": output}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
