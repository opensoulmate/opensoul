"""OpenMind API — 心智中心：情绪识别、人格调节。"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.mind.emotion import EmotionAnalyzer
from src.mind.emotion_decision import EMOTION_WEIGHTS, EmotionDrivenDecision
from src.mind.personality import PersonalityManager
from src.mind.preference_learner import PreferenceLearner
from src.services.tenant_binding import bind_identity

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
emotion_analyzer = EmotionAnalyzer()
emotion_decision = EmotionDrivenDecision()
personality_mgr = PersonalityManager()
preference_learner = PreferenceLearner()


# ── Request Schemas ────────────────────────────────────────


class EmotionAnalyzeRequest(BaseModel):
    text: str


class PersonalityCreateRequest(BaseModel):
    personality_id: str = ""
    name: str
    description: str = ""
    tone: str = "neutral"
    language_style: str = "normal"
    emoji_usage: str = "moderate"
    response_length: str = "normal"
    traits: list[str] = []
    system_prompt_suffix: str = ""


class PersonalityUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tone: str | None = None
    language_style: str | None = None
    emoji_usage: str | None = None
    response_length: str | None = None
    traits: list[str] | None = None
    system_prompt_suffix: str | None = None


class SetActiveRequest(BaseModel):
    personality_id: str


class BuildPromptRequest(BaseModel):
    personality_id: str = ""
    base_prompt: str = ""


# ── Emotion Endpoints ──────────────────────────────────────


@router.post("/emotion/analyze")
async def analyze_emotion(req: EmotionAnalyzeRequest):
    """Analyze emotion in text."""
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")
    if len(req.text) > 5000:
        raise HTTPException(400, "Text too long (max 5000 characters)")

    result = emotion_analyzer.analyze(req.text)
    return {
        "primary_emotion": result.primary_emotion,
        "confidence": result.confidence,
        "emotions": result.emotions,
        "valence": result.valence,
        "arousal": result.arousal,
        "sentiment": result.sentiment,
        "keywords": result.keywords,
        "elapsed_ms": result.elapsed_ms,
    }


@router.get("/emotion/keywords")
async def emotion_keywords():
    """List all emotion categories and their keywords."""
    from src.mind.emotion import _EMOTION_LEXICON

    return {
        "emotions": {
            name: {
                "keyword_count": len(config["keywords"]),
                "sample_keywords": config["keywords"][:5],
                "valence": config["valence"],
                "arousal": config["arousal"],
            }
            for name, config in _EMOTION_LEXICON.items()
        }
    }


@router.get("/emotion/sensitivity")
async def emotion_sensitivity(emotion: str | None = None):
    """情绪→决策权重观测口（F4b-㉛ 接线——修前 get_emotion_sensitivity 全仓零消费）。

    与 /emotion/keywords 同族：mind 情绪面观测口。权重取数走
    EmotionDrivenDecision.get_emotion_sensitivity 单一源（决策面 adjust_decision
    同一取数口）；未收录情绪回落 neutral 如实报 matched/resolved 不冒充。
    """
    if emotion is not None:
        weights = emotion_decision.get_emotion_sensitivity(emotion)
        matched = emotion in EMOTION_WEIGHTS
        return {
            "emotion": emotion,
            "matched": matched,
            "resolved": emotion if matched else "neutral",
            "weights": weights,
        }
    return {
        "emotions": {
            name: emotion_decision.get_emotion_sensitivity(name) for name in EMOTION_WEIGHTS
        }
    }


# ── Personality Endpoints ──────────────────────────────────


@router.get("/personalities")
async def list_personalities():
    """List all personalities."""
    return {
        "personalities": [
            {
                "personality_id": p.personality_id,
                "name": p.name,
                "description": p.description,
                "tone": p.tone,
                "language_style": p.language_style,
                "emoji_usage": p.emoji_usage,
                "response_length": p.response_length,
                "traits": p.traits,
                "builtin": p.builtin,
                "usage_count": p.usage_count,
            }
            for p in personality_mgr.list_all()
        ],
        "active": personality_mgr._active,
    }


@router.get("/personalities/active")
async def get_active_personality():
    """Get the currently active personality."""
    p = personality_mgr.get_active()
    return {
        "personality_id": p.personality_id,
        "name": p.name,
        "description": p.description,
        "tone": p.tone,
        "traits": p.traits,
        "system_prompt": personality_mgr.build_system_prompt(),
    }


@router.post("/personalities/active")
async def set_active_personality(req: SetActiveRequest):
    """Set the active personality."""
    if not personality_mgr.set_active(req.personality_id):
        raise HTTPException(404, f"Personality '{req.personality_id}' not found")
    p = personality_mgr.get_active()
    return {"message": "active personality set", "personality_id": p.personality_id, "name": p.name}


@router.get("/personalities/{personality_id}")
async def get_personality(personality_id: str):
    """Get a personality."""
    p = personality_mgr.get(personality_id)
    if not p:
        raise HTTPException(404, "Personality not found")
    return {
        "personality_id": p.personality_id,
        "name": p.name,
        "description": p.description,
        "tone": p.tone,
        "language_style": p.language_style,
        "emoji_usage": p.emoji_usage,
        "response_length": p.response_length,
        "traits": p.traits,
        "system_prompt_suffix": p.system_prompt_suffix,
        "builtin": p.builtin,
    }


@router.post("/personalities")
async def create_personality(req: PersonalityCreateRequest):
    """Create a new personality."""
    p = personality_mgr.create(req.model_dump())
    return {"personality_id": p.personality_id, "name": p.name}


@router.patch("/personalities/{personality_id}")
async def update_personality(personality_id: str, req: PersonalityUpdateRequest):
    """Update a personality."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not personality_mgr.update(personality_id, updates):
        raise HTTPException(404, "Personality not found or is built-in")
    return {"message": "updated"}


