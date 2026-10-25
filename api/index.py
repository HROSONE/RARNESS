"""Vercel ASGI entrypoint for the RARNESS web chat with automatic Claude Code-style intent & mode inference."""
from __future__ import annotations

import hmac
import os
import re
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from rarness.intent_router import infer_intent_and_mode

app = FastAPI(title="RARNESS API", docs_url=None, redoc_url=None)

ALLOWED_GEMINI_MODELS = {"gemini-3.8-flash", "gemini-4-argon"}
FORBIDDEN_LEGACY_GEMINI = re.compile(r"^gemini-[12](?:\.|-|$)", re.IGNORECASE)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=20)
    model: str | None = None
    mode: str | None = "auto"


def get_client() -> tuple[OpenAI, str]:
    provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY", "").strip()
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
        if FORBIDDEN_LEGACY_GEMINI.match(model) or model not in ALLOWED_GEMINI_MODELS:
            model = "gemini-3.8-flash"
    elif provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        base_url = "https://openrouter.ai/api/v1"
        model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free").strip()
    else:
        raise HTTPException(status_code=500, detail="AI_PROVIDER deve ser gemini ou openrouter.")

    if not key:
        raise HTTPException(status_code=503, detail=f"Configure a chave de API do provedor {provider} na Vercel.")

    return OpenAI(api_key=key, base_url=base_url, timeout=45.0, max_retries=1), model


@app.get("/")
def health():
    return {"name": "RARNESS", "status": "online", "mode": "web-chat-claude-code-engine"}


@app.post("/chat")
def chat(body: ChatRequest, x_rarness_token: str | None = Header(default=None)):
    expected = os.getenv("RARNESS_ACCESS_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Configure RARNESS_ACCESS_TOKEN nas variáveis de ambiente da Vercel antes de usar o chat.",
        )
    if not x_rarness_token or not hmac.compare_digest(x_rarness_token, expected):
        raise HTTPException(status_code=401, detail="Token de acesso inválido.")

    client, model = get_client()
    if os.getenv("AI_PROVIDER", "gemini").strip().lower() == "gemini" and body.model:
        if FORBIDDEN_LEGACY_GEMINI.match(body.model) or body.model not in ALLOWED_GEMINI_MODELS:
            raise HTTPException(
                status_code=400,
                detail="Modelo Gemini não permitido. Use apenas gemini-3.8-flash ou gemini-4-argon (modelos antigos 2.0/2.5 são bloqueados).",
            )
        model = body.model

    last_user_msg = next((m.content for m in reversed(body.messages) if m.role == "user"), "")
    profile = infer_intent_and_mode(
        last_user_msg,
        configured_mode=body.mode or "auto",
        current_model=model,
        auto_route_model=(body.model is None),
    )
    if os.getenv("AI_PROVIDER", "gemini").strip().lower() == "gemini" and body.model is None:
        model = profile.recommended_model

    sys_prompt = (
        "Você é o RARNESS, agente autônomo de engenharia de software (arquitetura estilo Claude Code). "
        f"Intenção inferida: {profile.intent} | Modo efetivo: {profile.effective_mode} | "
        f"Nível de raciocínio: {profile.thinking_level} | Tipo de projeto: {profile.inferred_project_type}. "
        f"{profile.reasoning_directive} "
        "Regra estrita: nunca use nem sugira modelos antigos como Gemini 1.x, 2.0 ou 2.5; utilize exclusivamente "
        "gemini-3.8-flash e gemini-4-argon."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                *[{"role": item.role, "content": item.content} for item in body.messages],
            ],
            temperature=profile.temperature,
            max_tokens=min(4096, profile.max_output_tokens),
        )
        answer = response.choices[0].message.content or ""
        return {
            "answer": answer,
            "model": model,
            "inferred_intent": profile.intent,
            "effective_mode": profile.effective_mode,
            "thinking_level": profile.thinking_level,
            "project_type": profile.inferred_project_type,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="O provedor de IA falhou. Confira a chave, o modelo e os limites da API.",
        )
