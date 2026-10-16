"""外部回调凭据判定单一源（DEC-⑬ / DEC-⑲ 共用，禁两套判法漂移）。

claim 回调身份必须持 register 签发 callback_token（F3b-④/⑤ 同族
"claim 注册身份必须持 register 签发 token"）。两个消费面共用本判定：
- src/api/link_gateway.py _callback_token_ok（DEC-⑬ heartbeat/incoming）
- src/api/link.py receive_webhook（DEC-⑲ OpenLink connector webhook 入口）

判定语义收敛在本模块一处：hmac 恒时比较；缺失/空白/不匹配一律 False
（fail-closed：无证可验=拒）。register 恒签发 token=生产不可达"无证"行。
"""

import hmac


def callback_token_ok(row_token: str, presented: str) -> bool:
    """比对 register 签发的回调凭据（恒时比较，fail-closed）。"""
    if not row_token or not presented:
        return False
    return hmac.compare_digest(str(row_token), str(presented))
