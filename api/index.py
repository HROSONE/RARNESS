"""Vercel ASGI entrypoint for the RARNESS web chat.

This serverless web interface is a chat frontend; it does not run local shell tools.
Configure RARNESS_ACCESS_TOKEN and one provider API key in Vercel environment variables.
"""
from __future__ import annotations

import hmac
import os
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

app = FastAPI(title="RARNESS API", docs_url=None, redoc_url=None)

ALLOWED_GEMINI_MODELS = {
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-3.8-flash",
    "gemini-4-argon",
}


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=20)
    model: str | None = None


def get_client() -> tuple[OpenAI, str]:
    provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY", "").strip()
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    elif provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        base_url = "https://openrouter.ai/api/v1"
        model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free").strip()
    elif provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        base_url = "https://api.anthropic.com/v1/"
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip()
    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "").strip()
        base_url = "https://api.openai.com/v1"
        model = os.getenv("OPENAI_MODEL", "gpt-4.1").strip()
    else:
        raise HTTPException(
            status_code=500,
            detail="AI_PROVIDER deve ser gemini, anthropic, openai ou openrouter.",
        )

    if not key:
        raise HTTPException(status_code=503, detail=f"Configure a chave de API do provedor {provider} na Vercel.")

    return OpenAI(api_key=key, base_url=base_url, timeout=45.0, max_retries=1), model


@app.get("/")
def health():
    return {"name": "RARNESS", "status": "online", "mode": "web-chat"}


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
        if body.model not in ALLOWED_GEMINI_MODELS:
            raise HTTPException(status_code=400, detail="Modelo Gemini não permitido.")
        model = body.model

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": item.role, "content": item.content} for item in body.messages],
            temperature=0.3,
            max_tokens=1800,
        )
        answer = response.choices[0].message.content or ""
        return {"answer": answer, "model": model}
    except HTTPException:
        raise
    except Exception:
        # Do not return provider errors that may contain internal request details.
        raise HTTPException(
            status_code=502,
            detail="O provedor de IA falhou. Confira a chave, o modelo e os limites da API.",
        )
