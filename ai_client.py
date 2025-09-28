# ai_client.py
import os, json, re, requests
from requests import HTTPError

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud")  # your model
TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

def _post_json(url, payload):
    r = requests.post(url, json=payload, timeout=TIMEOUT)
    try:
        r.raise_for_status()
    except HTTPError as e:
        body = ""
        try: body = f" | server said: {r.text[:500]}"
        except Exception: pass
        raise HTTPError(f"{e} (model={payload.get('model')}){body}") from e
    return r

def _messages_to_prompt(messages):
    sys = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    convo = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            convo.append(f"User: {m['content']}")
        elif role == "assistant":
            convo.append(f"Assistant: {m['content']}")
    convo.append("Assistant:")
    return (f"[System]\n{sys}\n\n" if sys else "") + "\n".join(convo)

def _extract_from_harmony_message(message_obj):
    """
    Harmony format: message.content can be a list of blocks, e.g.
      [{"type":"output_text","text":"..."},
       {"type":"json","json": {...}}]
    We prefer the first JSON block; otherwise join text blocks.
    """
    content = message_obj.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        first_json = None
        texts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if "json" in part and first_json is None:
                first_json = part["json"]
            # common text carriers
            if "text" in part:
                texts.append(str(part["text"]))
            elif part.get("type") in ("output_text", "text") and "content" in part:
                texts.append(str(part["content"]))
        if first_json is not None:
            # return as a JSON string for downstream parser
            try:
                return json.dumps(first_json)
            except Exception:
                pass
        if texts:
            return "".join(texts)
    # Some servers put text at top-level keys too
    return message_obj.get("text") or message_obj.get("response") or ""

def _extract_text_or_json(data):
    """
    Normalize various Ollama response shapes to a single string that
    is either JSON or plain text.
    """
    if isinstance(data, dict):
        # Newer chat: {"message": {...}}
        if "message" in data and isinstance(data["message"], dict):
            out = _extract_from_harmony_message(data["message"])
            if out: return out
        # Older generate: {"response":"..."}
        if "response" in data:
            return data["response"]
        # Some servers: {"content":"..."} or {"message":{"content":"string"}}
        if "content" in data:
            c = data["content"]
            if isinstance(c, str): return c
            if isinstance(c, list):  # Harmony-style list at top level
                return _extract_from_harmony_message({"content": c})
    # Last resort: stringify
    return json.dumps(data)

def _chat(messages, model=None, temperature=0.2, json_mode=False):
    """
    Try /api/chat first. On 404/501, fallback to /api/generate.
    If json_mode=True, ask for JSON via options.format='json' (ignored by some models).
    """
    use_model = model or MODEL
    options = {"temperature": temperature}
    if json_mode:
        # Supported by many Ollama backends; ignored by some (including gpt-oss in Harmony).
        options["format"] = "json"

    # 1) chat
    try:
        url = f"{OLLAMA_HOST}/api/chat"
        payload = {
            "model": use_model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        r = _post_json(url, payload)
        return _extract_text_or_json(r.json())
    except HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code not in (404, 501):
            # Real error (incl "model not found") -> bubble up
            raise

    # 2) generate (fallback)
    prompt = _messages_to_prompt(messages)
    gen_url = f"{OLLAMA_HOST}/api/generate"
    gen_payload = {
        "model": use_model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    r = _post_json(gen_url, gen_payload)
    return _extract_text_or_json(r.json())

# ---------- JSON helpers ----------

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.DOTALL)
_FIRST_JSON = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)

def _coerce_json(s: str):
    """
    1) strip ```json fences
    2) if it's already valid JSON -> parse
    3) else find the first {...} or [...] block and parse
    """
    if not isinstance(s, str):
        return s  # maybe already a dict/list
    candidate = _JSON_FENCE.sub("", s.strip())
    try:
        return json.loads(candidate)
    except Exception:
        m = _FIRST_JSON.search(candidate)
        if m:
            return json.loads(m.group(1))
        raise ValueError(f"Model did not return JSON.\nFirst 500 chars:\n{candidate[:500]}")

def json_reply(system_prompt, user_prompt, model=None):
    """
    Ask model for STRICT JSON. Harmony-aware extraction + fallbacks.
    """
    messages = [
        {"role": "system", "content": f"{system_prompt}\nReturn ONLY valid JSON."},
        {"role": "user", "content": user_prompt},
    ]
    content = _chat(messages, model=model, temperature=0.2, json_mode=True)
    return _coerce_json(content)
