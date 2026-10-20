"""Agent detection & install API - comprehensive registry of 50+ agents."""

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.user import get_current_user
from src.api.ws_chat import which_agent
from src.services.agent_registry import (
    AgentTenantDenied,
)
from src.services.agent_registry import (
    list_registered as list_registered_agents,
)
from src.services.agent_registry import (
    probe_agent as acp_probe_agent,
)
from src.services.tenant_binding import bind_identity

router = APIRouter()


@router.get("/health")
async def agents_health():
    """Agents health check."""
    return {"status": "ok", "component": "Agents"}


logger = logging.getLogger(__name__)


def _get_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    elif system == "windows":
        return "win32"
    return "linux"


# ─── Comprehensive Agent Registry (50+) ──────────────────────────

AGENT_REGISTRY = {
    # ── CLI Coding Agents ──
    "hermes": {
        "name": "Hermes Agent",
        "binary": "hermes",
        "description": "Nous Research Hermes Agent - 开源AI助手框架",
        "logo": "https://avatars.githubusercontent.com/u/143723048?s=48",
        "icon": "🏛️",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages hermes-agent",
            "darwin": "pip install --break-system-packages hermes-agent",
            "win32": "pip install --break-system-packages hermes-agent",
        },
    },
    "claude": {
        "name": "Claude Code",
        "binary": "claude",
        "description": "Anthropic Claude Code CLI - 终端AI编程",
        "icon": "🟣",
        "category": "coding",
        "install": {
            "linux": "npm install -g @anthropic-ai/claude-code",
            "darwin": "npm install -g @anthropic-ai/claude-code",
            "win32": "npm install -g @anthropic-ai/claude-code",
        },
    },
    "codex": {
        "name": "Codex CLI",
        "binary": "codex",
        "description": "OpenAI Codex CLI - GPT驱动的编程助手",
        "icon": "🟢",
        "category": "coding",
        "install": {
            "linux": "npm install -g @openai/codex",
            "darwin": "npm install -g @openai/codex",
            "win32": "npm install -g @openai/codex",
        },
    },
    "mimo": {
        "name": "MiMo Code",
        "binary": "mimo",
        "description": "小米 MiMo Code CLI - 中文优化编程",
        "icon": "📱",
        "category": "coding",
        "install": {
            "linux": "npm install -g @anthropic-ai/claude-code",
            "darwin": "npm install -g @anthropic-ai/claude-code",
            "win32": "npm install -g @anthropic-ai/claude-code",
        },
    },
    "opencode": {
        "name": "OpenCode",
        "binary": "opencode",
        "description": "开源AI编程助手",
        "icon": "⚡",
        "category": "coding",
        "install": {
            "linux": "go install github.com/opencode-ai/opencode@latest",
            "darwin": "brew install opencode",
            "win32": "go install github.com/opencode-ai/opencode@latest",
        },
    },
    "aider": {
        "name": "Aider",
        "binary": "aider",
        "description": "AI结对编程 - 终端内编辑代码",
        "icon": "🤝",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages aider-chat",
            "darwin": "brew install aider",
            "win32": "pip install --break-system-packages aider-chat",
        },
    },
    "gemini": {
        "name": "Gemini CLI",
        "binary": "gemini",
        "description": "Google Gemini CLI - 多模态AI编程",
        "icon": "🔵",
        "category": "coding",
        "install": {
            "linux": "npm install -g @google/gemini-cli",
            "darwin": "npm install -g @google/gemini-cli",
            "win32": "npm install -g @google/gemini-cli",
        },
    },
    "copilot": {
        "name": "GitHub Copilot",
        "binary": "gh",
        "description": "GitHub Copilot CLI - 代码补全与对话",
        "icon": "🐙",
        "category": "coding",
        "install": {
            "linux": "gh extension install github/gh-copilot",
            "darwin": "gh extension install github/gh-copilot",
            "win32": "gh extension install github/gh-copilot",
        },
    },
    "amazon-q": {
        "name": "Amazon Q",
        "binary": "q",
        "description": "Amazon Q Developer - AWS AI编程助手",
        "icon": "☁️",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages amazon-q-developer-cli",
            "darwin": "brew install amazon-q",
            "win32": "pip install --break-system-packages amazon-q-developer-cli",
        },
    },
    "qwen": {
        "name": "Qwen Coder",
        "binary": "qwen",
        "description": "通义千问编程版 - 阿里AI编程",
        "icon": "🟠",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages qwen-cli",
            "darwin": "pip install --break-system-packages qwen-cli",
            "win32": "pip install --break-system-packages qwen-cli",
        },
    },
    "deepseek": {
        "name": "DeepSeek",
        "binary": "deepseek",
        "description": "DeepSeek AI - 深度推理编程",
        "icon": "🐋",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages deepseek-cli",
            "darwin": "pip install --break-system-packages deepseek-cli",
            "win32": "pip install --break-system-packages deepseek-cli",
        },
    },
    "devin": {
        "name": "Devin",
        "binary": "devin",
        "description": "Devin AI - 自主软件工程师",
        "icon": "🤖",
        "category": "coding",
        "install": {
            "linux": "https://devin.ai",
            "darwin": "https://devin.ai",
            "win32": "https://devin.ai",
        },
    },
    "open-interpreter": {
        "name": "Open Interpreter",
        "binary": "interpreter",
        "description": "本地代码执行 - 自然语言控制计算机",
        "icon": "💻",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages open-interpreter",
            "darwin": "pip install --break-system-packages open-interpreter",
            "win32": "pip install --break-system-packages open-interpreter",
        },
    },
    "gptme": {
        "name": "GPTMe",
        "binary": "gptme",
        "description": "终端AI助手 - 本地执行代码",
        "icon": "🧠",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages gptme",
            "darwin": "pip install --break-system-packages gptme",
            "win32": "pip install --break-system-packages gptme",
        },
    },
    "mentat": {
        "name": "Mentat",
        "binary": "mentat",
        "description": "AI编程助手 - 多文件编辑",
        "icon": "🧙",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages mentat",
            "darwin": "pip install --break-system-packages mentat",
            "win32": "pip install --break-system-packages mentat",
        },
    },
    "sweep": {
        "name": "Sweep",
        "binary": "sweep",
        "description": "AI Junior Dev - 自动修bug和写功能",
        "icon": "🧹",
        "category": "coding",
        "install": {
            "linux": "pip install --break-system-packages sweepai",
            "darwin": "pip install --break-system-packages sweepai",
            "win32": "pip install --break-system-packages sweepai",
        },
    },
    "cursor-agent": {
        "name": "Cursor",
        "binary": "cursor",
        "description": "Cursor IDE内置AI Agent",
        "icon": "▶️",
        "category": "ide",
        "install": {
            "linux": "https://cursor.sh",
            "darwin": "https://cursor.sh",
            "win32": "https://cursor.sh",
        },
    },
    "windsurf": {
        "name": "Windsurf",
        "binary": "windsurf",
        "description": "Codeium Windsurf - AI驱动IDE",
        "icon": "🏄",
        "category": "ide",
        "install": {
            "linux": "https://windsurf.ai",
            "darwin": "https://windsurf.ai",
            "win32": "https://windsurf.ai",
        },
    },
    "kiro": {
        "name": "Kiro",
        "binary": "kiro",
        "description": "AWS Kiro - AI原生IDE",
        "icon": "🎯",
        "category": "ide",
        "install": {
            "linux": "https://kiro.dev",
            "darwin": "https://kiro.dev",
            "win32": "https://kiro.dev",
        },
    },
    "cline": {
        "name": "Cline",
        "binary": "cline",
        "description": "VS Code AI编程助手",
        "icon": "🔧",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    "roo": {
        "name": "Roo Code",
        "binary": "roo",
        "description": "Roo Code AI助手",
        "icon": "🦘",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    "kilo": {
        "name": "Kilo Code",
        "binary": "kilo",
        "description": "Kilo Code AI助手",
        "icon": "⚡",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    "continue": {
        "name": "Continue",
        "binary": "continue",
        "description": "开源AI代码助手",
        "icon": "🔄",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    "tabby": {
        "name": "Tabby",
        "binary": "tabby",
        "description": "自托管AI编程助手",
        "icon": "📋",
        "category": "ide",
        "install": {
            "linux": "https://tabby.tabbyml.com",
            "darwin": "https://tabby.tabbyml.com",
            "win32": "https://tabby.tabbyml.com",
        },
    },
    "supermaven": {
        "name": "Supermaven",
        "binary": "supermaven",
        "description": "超快AI代码补全",
        "icon": "🚀",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    "codeium": {
        "name": "Codeium",
        "binary": "codeium",
        "description": "免费AI代码补全",
        "icon": "🌊",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    "codium": {
        "name": "CodiumAI",
        "binary": "codium",
        "description": "AI测试生成器",
        "icon": "✅",
        "category": "ide",
        "install": {
            "linux": "VS Code Extension",
            "darwin": "VS Code Extension",
            "win32": "VS Code Extension",
        },
    },
    # ── Chat & General AI ──
    "ollama": {
        "name": "Ollama",
        "binary": "ollama",
        "description": "本地大模型推理 - 一键运行Llama/Qwen",
        "icon": "🦙",
        "category": "chat",
        "install": {
            "linux": "curl -fsSL https://ollama.com/install.sh | sh",
            "darwin": "brew install ollama",
            "win32": "https://ollama.com/download",
        },
    },
    "lmstudio": {
        "name": "LM Studio",
        "binary": "lmstudio",
        "description": "本地大模型GUI - 可视化管理",
        "icon": "🖥️",
        "category": "chat",
        "install": {
            "linux": "https://lmstudio.ai",
            "darwin": "https://lmstudio.ai",
            "win32": "https://lmstudio.ai",
        },
    },
    "jan": {
        "name": "Jan",
        "binary": "jan",
        "description": "开源本地AI聊天 - 隐私优先",
        "icon": "🔵",
        "category": "chat",
        "install": {
            "linux": "https://jan.ai",
            "darwin": "https://jan.ai",
            "win32": "https://jan.ai",
        },
    },
    "open-webui": {
        "name": "Open WebUI",
        "binary": "open-webui",
        "description": "Ollama Web界面 - 类ChatGPT",
        "icon": "🌐",
        "category": "chat",
        "install": {
            "linux": "pip install --break-system-packages open-webui",
            "darwin": "pip install --break-system-packages open-webui",
            "win32": "pip install --break-system-packages open-webui",
        },
    },
    "chatbox": {
        "name": "ChatBox",
        "binary": "chatbox",
        "description": "多模型AI聊天客户端",
        "icon": "💬",
        "category": "chat",
        "install": {
            "linux": "https://chatboxai.app",
            "darwin": "https://chatboxai.app",
            "win32": "https://chatboxai.app",
        },
    },
    "lobe-chat": {
        "name": "LobeChat",
        "binary": "lobe-chat",
        "description": "开源AI聊天框架 - 插件丰富",
        "icon": "🧠",
        "category": "chat",
        "install": {
            "linux": "docker run -p 3210:3210 lobehub/lobe-chat",
            "darwin": "docker run -p 3210:3210 lobehub/lobe-chat",
            "win32": "docker run -p 3210:3210 lobehub/lobe-chat",
        },
    },
    "dify": {
        "name": "Dify",
        "binary": "dify",
        "description": "LLM应用开发平台 - 工作流编排",
        "icon": "🔮",
        "category": "chat",
        "install": {
            "linux": "docker compose up -d",
            "darwin": "docker compose up -d",
            "win32": "docker compose up -d",
        },
    },
    "flowise": {
        "name": "Flowise",
        "binary": "flowise",
        "description": "可视化LLM工作流编排",
        "icon": "🌊",
        "category": "chat",
        "install": {
            "linux": "npm install -g flowise",
            "darwin": "npm install -g flowise",
            "win32": "npm install -g flowise",
        },
    },
    "n8n-ai": {
        "name": "n8n AI",
        "binary": "n8n",
        "description": "n8n AI工作流 - 400+集成",
        "icon": "⚙️",
        "category": "workflow",
        "install": {
            "linux": "docker run -p 5678:5678 n8nio/n8n",
            "darwin": "docker run -p 5678:5678 n8nio/n8n",
            "win32": "docker run -p 5678:5678 n8nio/n8n",
        },
    },
    # ── Research & Knowledge ──
    "perplexity": {
        "name": "Perplexity CLI",
        "binary": "pplx",
        "description": "AI搜索引擎CLI",
        "icon": "🔍",
        "category": "research",
        "install": {
            "linux": "pip install --break-system-packages perplexity-cli",
            "darwin": "pip install --break-system-packages perplexity-cli",
            "win32": "pip install --break-system-packages perplexity-cli",
        },
    },
    "phind": {
        "name": "Phind",
        "binary": "phind",
        "description": "AI编程搜索引擎",
        "icon": "🔎",
        "category": "research",
        "install": {
            "linux": "https://phind.com",
            "darwin": "https://phind.com",
            "win32": "https://phind.com",
        },
    },
    "you-cli": {
        "name": "You.com CLI",
        "binary": "you",
        "description": "You.com AI搜索CLI",
        "icon": "🌐",
        "category": "research",
        "install": {
            "linux": "pip install --break-system-packages you-cli",
            "darwin": "pip install --break-system-packages you-cli",
            "win32": "pip install --break-system-packages you-cli",
        },
    },
    # ── Automation & Agents ──
    "autogpt": {
        "name": "AutoGPT",
        "binary": "autogpt",
        "description": "自主AI Agent - 自动完成任务",
        "icon": "🤖",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages autogpt",
            "darwin": "pip install --break-system-packages autogpt",
            "win32": "pip install --break-system-packages autogpt",
        },
    },
    "crewai": {
        "name": "CrewAI",
        "binary": "crewai",
        "description": "多Agent协作框架",
        "icon": "👥",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages crewai",
            "darwin": "pip install --break-system-packages crewai",
            "win32": "pip install --break-system-packages crewai",
        },
    },
    "autogen": {
        "name": "AutoGen",
        "binary": "autogen",
        "description": "微软多Agent对话框架",
        "icon": "🔄",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages pyautogen",
            "darwin": "pip install --break-system-packages pyautogen",
            "win32": "pip install --break-system-packages pyautogen",
        },
    },
    "langchain": {
        "name": "LangChain",
        "binary": "langchain",
        "description": "LLM应用开发框架",
        "icon": "🦜",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages langchain",
            "darwin": "pip install --break-system-packages langchain",
            "win32": "pip install --break-system-packages langchain",
        },
    },
    "llamaindex": {
        "name": "LlamaIndex",
        "binary": "llamaindex",
        "description": "数据连接LLM框架 - RAG",
        "icon": "🦙",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages llama-index",
            "darwin": "pip install --break-system-packages llama-index",
            "win32": "pip install --break-system-packages llama-index",
        },
    },
    "semantic-kernel": {
        "name": "Semantic Kernel",
        "binary": "sk",
        "description": "微软AI编排框架",
        "icon": "🧠",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages semantic-kernel",
            "darwin": "pip install --break-system-packages semantic-kernel",
            "win32": "pip install --break-system-packages semantic-kernel",
        },
    },
    "haystack": {
        "name": "Haystack",
        "binary": "haystack",
        "description": "deepset NLP/RAG框架",
        "icon": "🌾",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages haystack-ai",
            "darwin": "pip install --break-system-packages haystack-ai",
            "win32": "pip install --break-system-packages haystack-ai",
        },
    },
    # ── DevOps & Infra ──
    "k9s": {
        "name": "K9s",
        "binary": "k9s",
        "description": "Kubernetes终端管理UI",
        "icon": "☸️",
        "category": "devops",
        "install": {
            "linux": "brew install k9s",
            "darwin": "brew install k9s",
            "win32": "choco install k9s",
        },
    },
    "lazydocker": {
        "name": "LazyDocker",
        "binary": "lazydocker",
        "description": "Docker终端管理UI",
        "icon": "🐳",
        "category": "devops",
        "install": {
            "linux": "brew install lazydocker",
            "darwin": "brew install lazydocker",
            "win32": "https://github.com/jesseduffield/lazydocker",
        },
    },
    "terraform": {
        "name": "Terraform",
        "binary": "terraform",
        "description": "基础设施即代码",
        "icon": "🏗️",
        "category": "devops",
        "install": {
            "linux": "brew install terraform",
            "darwin": "brew install terraform",
            "win32": "choco install terraform",
        },
    },
    "ansible": {
        "name": "Ansible",
        "binary": "ansible",
        "description": "自动化运维工具",
        "icon": "📜",
        "category": "devops",
        "install": {
            "linux": "pip install --break-system-packages ansible",
            "darwin": "pip install --break-system-packages ansible",
            "win32": "pip install --break-system-packages ansible",
        },
    },
    # ── AI Agent Frameworks ──
    "openclaw": {
        "name": "OpenClaw",
        "binary": "openclaw",
        "description": "开源AI Agent框架 - 可扩展自主Agent",
        "icon": "🦞",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages openclaw",
            "darwin": "pip install --break-system-packages openclaw",
            "win32": "pip install --break-system-packages openclaw",
        },
    },
    "pi-agent": {
        "name": "Pi Agent",
        "binary": "pi",
        "description": "Inflection AI Pi - 个人AI助手",
        "icon": "🥧",
        "category": "chat",
        "install": {
            "linux": "pip install --break-system-packages pi-agent",
            "darwin": "pip install --break-system-packages pi-agent",
            "win32": "pip install --break-system-packages pi-agent",
        },
    },
    "openai-agents": {
        "name": "OpenAI Agents SDK",
        "binary": "agents",
        "description": "OpenAI Agents SDK - 多Agent编排框架",
        "icon": "🤖",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages openai-agents",
            "darwin": "pip install --break-system-packages openai-agents",
            "win32": "pip install --break-system-packages openai-agents",
        },
    },
    "smolagents": {
        "name": "SmolAgents",
        "binary": "smolagents",
        "description": "HuggingFace轻量Agent框架 - 代码优先",
        "icon": "🤗",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages smolagents",
            "darwin": "pip install --break-system-packages smolagents",
            "win32": "pip install --break-system-packages smolagents",
        },
    },
    "pydantic-ai": {
        "name": "PydanticAI",
        "binary": "pydantic-ai",
        "description": "Pydantic类型安全Agent框架",
        "icon": "📐",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages pydantic-ai",
            "darwin": "pip install --break-system-packages pydantic-ai",
            "win32": "pip install --break-system-packages pydantic-ai",
        },
    },
    "letta": {
        "name": "Letta",
        "binary": "letta",
        "description": "Letta(原MemGPT) - 长期记忆AI Agent",
        "icon": "🧬",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages letta",
            "darwin": "pip install --break-system-packages letta",
            "win32": "pip install --break-system-packages letta",
        },
    },
    "browser-use": {
        "name": "Browser Use",
        "binary": "browser-use",
        "description": "AI浏览器自动化Agent - 智能网页操作",
        "icon": "🌐",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages browser-use",
            "darwin": "pip install --break-system-packages browser-use",
            "win32": "pip install --break-system-packages browser-use",
        },
    },
    "mastra": {
        "name": "Mastra",
        "binary": "mastra",
        "description": "TypeScript AI Agent框架 - LLM编排",
        "icon": "⚡",
        "category": "automation",
        "install": {
            "linux": "npm install -g @mastra/core",
            "darwin": "npm install -g @mastra/core",
            "win32": "npm install -g @mastra/core",
        },
    },
    "composio": {
        "name": "Composio",
        "binary": "composio",
        "description": "Agent工具集成平台 - 250+工具连接",
        "icon": "🔌",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages composio-core",
            "darwin": "pip install --break-system-packages composio-core",
            "win32": "pip install --break-system-packages composio-core",
        },
    },
    "agentstack": {
        "name": "AgentStack",
        "binary": "agentstack",
        "description": "AI Agent快速开发框架 - 脚手架工具",
        "icon": "🏗️",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages agentstack",
            "darwin": "pip install --break-system-packages agentstack",
            "win32": "pip install --break-system-packages agentstack",
        },
    },
    "phidata": {
        "name": "Phidata",
        "binary": "phi",
        "description": "Phidata AI Agent框架 - 多模态Agent",
        "icon": "💎",
        "category": "automation",
        "install": {
            "linux": "pip install --break-system-packages phidata",
            "darwin": "pip install --break-system-packages phidata",
            "win32": "pip install --break-system-packages phidata",
        },
    },
}

