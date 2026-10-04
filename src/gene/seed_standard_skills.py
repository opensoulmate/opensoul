"""gene标准技能种子落库 — 集成修复#1：skill_learner.discovered=173但learned_skills=0。

数据源：~/.agents/skills/目录（P3-①标准对齐机制识别的standard技能）。
只落库真实存在的标准技能文件，不合成虚假执行记录。
动态增长由cron执行链的extract上报驱动（cron prompt第7步）。
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

SKILLS_DB = Path.home() / ".hermes" / "opensoul" / "gene" / "skills.db"
AGENT_SKILLS_DIR = Path.home() / ".agents" / "skills"


def _parse_frontmatter(md_path: Path) -> dict:
    """解析SKILL.md的YAML frontmatter（name/description）"""
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    meta = {}
    if text.startswith("---"):
        lines = text[3:].split("---")[0].splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if ":" in line and not line.startswith((" ", "\t")):
                k, _, v = line.partition(":")
                v = v.strip()
                if v in ("|", ">"):  # 多行YAML block scalar
                    block = []
                    i += 1
                    while i < len(lines) and lines[i].startswith((" ", "\t")):
                        block.append(lines[i].strip())
                        i += 1
                    meta[k.strip()] = " ".join(block)
                    continue
                meta[k.strip()] = v
            i += 1
    return meta


def seed_standard_skills() -> list[dict]:
    """把.agents/skills标准技能注册进gene skill库"""
    if not AGENT_SKILLS_DIR.exists():
        return []

    seeded = []
    with sqlite3.connect(str(SKILLS_DB)) as conn:
        for skill_dir in sorted(AGENT_SKILLS_DIR.iterdir()):
            md = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not md.exists():
                continue
            meta = _parse_frontmatter(md)
            name = meta.get("name", skill_dir.name)
            desc = meta.get("description", "")[:200]
            skill_id = (
                "std_"
                + hashlib.sha256(f"agent_standard:{skill_dir.name}".encode()).hexdigest()[:12]
            )
            now = time.time()
            conn.execute(
                """INSERT OR IGNORE INTO learned_skills
                   (skill_id, name, description, category, code_template,
                    parameters, tags, created_at, source_session, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill_id,
                    name,
                    desc,
                    "agent_standard",
                    f".agents/skills/{skill_dir.name}/SKILL.md",
                    json.dumps(["skill_dir"]),
                    json.dumps(["agent_standard", skill_dir.name]),
                    now,
                    "agent_standard_sync",
                    # 来源provenance（learned_skills.source列）：种子落库≠执行提炼，
                    # 重播种前可按source='seed'整批清（purge_by_source）
                    "seed",
                ),
            )
            seeded.append({"skill_id": skill_id, "name": name, "dir": skill_dir.name})
        conn.commit()
    return seeded


if __name__ == "__main__":
    result = seed_standard_skills()
    print(json.dumps({"seeded": len(result), "skills": result}, ensure_ascii=False, indent=2))
