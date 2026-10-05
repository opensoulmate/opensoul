import logging
import os
import re
from pathlib import Path

import casbin

logger = logging.getLogger(__name__)

_config_dir = Path(__file__).parent.parent.parent / "config"
_model_path = str(_config_dir / "rbac_model.conf")
# git跟踪的种子模板（只读播种源，运行期绝不写回——见 _seed_runtime_policy）
_seed_policy_path = str(_config_dir / "rbac_policy.csv")
# 运行时策略=数据目录（~/.hermes/opensoul/），与git脱钩：测试/探针/真实注册都不许弄脏工作区
_RUNTIME_POLICY_ENV = "OPENSOUL_RBAC_POLICY_PATH"
_default_runtime_policy = Path("~/.hermes/opensoul/rbac_policy.csv")

# 测试产物用户名（tests/test_user.py生成的uuid后缀名）——保存前剔除、播种时剔除（产生侧清理）
_TEST_USER_RE = re.compile(r"^(testuser|logintest|metest)_[0-9a-f]{8}$")

_enforcer: casbin.Enforcer | None = None


def _is_test_user(subject: str) -> bool:
    """测试产物用户名判定（单一真源：播种剔除与保存剔除共用）。"""
    return bool(_TEST_USER_RE.match(subject))


def _resolve_policy_path() -> str:
    """运行时策略路径：env覆盖 > 数据目录默认（绝不落回git目录）。"""
    override = os.environ.get(_RUNTIME_POLICY_ENV)
    if override:
        return override
    return str(_default_runtime_policy.expanduser())


def _seed_runtime_policy(path: str) -> None:
    """运行时策略文件缺失时，从git种子模板播种（剔除测试产物行）。

    种子模板=只读origin（kilocode skill供应链"origin钉死"同款哲学）：运行期
    增删改角色只写数据目录副本，config/rbac_policy.csv 永不被写回→git工作区
    不再被测试/注册流量弄脏（2026-10-06清账前该文件积300+测试行）。
    """
    p = Path(path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if os.path.exists(_seed_policy_path):
        with open(_seed_policy_path, encoding="utf-8") as f:
            for line in f:
                parts = [x.strip() for x in line.split(",")]
                if len(parts) >= 2 and parts[0] == "g" and _is_test_user(parts[1]):
                    continue
                lines.append(line)
    else:
        logger.warning("RBAC seed template missing: %s", _seed_policy_path)
    p.write_text("".join(lines), encoding="utf-8")
    logger.info("Casbin runtime policy seeded at %s (from %s)", path, _seed_policy_path)


def get_enforcer() -> casbin.Enforcer:
    global _enforcer
    if _enforcer is None:
        policy_path = _resolve_policy_path()
        _seed_runtime_policy(policy_path)
        _enforcer = casbin.Enforcer(_model_path, policy_path)
        logger.info("Casbin enforcer initialized (policy=%s)", policy_path)
    return _enforcer


def check_permission(username: str, obj: str, act: str) -> bool:
    """检查用户是否有权限"""
    enforcer = get_enforcer()
    return enforcer.enforce(username, obj, act)


def add_role(username: str, role: str):
    """给用户添加角色"""
    enforcer = get_enforcer()
    enforcer.add_role_for_user(username, role)
    _save_policy()


def remove_role(username: str, role: str):
    """移除用户角色"""
    enforcer = get_enforcer()
    enforcer.delete_role_for_user(username, role)
    _save_policy()


def get_user_roles(username: str) -> list[str]:
    """获取用户角色"""
    enforcer = get_enforcer()
    return enforcer.get_roles_for_user(username)


def add_policy(sub: str, obj: str, act: str):
    """添加策略"""
    enforcer = get_enforcer()
    enforcer.add_policy(sub, obj, act)
    _save_policy()


def remove_policy(sub: str, obj: str, act: str):
    """删除策略"""
    enforcer = get_enforcer()
    enforcer.remove_policy(sub, obj, act)
    _save_policy()


def get_all_policies() -> list[list[str]]:
    """获取所有策略"""
    enforcer = get_enforcer()
    return enforcer.get_policy()


def get_all_roles() -> list[list[str]]:
    """获取所有角色映射"""
    enforcer = get_enforcer()
    return enforcer.get_grouping_policy()


def _save_policy():
    """保存策略到文件（落盘前剔除测试产物行——产生侧清理，测试用户名不进运行时策略）"""
    enforcer = get_enforcer()
    pruned = 0
    for row in list(enforcer.get_grouping_policy()):
        if len(row) >= 2 and _is_test_user(row[0]):
            if enforcer.remove_grouping_policy(*row):
                pruned += 1
    if pruned:
        logger.info("Pruned %d test-user rows before policy save", pruned)
    enforcer.save_policy()
    logger.info("Casbin policy saved")