_install_tasks: dict[str, dict] = {}


_detect_cache: dict | None = None
_detect_cache_time: float = 0
_DETECT_CACHE_TTL = 300  # 5 minutes


@router.get("/detect")
async def detect_agents(authorization: str | None = Header(None)):
    """检测本机安装的AI Agent (结果缓存5分钟)

    B3打通：session_agents 与 /api/agents/registry、/api/agents/probe 同表一条真源
    （探测即注册）。装没装=目录信息；能否开会话=真ACP探测后的注册状态。
    DEC-⑦-⑤：session_agents 恒按行租户键过滤（tenant=bind_identity 派生禁自报，
    匿名 'default'——单机存量行全 'default'，契约不变）；安装目录扫描=机器级
    目录信息无行键，保持系统视图（诚实边界）。
    """
    global _detect_cache, _detect_cache_time
    import time

    identity = bind_identity(authorization)
    now = time.time()
    if not _detect_cache or (now - _detect_cache_time) >= _DETECT_CACHE_TTL:
        os_name = _get_os()
        result = []
        for agent_id, info in AGENT_REGISTRY.items():
            # which_agent：PATH+用户安装目录（~/.local/bin 等）——裸 shutil.which
            # 在 systemd 服务 PATH 盲区会把已安装 agent 误报 available=False
            path = which_agent(info["binary"])
            version = None
            if path:
                # Only probe version for installed agents (skip to avoid 2s timeout per missing binary)
                try:
                    r = subprocess.run(
                        [path, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        env={**os.environ, "NO_COLOR": "1"},
                    )
                    version = r.stdout.strip()[:50] or None
                except Exception as exc:
                    logging.getLogger(__name__).debug("probe skipped: %s", exc)
            result.append(
                {
                    "id": agent_id,
                    "name": info["name"],
                    "binary": info["binary"],
                    "description": info["description"],
                    "icon": info["icon"],
                    "category": info.get("category", "other"),
                    "available": path is not None,
                    "version": version,
                    "path": path,
                    "installCommand": info["install"].get(os_name),
                    "os": os_name,
                }
            )
        _detect_cache = {"os": os_name, "agents": result, "total": len(result)}
        _detect_cache_time = now
    # session_agents 每次现读注册表（一条真源，不进安装目录缓存）——
    # DEC-⑦-⑤：恒按派生租户过滤。
    return {
        **_detect_cache,
        "session_agents": list_registered_agents(tenant_id=identity.tenant_id),
    }


class ProbeRequest(BaseModel):
    agent_id: str
    cmd: list[str] | None = None
    verify_session: bool = True


@router.post("/probe")
async def probe_agent_endpoint(req: ProbeRequest, authorization: str | None = Header(None)):
    """探测即注册（B3）：L1真ACP握手（acp-proxy /agents/probe）+ L2真开会话
    （ws session/new 拿到 sessionId），两级全过才注册——检测成功=必能开会话。
    verify_session=false 只做连通性体检，不改注册状态。
    DEC-⑦-⑤：写点行归属=bind_identity 派生租户流入 _record（匿名 'default'
    契约不变，禁自报）；跨租户同 agent_id 重探测=劫持→AgentTenantDenied→403
    行零改写。
    """
    identity = bind_identity(authorization)
    try:
        result = await acp_probe_agent(
            req.agent_id,
            cmd=req.cmd,
            verify_session=req.verify_session,
            tenant_id=identity.tenant_id,
        )
    except AgentTenantDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return result


@router.get("/registry")
async def agent_registry_list(authorization: str | None = Header(None)):
    """已注册（可开会话）agent 列表——未注册不进列表。
    与 /detect.session_agents、/probe 同表一条真源。
    DEC-⑦-⑤：恒按行租户键过滤（tenant=bind_identity 派生禁自报，匿名 'default'
    契约不变）；行 dict 带加性 tenant_id 键（归一值=agent_row_tenant）。"""
    identity = bind_identity(authorization)
    rows = list_registered_agents(tenant_id=identity.tenant_id)
    return {"agents": rows, "total": len(rows)}


class InstallRequest(BaseModel):
    agent_id: str


@router.post("/install")
async def start_install(req: InstallRequest, user_id: UUID = Depends(get_current_user)):
    """Start installing an agent in background."""
    if req.agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {req.agent_id}"}
    agent = AGENT_REGISTRY[req.agent_id]
    os_name = _get_os()
    cmd = agent["install"].get(os_name)
    if not cmd:
        return {"success": False, "error": f"No install command for {os_name}"}
    if req.agent_id in _install_tasks and _install_tasks[req.agent_id]["status"] == "running":
        return {"success": True, "task_id": req.agent_id, "status": "already_running"}
    _install_tasks[req.agent_id] = {"status": "running", "progress": 0, "output": [], "error": None}
    asyncio.create_task(_run_install(req.agent_id, cmd))
    return {"success": True, "task_id": req.agent_id, "status": "started"}


@router.get("/install/{agent_id}/progress")
async def get_install_progress(agent_id: str, user_id: UUID = Depends(get_current_user)):
    """SSE endpoint for real-time install progress."""
    if agent_id not in AGENT_REGISTRY:
        return {"error": "Unknown agent"}

    async def event_stream():
        last_idx = 0
        while True:
            task = _install_tasks.get(agent_id)
            if not task:
                yield f"data: {json.dumps({'status': 'unknown'})}\n\n"
                break
            if len(task["output"]) > last_idx:
                for line in task["output"][last_idx:]:
                    yield f"data: {json.dumps({'status': 'running', 'line': line, 'progress': task['progress']})}\n\n"
                last_idx = len(task["output"])
            if task["status"] in ("done", "error"):
                yield f"data: {json.dumps({'status': task['status'], 'error': task.get('error'), 'progress': 100 if task['status'] == 'done' else task['progress']})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/install/status")
