"""统一错误响应（C2 2026-10-11）：查询失败禁谎报ok。

全API审计（脚本扫描 src/api/*.py 全部 except 块）发现 6 个 stats 端点
（agent/entity/graph/knowledge/search/workflow）在异常时返回
``{"status": "ok", ...全0}``——监控看到 ok 以为健康，"0 条数据"把故障伪装成
"没数据"，正是谎报。统一降级形状：

    {"status": "error", "component": <组件名>, "error": <异常摘要>, ...兼容零值字段}

约定：
- status 必须是 "error"，error 必填（异常类型: 消息，禁空串）；
- 兼容零值字段（total_* 等）只为前端取数不炸，禁用它推断真实值；
- HTTP 保持 200：沿用 diagnostics/search 既有降级惯例（in-body 报错），
  让面板直接把故障原因显示出来，而不是被非 200 吞掉。
"""


def stats_error(component: str, exc: BaseException, **compat_fields) -> dict:
    """stats 端点统一降级回包：说失败、说原因，禁说 ok。"""
    msg = str(exc).strip() or "(no message)"
    return {
        "status": "error",
        "component": component,
        "error": f"{type(exc).__name__}: {msg}",
        **compat_fields,
    }
