import json
import re
from typing import Any

from config import AD_ID_KEYS, SPONSORED_HINTS

_WORD_SPLIT_RE = re.compile(r"[\s\u00B7•·|/\\\-_—–:;,.!?()\[\]{}\"']+")


def clean_graphql_text(text: str) -> str:
    if text.startswith("for (;;);"):
        return text[len("for (;;);") :]
    return text


def parse_graphql_payload(text: str) -> list[dict]:
    cleaned = clean_graphql_text(text).strip()
    if not cleaned:
        return []
    try:
        return [json.loads(cleaned)]
    except Exception:
        pass

    payloads: list[dict] = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("for (;;);"):
            line = line[len("for (;;);") :]
        try:
            payloads.append(json.loads(line))
        except Exception:
            continue
    return payloads


def collect_strings(node: Any, limit=2000) -> list[str]:
    strings: list[str] = []

    def walk(n):
        if len(strings) >= limit:
            return
        if isinstance(n, dict):
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
        elif isinstance(n, str):
            strings.append(n)

    walk(node)
    return strings


def find_first_key(node: Any, keys_lower: set[str]):
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in keys_lower:
                return v
        for v in node.values():
            found = find_first_key(v, keys_lower)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = find_first_key(v, keys_lower)
            if found is not None:
                return found
    return None


def normalize_blob_for_hints(strings: list[str]):
    glued: list[str] = []
    buf: list[str] = []

    def flush_buf():
        nonlocal buf
        if buf:
            glued.append("".join(buf))
            buf = []

    for s in strings:
        if not isinstance(s, str):
            continue
        t = s.strip()
        if not t:
            continue
        if len(t) == 1 and re.fullmatch(r"[A-Za-z]", t):
            buf.append(t)
        else:
            flush_buf()
            glued.append(t)

    flush_buf()

    tokens: list[str] = []
    for part in glued:
        part = part.strip().lower()
        if not part:
            continue
        part = part.replace("\u00B7", " ").replace("·", " ").replace("•", " ")
        for tok in _WORD_SPLIT_RE.split(part):
            tok = tok.strip()
            if tok:
                tokens.append(tok)

    blob = " ".join(tokens)
    blob_spaced = f" {blob} "
    return blob, blob_spaced


def payload_looks_sponsored(payload: dict) -> bool:
    if find_first_key(payload, AD_ID_KEYS) is not None:
        return True

    def walk(n):
        if isinstance(n, dict):
            keys = [str(k).lower() for k in n.keys()]
            if "sponsored_data" in keys and n.get("sponsored_data") is not None:
                return True
            if n.get("is_sponsored") is True:
                return True
            if any("sponsor" in k for k in keys):
                return True
            for v in n.values():
                if walk(v):
                    return True
        elif isinstance(n, list):
            for v in n:
                if walk(v):
                    return True
        return False

    if walk(payload):
        return True

    strings = collect_strings(payload, limit=2000)
    blob, blob_spaced = normalize_blob_for_hints(strings)

    for hint in SPONSORED_HINTS:
        h = hint.strip().lower()
        if " " in h:
            if h in blob:
                return True
        else:
            if f" {h} " in blob_spaced:
                return True

    if " ad " in blob_spaced:
        return True

    return False