async def get_all_install_status(user_id: UUID = Depends(get_current_user)):
    """Get status of all running/recent installations."""
    return {
        aid: {
            "status": t["status"],
            "progress": t["progress"],
            "line_count": len(t["output"]),
            "error": t.get("error"),
        }
        for aid, t in _install_tasks.items()
    }


class UninstallRequest(BaseModel):
    agent_id: str


@router.post("/uninstall")
async def uninstall_agent(req: UninstallRequest, user_id: UUID = Depends(get_current_user)):
    """Uninstall an agent"""
    if req.agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {req.agent_id}"}
    agent = AGENT_REGISTRY[req.agent_id]
    path = which_agent(agent["binary"])
    if not path:
        return {"success": False, "error": "Agent not installed"}
    cmds = []
    if shutil.which("npm"):
        cmds.append(f"npm uninstall -g {agent['binary']}")
    if shutil.which("pip"):
        cmds.append(f"pip uninstall -y {agent['binary']}")
    for cmd in cmds:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0:
                return {"success": True, "output": stdout.decode()[-300:]}
        except Exception:
            continue
    return {"success": False, "error": "Could not uninstall. Try manually: " + path}


class UpdateRequest(BaseModel):
    agent_id: str


def _upgrade_cmd(cmd: str | None) -> str | None:
    """安装命令 → 真升级命令（语义正确，禁把重装冒充升级）。

    - pip 不带 --upgrade 重装只报 "Requirement already satisfied" 原地不动 → 必加 --upgrade
    - npm install -g 重装即解析最新版 → 显式 @latest 锁死升级语义
    - brew install → brew upgrade 子命令
    - go install …@latest / curl 安装器本身就是"最新版"语义 → 原样跑
    - 官网下载页、docker run、gh extension 这类无干净命令行升级面 → 诚实回 None，
      禁假跑一遍安装命令冒充升级成功
    """
    if not cmd:
        return None
    c = cmd.strip()
    if c.startswith(("http://", "https://")) or c.startswith(("docker ", "gh extension ")):
        return None
    if "pip install" in c:
        return c.replace("pip install", "pip install --upgrade", 1)
    if "npm install" in c:
        tokens = c.split()
        for i in range(len(tokens) - 1, -1, -1):
            tok = tokens[i]
            if tok.startswith("-") or tok.startswith("http"):
                continue
            base = tok.rsplit("@", 1)[0] if "@" in tok[1:] else tok
            tokens[i] = base + "@latest"
            break
        return " ".join(tokens)
    if c.startswith("brew install"):
        return c.replace("brew install", "brew upgrade", 1)
    return c


