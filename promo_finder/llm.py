"""One function, many models: `complete_json(model_spec, system, prompt)`.

model_spec is "provider:model", e.g.
    gemini:gemini-3.6-flash          (free tier, GEMINI_API_KEY)
    openai:gpt-5-mini                (OPENAI_API_KEY)
    anthropic:claude-opus-5-5        (ANTHROPIC_API_KEY, pip install anthropic)
    groq:llama-3.3-70b-versatile     (free tier, GROQ_API_KEY)
    openrouter:<model>               (OPENROUTER_API_KEY)
    ollama:qwen2.5:14b               (local, no key)
    compat:<model>                   (any OpenAI-compatible server: LLM_BASE_URL + LLM_API_KEY)
A comma-separated list is a fallback chain.

Keys come from a per-run `Ctx` (the web app passes each visitor's own keys, so
nothing is shared between visitors) or, for the CLI, from environment variables.
Responses are cached on disk by hash(model, system, prompt) when a cache dir is
set, so CLI re-runs cost nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

OPENAI_COMPAT = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}
KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "compat": "LLM_API_KEY",
           **{p: env for p, (_, env) in OPENAI_COMPAT.items() if env}}


@dataclass
class Ctx:
    """Per-run settings. keys: {"GEMINI_API_KEY": "...", "LLM_BASE_URL": "..."}.
    Missing keys fall back to environment variables. cache_dir=None disables the cache."""
    keys: dict = field(default_factory=dict)
    cache_dir: Path | None = field(default_factory=lambda: Path(os.environ.get("PROMO_CACHE", ".cache/llm")))
    exhausted: set = field(default_factory=set)
    last_call: float = 0.0

    def get(self, name: str) -> str:
        return (self.keys.get(name) or os.environ.get(name, "")).strip()


_DEFAULT = Ctx()


class LLMError(RuntimeError):
    pass


class QuotaExhausted(LLMError):
    """Daily quota used up: retrying today is pointless, move to the next model."""


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


def _gemini(ctx: Ctx, model: str, system: str, prompt: str) -> str:
    key = ctx.get("GEMINI_API_KEY") or ctx.get("GOOGLE_API_KEY")
    if not key:
        raise LLMError("Set GEMINI_API_KEY (free at https://aistudio.google.com/apikey)")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=300)
    _check_quota("gemini", r)
    if r.status_code != 200:
        raise LLMError(f"gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError(f"gemini: no text in response: {json.dumps(data)[:300]}")


def _openai_compat(ctx: Ctx, provider: str, model: str, system: str, prompt: str) -> str:
    if provider == "compat":
        base, key = ctx.get("LLM_BASE_URL"), ctx.get("LLM_API_KEY")
        if not base:
            raise LLMError("Set LLM_BASE_URL (the API's base URL, e.g. https://api.example.com/v1)")
    else:
        base, env = OPENAI_COMPAT[provider]
        key = ctx.get(env) if env else ""
        if env and not key:
            raise LLMError(f"Set {env}")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }  # no temperature: several newer models only accept the default
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = requests.post(f"{base.rstrip('/')}/chat/completions", json=body, headers=headers, timeout=300)
    _check_quota(provider, r)
    if r.status_code == 400 and "response_format" in r.text:
        body.pop("response_format")  # server doesn't support JSON mode: the prompt still asks for JSON
        r = requests.post(f"{base.rstrip('/')}/chat/completions", json=body, headers=headers, timeout=300)
    if r.status_code != 200:
        raise LLMError(f"{provider} {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def _anthropic(ctx: Ctx, model: str, system: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise LLMError("pip install anthropic")
    key = ctx.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("Set ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"output_config": {"effort": "low"}},
        )
    except anthropic.APIStatusError as e:
        raise LLMError(f"anthropic {e.status_code}: {str(e)[:300]}")
    except anthropic.APIConnectionError as e:
        raise LLMError(f"anthropic connection error: {e}")
    if msg.stop_reason == "refusal":
        raise LLMError("anthropic: refusal")
    return "".join(b.text for b in msg.content if b.type == "text")


def guess_provider(key: str) -> str | None:
    """Provider from the key's prefix, when it has a well-known one."""
    k = key.strip()
    for prefix, provider in (("sk-ant-", "anthropic"), ("sk-or-", "openrouter"), ("gsk_", "groq"),
                             ("AIza", "gemini"), ("sk-", "openai")):
        if k.startswith(prefix):
            return provider
    return None


