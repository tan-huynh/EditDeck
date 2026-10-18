from __future__ import annotations

import re
from typing import Any, Optional

import requests
from openai import OpenAI


DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def chat_completion_text(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: int = 120,
) -> str:
    normalized_provider = (provider or "").strip().lower()
    if normalized_provider == "openai":
        client = OpenAI(base_url=base_url, api_key=api_key)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response = client.chat.completions.create(**payload)
        return _message_text(response.choices[0].message.content).strip()

    if normalized_provider == "gemini":
        request_payload = _build_gemini_request(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        response = requests.post(
            _build_gemini_generate_content_url(base_url, model),
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json=request_payload,
            timeout=timeout,
        )
        raw_text = response.text or ""
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"Gemini response was not valid JSON: {raw_text[:500]}") from exc
        if not response.ok:
            raise RuntimeError(f"Gemini request failed with HTTP {response.status_code}: {raw_text[:500]}")
        _raise_for_gemini_block(data)
        text = _extract_gemini_text(data)
        if not text:
            raise RuntimeError(f"Gemini response did not contain text output: {raw_text[:500]}")
        return text.strip()

    raise ValueError(f"Unsupported text provider: {provider}")


def _build_gemini_generate_content_url(base_url: str, model: str) -> str:
    cleaned = (base_url or DEFAULT_GEMINI_BASE_URL).strip().rstrip("/")
    if not cleaned:
        cleaned = DEFAULT_GEMINI_BASE_URL
    if cleaned.endswith(":generateContent"):
        return cleaned
    if cleaned.endswith("/models"):
        return f"{cleaned}/{model}:generateContent"
    if "/models/" in cleaned:
        return f"{cleaned}:generateContent"
    return f"{cleaned}/models/{model}:generateContent"


def _build_gemini_request(
    *,
    messages: list[dict[str, Any]],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> dict[str, Any]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for message in messages:
        role = str(message.get("role", "user") or "user").strip().lower()
        raw_content = message.get("content")

        if role == "system":
            system_text = _content_to_plain_text(raw_content).strip()
            if system_text:
                system_parts.append(system_text)
            continue

        parts = _content_to_gemini_parts(raw_content)
        if not parts:
            continue

        gemini_role = "model" if role == "assistant" else "user"
        if contents and contents[-1].get("role") == gemini_role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": gemini_role, "parts": parts})

    payload: dict[str, Any] = {
        "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

    generation_config: dict[str, Any] = {}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens
    if generation_config:
        payload["generationConfig"] = generation_config
    return payload


def _content_to_plain_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            if item_type == "text" and item.get("text"):
                chunks.append(str(item["text"]))
        return "\n".join(chunks)
    return str(content)


def _content_to_gemini_parts(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return [{"text": str(content)}]

    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            parts.append({"text": item})
            continue
        if not isinstance(item, dict):
            parts.append({"text": str(item)})
            continue

        item_type = str(item.get("type", "") or "").strip().lower()
        if item_type == "text":
            text = item.get("text")
            if text:
                parts.append({"text": str(text)})
            continue

        if item_type == "image_url":
            image_payload = item.get("image_url")
            if isinstance(image_payload, dict):
                image_url = image_payload.get("url")
            else:
                image_url = image_payload
            if not image_url:
                continue
            mime_type, data = _parse_data_url(str(image_url))
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": data,
                    }
                }
            )
            continue

    return parts


def _parse_data_url(data_url: str) -> tuple[str, str]:
    match = re.fullmatch(r"data:([^;]+);base64,(.+)", data_url, re.DOTALL)
    if not match:
        raise ValueError("Gemini provider currently requires image inputs as base64 data URLs.")
    return match.group(1), match.group(2)


def _raise_for_gemini_block(payload: dict[str, Any]) -> None:
    prompt_feedback = payload.get("promptFeedback") or {}
    block_reason = prompt_feedback.get("blockReason")
    if block_reason:
        raise RuntimeError(f"Gemini blocked the request: {block_reason}")

    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        finish_reason = candidate.get("finishReason")
        if finish_reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
            raise RuntimeError(f"Gemini candidate blocked with finish reason: {finish_reason}")


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks)


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                text_val = item.get("text")
                if text_val:
                    chunks.append(str(text_val))
                continue
            text_val = getattr(item, "text", None)
            if text_val:
                chunks.append(str(text_val))
        return "\n".join(chunks)
    return str(content)