@router.post("/update")
async def update_agent(req: UpdateRequest, user_id: UUID = Depends(get_current_user)):
    """升级已安装 agent 到最新版（升级命令≠安装命令：pip 需 --upgrade 等，见 _upgrade_cmd）"""
    if req.agent_id not in AGENT_REGISTRY:
        return {"success": False, "error": f"Unknown agent: {req.agent_id}"}
    agent = AGENT_REGISTRY[req.agent_id]
    os_name = _get_os()
    install_cmd = agent["install"].get(os_name)
    if not install_cmd:
        return {"success": False, "error": f"No update command for {os_name}"}
    cmd = _upgrade_cmd(install_cmd)
    if not cmd:
        return {"success": False, "error": "此 Agent 无命令行升级方式，请到官网手动升级"}
    if req.agent_id in _install_tasks and _install_tasks[req.agent_id]["status"] == "running":
        return {"success": True, "task_id": req.agent_id, "status": "already_running"}
    _install_tasks[req.agent_id] = {"status": "running", "progress": 0, "output": [], "error": None}
    asyncio.create_task(_run_install(req.agent_id, cmd))
    return {"success": True, "task_id": req.agent_id, "status": "started"}


async def _run_install(agent_id: str, cmd: str):
    """Run install command in background with progress tracking."""
    task = _install_tasks[agent_id]
    try:
        # Force line-buffered output for pip/npm (they buffer when not on a TTY)
        env_prefix = ""
        if "pip " in cmd or "pip3 " in cmd:
            env_prefix = "PYTHONUNBUFFERED=1 "
        elif "npm " in cmd:
            env_prefix = ""
        full_cmd = env_prefix + cmd

        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        while True:
            line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                task["output"].append(text)
                lower = text.lower()
                if any(
                    w in lower for w in ["success", "installed", "complete", "added", "satisfied"]
                ):
                    task["progress"] = 100
                elif any(w in lower for w in ["downloading", "fetching", "receiving"]):
                    task["progress"] = min(task["progress"] + 15, 60)
                elif any(w in lower for w in ["installing", "building", "compiling", "linking"]):
                    task["progress"] = min(task["progress"] + 10, 80)
                elif any(w in lower for w in ["resolving", "collecting", "using", "already"]):
                    task["progress"] = min(task["progress"] + 5, 40)
                elif "error" in lower or "failed" in lower:
                    task["progress"] = task["progress"]  # don't advance on errors
                # else: don't increment for unknown lines
        await proc.wait()
        if proc.returncode == 0:
            task["status"] = "done"
            task["progress"] = 100
        else:
            task["status"] = "error"
            task["error"] = f"Exit code {proc.returncode}"
    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