@router.delete("/personalities/{personality_id}")
async def delete_personality(personality_id: str):
    """Delete a user personality."""
    if not personality_mgr.delete(personality_id):
        raise HTTPException(400, "Cannot delete: not found or is built-in")
    return {"message": "deleted"}


@router.post("/personalities/build-prompt")
async def build_prompt(req: BuildPromptRequest):
    """Build a system prompt with personality traits."""
    prompt = personality_mgr.build_system_prompt(
        personality_id=req.personality_id,
        base_prompt=req.base_prompt,
    )
    return {"system_prompt": prompt}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def mind_stats():
    """OpenMind detailed statistics."""
    all_personalities = personality_mgr.list_all()
    by_tone = {}
    for p in all_personalities:
        by_tone[p.tone] = by_tone.get(p.tone, 0) + 1

    return {
        "status": "ok",
        "component": "OpenMind",
        "emotion": emotion_analyzer.stats(),
        "personality": personality_mgr.stats(),
        "by_tone": by_tone,
        "active_personality": personality_mgr._active,
    }


# ── Preference Learning ────────────────────────────────────


class LearnPreferencesRequest(BaseModel):
    tenant_id: str = "default"
    user_id: str = "default"
    messages: list[dict]


@router.post("/preference/learn")
async def learn_preferences(req: LearnPreferencesRequest, authorization: str | None = Header(None)):
    """Learn user preferences from conversation messages."""
    # F3b-② 身份派生收口：tenant/user 声明只可确认绑定不得矛盾（冒读面收口）。
    # 诚实边界：本 handler 的偏好学习是全局面（preference_learner 单例不分域），
    # 两字段目前不消费数据面——收口在声明面，数据面隔离=F3b-③ 另拍。
    bind_identity(authorization, req.tenant_id, req.user_id)
    all_learned = []
    for msg in req.messages:
        content = msg.get("content", "")
        if content:
            learned = preference_learner.learn_from_message(content)
            all_learned.extend(learned)
    return {
        "learned_count": len(all_learned),
        "preferences": [
            {
                "category": p.category,
                "value": p.value,
                "confidence": p.confidence,
                "source": p.source,
            }
            for p in all_learned
        ],
    }


@router.get("/preference/list")
async def list_preferences():
    """List all learned preferences."""
    prefs = preference_learner.get_preferences()
    return {
        "preferences": [
            {
                "category": p.get("category", ""),
                "value": p.get("value", ""),
                "confidence": p.get("confidence", 0.0),
                "source": p.get("source", ""),
                "usage_count": p.get("usage_count", 0),
            }
            for p in prefs
        ],
        "count": len(prefs),
    }


@router.get("/preference/context")
async def preference_context():
    """Get preferences formatted for system prompt injection."""
    return {"context": preference_learner.get_context_prompt()}


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def mind_health():
    """OpenMind health check."""
    return {
        "status": "ok",
        "component": "OpenMind",
        "emotion": emotion_analyzer.stats(),
        "personality": personality_mgr.stats(),
        "preferences": preference_learner.get_stats(),
    }
