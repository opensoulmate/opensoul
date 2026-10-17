import json
from uuid import UUID, uuid4

from src.database.postgres import db_pool
from src.models.entity import GraphData, GraphEdge, GraphNode, RelationCreate
from src.services.extraction import extract_entities_and_relations


def _props(raw) -> dict:
    """properties 列存 TEXT JSON（写侧 json.dumps），API 契约 properties: dict——
    读侧解析为 dict 并兼容存量 NULL 行（services/entity.py C3-⑪ 同款根因收口）。
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    if raw is None:
        return {}
    return raw


def _row_to_relation(row) -> dict:
    d = dict(row)
    d["source_entity_id"] = d.pop("source_id")
    d["target_entity_id"] = d.pop("target_id")
    # DB column mapping: the relations table uses "type"（SQLite main.py DDL 真源，
    # entities.type 同款）。We rename on read so the API contract stays
    # `relation_type`（services/entity.py 对 entity_type 的同款约定）。
    d["relation_type"] = d.pop("type", "")
    d["properties"] = _props(d.get("properties"))
    return d


async def create_relation(data: RelationCreate) -> dict:
    import uuid as _uuid

    rel_id = str(_uuid.uuid4())
    await db_pool.execute(
        # DB 真源列名=type（relations 表 SQLite DDL），API 契约 relation_type
        # 在 _row_to_relation 读侧映射（entity.py 同款约定）。
        "INSERT INTO relations (id, source_id, target_id, type, properties) "
        "VALUES ($1, $2, $3, $4, $5)",
        rel_id,
        str(data.source_entity_id),
        str(data.target_entity_id),
        data.relation_type,
        # json.dumps 铁律：dict 禁直接绑参（entity.py create_entity 同款）。
        json.dumps(data.properties or {}),
    )
    row = await db_pool.fetchrow("SELECT * FROM relations WHERE id = $1", rel_id)
    return _row_to_relation(row)


async def list_relations(user_id: UUID, offset: int = 0, limit: int = 100) -> list[dict]:
    rows = await db_pool.fetch(
        "SELECT r.* FROM relations r "
        "JOIN entities e ON r.source_id = e.id "
        "WHERE e.user_id = $1 "
        "ORDER BY r.created_at DESC LIMIT $3 OFFSET $2",
        str(user_id),
        offset,
        limit,
    )
    return [_row_to_relation(r) for r in rows]


async def get_graph(user_id: UUID, depth: int = 2, entity_id: UUID | None = None) -> GraphData:
    if entity_id:
        visited_entities: set[str] = set()
        visited_relations: set[str] = set()
        # UUID/TEXT 比较坑：SQLite 行值恒 TEXT——遍历键入队前统一 str 化，
        # 比较/去重恒同型（接线前定形收口，修前出边邻居被误判成自身）。
        queue: list[tuple[str, int]] = [(str(entity_id), 0)]
        nodes = []
        edges = []

        while queue:
            current_id, current_depth = queue.pop(0)
            current_key = str(current_id)
            if current_key in visited_entities or current_depth > depth:
                continue
            visited_entities.add(current_key)

            entity = await db_pool.fetchrow(
                "SELECT * FROM entities WHERE id = $1 AND user_id = $2",
                str(current_id),
                str(user_id),
            )
            if entity:
                nodes.append(
                    GraphNode(
                        id=entity["id"],
                        label=entity["name"],
                        node_type=entity["type"],
                        properties=_props(entity["properties"]),
                    )
                )
                relations = await db_pool.fetch(
                    "SELECT * FROM relations WHERE source_id = $1 OR target_id = $1",
                    str(current_id),
                )
                for rel in relations:
                    rel_id = rel["id"]
                    if rel_id not in visited_relations:
                        visited_relations.add(rel_id)
                        target = (
                            rel["target_id"]
                            if str(rel["source_id"]) == current_key
                            else rel["source_id"]
                        )
                        edges.append(
                            GraphEdge(
                                source=rel["source_id"],
                                target=rel["target_id"],
                                relation_type=rel["type"],
                                properties=_props(rel["properties"]),
                            )
                        )
                        if current_depth + 1 <= depth:
                            queue.append((target, current_depth + 1))
    else:
        entities = await db_pool.fetch(
            "SELECT * FROM entities WHERE user_id = $1 ORDER BY name LIMIT 200", str(user_id)
        )
        entity_ids = {e["id"] for e in entities}
        nodes = [
            GraphNode(
                id=e["id"], label=e["name"], node_type=e["type"], properties=_props(e["properties"])
            )
            for e in entities
        ]

        if entity_ids:
            relations = await db_pool.fetch(
                "SELECT * FROM relations WHERE source_id = ANY($1) AND target_id = ANY($1)",
                [str(eid) for eid in entity_ids],
            )
            edges = [
                GraphEdge(
                    source=r["source_id"],
                    target=r["target_id"],
                    relation_type=r["type"],
                    properties=_props(r["properties"]),
                )
                for r in relations
            ]
        else:
            edges = []

    return GraphData(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Knowledge graph construction from NER results
# ---------------------------------------------------------------------------


async def build_graph_from_text(text: str, user_id: UUID) -> GraphData:
    """Extract entities and relations from text and build the knowledge graph."""
    extracted = await extract_entities_and_relations(text)

    # Resolve or create entities
    entity_name_to_id: dict[str, UUID] = {}
    for ent in extracted.get("entities", []):
        name = ent["name"].strip()
        if not name:
            continue

        existing = await db_pool.fetchrow(
            "SELECT id FROM entities WHERE user_id = $1 AND name = $2",
            str(user_id),
            name,
        )
        if existing:
            entity_name_to_id[name] = existing["id"]
        else:
            entity_id = uuid4()
            await db_pool.execute(
                "INSERT INTO entities (id, user_id, name, type, description, properties) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                str(entity_id),
                str(user_id),
                name,
                ent.get("type", "other"),
                ent.get("description", ""),
                # json.dumps 铁律：dict 禁直接绑参（entity.py create_entity 同款）。
                json.dumps({}),
            )
            entity_name_to_id[name] = entity_id

    # Create relations
    for rel in extracted.get("relations", []):
        source_name = rel["source"].strip()
        target_name = rel["target"].strip()
        source_id = entity_name_to_id.get(source_name)
        target_id = entity_name_to_id.get(target_name)

        if not source_id or not target_id:
            continue

        # Skip duplicate relations（DB 真源列名=type，_row_to_relation 读侧映射）
        exists = await db_pool.fetchrow(
            "SELECT id FROM relations WHERE source_id = $1 AND target_id = $2 AND type = $3",
            str(source_id),
            str(target_id),
            rel.get("relation_type", "related"),
        )
        if not exists:
            await db_pool.execute(
                "INSERT INTO relations (id, source_id, target_id, type, properties) "
                "VALUES ($1, $2, $3, $4, $5)",
                str(uuid4()),
                str(source_id),
                str(target_id),
                rel.get("relation_type", "related"),
                # json.dumps 铁律：dict 禁直接绑参。
                json.dumps({}),
            )

    return await get_graph(user_id)


# ---------------------------------------------------------------------------
# Graph query: entity neighbors and path analysis
# ---------------------------------------------------------------------------


async def get_entity_neighbors(user_id: UUID, entity_id: UUID) -> dict:
    """Get all directly connected entities and their relation types."""
    entity = await db_pool.fetchrow(
        "SELECT * FROM entities WHERE id = $1 AND user_id = $2", str(entity_id), str(user_id)
    )
    if not entity:
        return {"entity": None, "neighbors": []}

    relations = await db_pool.fetch(
        "SELECT * FROM relations WHERE source_id = $1 OR target_id = $1", str(entity_id)
    )
    neighbors = []
    anchor = str(entity_id)
    for rel in relations:
        # UUID/TEXT 比较坑：SQLite 行值恒 TEXT——统一 str 比较
        # （修前方向/邻居恒误判，接线前定形收口）。
        outgoing = str(rel["source_id"]) == anchor
        neighbor_id = rel["target_id"] if outgoing else rel["source_id"]
        direction = "outgoing" if outgoing else "incoming"
        neighbor = await db_pool.fetchrow(
            # 用户边界：邻居行必须同属查询者（接线前定形——跨用户邻居不暴露）。
            "SELECT id, name, type FROM entities WHERE id = $1 AND user_id = $2",
            str(neighbor_id),
            str(user_id),
        )
        if neighbor:
            neighbors.append(
                {
                    "entity_id": neighbor["id"],
                    "name": neighbor["name"],
                    "type": neighbor["type"],
                    "relation_type": rel["type"],
                    "direction": direction,
                }
            )

    return {
        "entity": {"id": entity["id"], "name": entity["name"], "type": entity["type"]},
        "neighbors": neighbors,
    }


async def find_path(
    user_id: UUID, source_id: UUID, target_id: UUID, max_depth: int = 5
) -> list[list[dict]]:
    """Find all paths between two entities using BFS (up to max_depth).

    语义如实标注：关系按无向遍历（既有实现语义，source/target 双向可达）；
    max_depth=路径节点数上限（含起终点，实现既有语义）；
    返回最多 10 条路径；起点/终点同实体无环路径=空（禁硬造自环）。
    """
    paths: list[list[dict]] = []
    source = await db_pool.fetchrow(
        # 用户边界：起点必须是查询者自己的实体（接线前定形）。
        "SELECT id, name, type FROM entities WHERE id = $1 AND user_id = $2",
        str(source_id),
        str(user_id),
    )
    if not source:
        return []
    # 步形状单一源：每步恒 4 键（首步 via_relation=None），禁步间键集漂移。
    start_step = {
        "entity_id": source["id"],
        "name": source["name"],
        "type": source["type"],
        "via_relation": None,
    }
    queue: list[tuple[str, list[dict]]] = [(str(source_id), [start_step])]

    while queue:
        current_id, path = queue.pop(0)

        if len(path) > max_depth:
            continue

        if current_id == str(target_id) and len(path) > 1:
            paths.append(path)
            continue

        visited_in_path = {str(step["entity_id"]) for step in path}

        relations = await db_pool.fetch(
            "SELECT * FROM relations WHERE source_id = $1 OR target_id = $1", str(current_id)
        )
        for rel in relations:
            # UUID/TEXT 比较坑：统一 str 比较（修前出边邻居被误判成自身=遍历残废）。
            neighbor_id = (
                rel["target_id"] if str(rel["source_id"]) == str(current_id) else rel["source_id"]
            )
            if str(neighbor_id) not in visited_in_path:
                neighbor = await db_pool.fetchrow(
                    # 用户边界：只在查询者自己的实体间找路（接线前定形）。
                    "SELECT id, name, type FROM entities WHERE id = $1 AND user_id = $2",
                    str(neighbor_id),
                    str(user_id),
                )
                if neighbor:
                    new_step = {
                        "entity_id": neighbor["id"],
                        "name": neighbor["name"],
                        "type": neighbor["type"],
                        "via_relation": rel["type"],
                    }
                    queue.append((str(neighbor_id), path + [new_step]))

    return paths[:10]  # Cap at 10 paths
