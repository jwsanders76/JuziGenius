"""
Thin, dependency-free REST clients for the AI providers JuziGenius can use
for sentence generation. Each provider is called via plain urllib so no
provider SDK needs to be installed. Every call takes an api_key explicitly --
none of these functions read config.py or persist keys; that's the caller's
responsibility (see juzi_engine.py / server.py for how server-side vs.
client-held keys are handled).
"""
import json
import urllib.error
import urllib.request

PROVIDER_CONFIG = {
    "gemini": {"label": "Gemini", "model": "gemini-3.6-flash"},
    "claude": {"label": "Claude", "model": "claude-sonnet-5"},
    "openai": {"label": "ChatGPT", "model": "gpt-5.4-mini"},
    "grok": {"label": "Grok", "model": "grok-4.6"},
}


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach {url}: {e.reason}") from e


def _call_gemini(api_key: str, model: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    data = _post_json(url, {"Content-Type": "application/json"}, {
        "contents": [{"parts": [{"text": prompt}]}]
    })
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_claude(api_key: str, model: str, prompt: str) -> str:
    data = _post_json("https://api.anthropic.com/v1/messages", {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }, {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    })
    return data["content"][0]["text"]


def _call_openai_compatible(base_url: str, api_key: str, model: str, prompt: str) -> str:
    data = _post_json(f"{base_url}/chat/completions", {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}]
    })
    return data["choices"][0]["message"]["content"]


def call_provider(provider: str, api_key: str, prompt: str, model: str = None) -> str:
    """Sends prompt to the given provider's completion API, returns the raw response text."""
    if provider not in PROVIDER_CONFIG:
        raise ValueError(f"Unknown AI provider: {provider}")
    if not api_key:
        raise ValueError(f"No API key provided for {PROVIDER_CONFIG[provider]['label']}.")

    model = model or PROVIDER_CONFIG[provider]["model"]

    if provider == "gemini":
        return _call_gemini(api_key, model, prompt)
    if provider == "claude":
        return _call_claude(api_key, model, prompt)
    if provider == "openai":
        return _call_openai_compatible("https://api.openai.com/v1", api_key, model, prompt)
    if provider == "grok":
        return _call_openai_compatible("https://api.x.ai/v1", api_key, model, prompt)

    raise ValueError(f"Unhandled AI provider: {provider}")