_PROBES = {  # cheap "list models" calls: they cost nothing and use no generation quota
    "gemini": lambda k: requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                                     headers={"x-goog-api-key": k}, params={"pageSize": 1}, timeout=10),
    "openai": lambda k: requests.get("https://api.openai.com/v1/models",
                                     headers={"Authorization": f"Bearer {k}"}, timeout=10),
    "anthropic": lambda k: requests.get("https://api.anthropic.com/v1/models", timeout=10,
                                        headers={"x-api-key": k, "anthropic-version": "2023-06-01"}),
    "groq": lambda k: requests.get("https://api.groq.com/openai/v1/models",
                                   headers={"Authorization": f"Bearer {k}"}, timeout=10),
    "openrouter": lambda k: requests.get("https://openrouter.ai/api/v1/key",
                                         headers={"Authorization": f"Bearer {k}"}, timeout=10),
}


def detect_provider(key: str) -> str:
    """Which provider accepts this key. Tries the prefix guess first, then asks each
    provider. Falls back to the prefix guess (so a bad key still gets that provider's
    clear error), else "compat" (any other OpenAI-compatible API; needs a base URL)."""
    k = key.strip()
    guess = guess_provider(k)
    order = ([guess] if guess else []) + [p for p in _PROBES if p != guess]
    for p in order:
        try:
            if _PROBES[p](k).status_code == 200:
                return p
        except requests.RequestException:
            continue
    return guess or "compat"


def complete_json(model_spec: str, system: str, prompt: str, rpm: float = 8,
                  retries: int = 5, ctx: Ctx | None = None) -> tuple[dict, str, bool, str]:
    """model_spec may be a comma-separated fallback chain: "gemini:a,gemini:b".
    A model whose daily quota is used up is skipped for the rest of the run.
    Returns (parsed_json, raw_text, from_cache, model_used)."""
    ctx = ctx or _DEFAULT
    chain = [m.strip() for m in model_spec.split(",") if m.strip()]
    if ctx.cache_dir:
        for spec in chain:  # a cached answer from any model in the chain is reused
            cpath = _cache_path(ctx, spec, system, prompt)
            if cpath.exists():
                raw = json.loads(cpath.read_text(encoding="utf-8"))["raw"]
                return _extract_json(raw), raw, True, spec
    last = None
    for spec in chain:
        if spec in ctx.exhausted:
            continue
        try:
            parsed, raw = _call(ctx, spec, system, prompt, rpm, retries)
            return parsed, raw, False, spec
        except QuotaExhausted as e:
            print(f"    {spec}: {e}; falling back", flush=True)
            ctx.exhausted.add(spec)
            last = e
    raise last or LLMError("no model available")


def _cache_path(ctx: Ctx, spec: str, system: str, prompt: str) -> Path:
    key = hashlib.sha256("\n".join([spec, system, prompt]).encode()).hexdigest()[:24]
    return ctx.cache_dir / f"{key}.json"


def _call(ctx: Ctx, model_spec: str, system: str, prompt: str, rpm: float, retries: int) -> tuple[dict, str]:
    provider, _, model = model_spec.partition(":")
    err = None
    for attempt in range(retries):
        wait = 60.0 / rpm - (time.time() - ctx.last_call)
        if wait > 0:
            time.sleep(wait)
        ctx.last_call = time.time()
        try:
            if provider == "gemini":
                raw = _gemini(ctx, model, system, prompt)
            elif provider == "anthropic":
                raw = _anthropic(ctx, model, system, prompt)
            elif provider in OPENAI_COMPAT or provider == "compat":
                raw = _openai_compat(ctx, provider, model, system, prompt)
            else:
                raise LLMError(f"unknown provider '{provider}'")
            parsed = _extract_json(raw)
            if ctx.cache_dir:
                ctx.cache_dir.mkdir(parents=True, exist_ok=True)
                _cache_path(ctx, model_spec, system, prompt).write_text(
                    json.dumps({"model": model_spec, "raw": raw}, ensure_ascii=False), encoding="utf-8")
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
