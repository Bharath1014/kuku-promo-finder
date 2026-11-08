"""One function, many models: `complete_json(model_spec, system, prompt)`.

model_spec is "provider:model", e.g.
    gemini:gemini-3.6-flash          (free tier, GEMINI_API_KEY)
    groq:llama-3.3-70b-versatile     (free tier, GROQ_API_KEY)
    openrouter:<model>               (OPENROUTER_API_KEY)
    openai:<model>                   (OPENAI_API_KEY)
    ollama:qwen2.5:14b               (local, no key)
    anthropic:claude-opus-5-5        (ANTHROPIC_API_KEY, pip install anthropic)
    compat:<model>                   (any OpenAI-compatible server: LLM_BASE_URL + LLM_API_KEY)

Swapping models is a CLI flag. Responses are cached on disk by
hash(model, system, prompt), so re-runs cost nothing and raw outputs are kept.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests

CACHE_DIR = Path(os.environ.get("PROMO_CACHE", ".cache/llm"))
_last_call = [0.0]

OPENAI_COMPAT = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}


class LLMError(RuntimeError):
    pass


class QuotaExhausted(LLMError):
    """Daily quota used up: retrying today is pointless, move to the next model."""


_exhausted: set[str] = set()


def _check_quota(provider: str, r) -> None:
    if r.status_code == 429 and re.search(r"PerDay|per day|RPD|TPD|daily", r.text, re.I):
        raise QuotaExhausted(f"{provider}: daily free quota used up")


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _gemini(model: str, system: str, prompt: str) -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise LLMError("Set GEMINI_API_KEY (free at https://aistudio.google.com/apikey)")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    r = requests.post(url, params={"key": key}, json=body, timeout=300)
    _check_quota("gemini", r)
    if r.status_code != 200:
        raise LLMError(f"gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError(f"gemini: no text in response: {json.dumps(data)[:300]}")


def _openai_compat(provider: str, model: str, system: str, prompt: str) -> str:
    if provider == "compat":
        base, key = os.environ["LLM_BASE_URL"], os.environ.get("LLM_API_KEY", "")
    else:
        base, env = OPENAI_COMPAT[provider]
        key = os.environ.get(env, "") if env else ""
        if env and not key:
            raise LLMError(f"Set {env}")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = requests.post(f"{base}/chat/completions", json=body, headers=headers, timeout=300)
    _check_quota(provider, r)
    if r.status_code != 200:
        raise LLMError(f"{provider} {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def _anthropic(model: str, system: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise LLMError("pip install anthropic")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low"},
    )
    if msg.stop_reason == "refusal":
        raise LLMError("anthropic: refusal")
    return "".join(b.text for b in msg.content if b.type == "text")


def complete_json(model_spec: str, system: str, prompt: str, rpm: float = 8,
                  retries: int = 5) -> tuple[dict, str, bool, str]:
    """model_spec may be a comma-separated fallback chain: "gemini:a,gemini:b".
    A model whose daily quota is used up is skipped for the rest of the run.
    Returns (parsed_json, raw_text, from_cache, model_used)."""
    chain = [m.strip() for m in model_spec.split(",") if m.strip()]
    for spec in chain:  # a cached answer from any model in the chain is reused
        cpath = _cache_path(spec, system, prompt)
        if cpath.exists():
            raw = json.loads(cpath.read_text(encoding="utf-8"))["raw"]
            return _extract_json(raw), raw, True, spec
    last = None
    for spec in chain:
        if spec in _exhausted:
            continue
        try:
            parsed, raw = _call(spec, system, prompt, rpm, retries)
            return parsed, raw, False, spec
        except QuotaExhausted as e:
            print(f"    {spec}: {e}; falling back", flush=True)
            _exhausted.add(spec)
            last = e
    raise last or LLMError("no model available")


def _cache_path(spec: str, system: str, prompt: str) -> Path:
    key = hashlib.sha256("\n".join([spec, system, prompt]).encode()).hexdigest()[:24]
    return CACHE_DIR / f"{key}.json"


def _call(model_spec: str, system: str, prompt: str, rpm: float, retries: int) -> tuple[dict, str]:
    provider, _, model = model_spec.partition(":")
    cpath = _cache_path(model_spec, system, prompt)
    err = None
    for attempt in range(retries):
        wait = 60.0 / rpm - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            if provider == "gemini":
                raw = _gemini(model, system, prompt)
            elif provider == "anthropic":
                raw = _anthropic(model, system, prompt)
            elif provider in OPENAI_COMPAT or provider == "compat":
                raw = _openai_compat(provider, model, system, prompt)
            else:
                raise LLMError(f"unknown provider '{provider}'")
            parsed = _extract_json(raw)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps({"model": model_spec, "raw": raw}, ensure_ascii=False),
                             encoding="utf-8")
            return parsed, raw
        except QuotaExhausted:
            raise
        except (LLMError, requests.RequestException, json.JSONDecodeError, KeyError) as e:
            err = e
            msg = str(e)
            if ("Set " in msg or "unknown provider" in msg or "pip install" in msg
                    or re.search(r" (400|401|403|404): ", msg)):
                raise  # not worth retrying: bad key, bad model name, bad request
            delay = min(60, 5 * 2 ** attempt)
            print(f"    retry {attempt + 1}/{retries} in {delay}s: {msg[:160]}", flush=True)
            time.sleep(delay)
    raise LLMError(f"{model_spec} failed after {retries} attempts: {err}")
