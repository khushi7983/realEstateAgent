"""Language helpers for multilingual voice support."""

from __future__ import annotations

import re

SUPPORTED_LANGUAGES = frozenset({"en-IN", "hi-IN", "te-IN", "ta-IN"})

LANGUAGE_LABELS = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
    "ta-IN": "Tamil",
}

_LANGUAGE_SWITCH_PATTERNS: dict[str, tuple[str, ...]] = {
    "en-IN": (
        r"\bswitch\s+back\s+to\s+english\b",
        r"\bback\s+to\s+english\b",
        r"\bspeak\s+in\s+english\b",
        r"\brespond\s+in\s+english\b",
        r"\banswer\s+in\s+english\b",
        r"\bcontinue\s+in\s+english\b",
    ),
    "hi-IN": (
        r"\bspeak\s+in\s+hindi\b",
        r"\brespond\s+in\s+hindi\b",
        r"\banswer\s+in\s+hindi\b",
        r"\bswitch\s+to\s+hindi\b",
        r"हिंदी\s*में",
        r"हिन्दी\s*में",
    ),
    "te-IN": (
        r"\bspeak\s+in\s+telugu\b",
        r"\brespond\s+in\s+telugu\b",
        r"\banswer\s+in\s+telugu\b",
        r"\bswitch\s+to\s+telugu\b",
        r"తెలుగు\s*లో",
    ),
    "ta-IN": (
        r"\bspeak\s+in\s+tamil\b",
        r"\brespond\s+in\s+tamil\b",
        r"\banswer\s+in\s+tamil\b",
        r"\bswitch\s+to\s+tamil\b",
        r"தமிழில்",
        r"tamil\s+la",
    ),
}


def normalize_language_code(code: str | None) -> str:
    if not code or code.strip().lower() in {"unknown", "auto", "null"}:
        return "en-IN"

    normalized = code.strip()
    if normalized == "en-US":
        return "en-IN"

    if normalized in SUPPORTED_LANGUAGES:
        return normalized

    prefix = normalized.split("-", 1)[0].lower()
    for lang in SUPPORTED_LANGUAGES:
        if lang.startswith(prefix):
            return lang

    return "en-IN"


def detect_language_from_text(text: str) -> str:
    for char in text:
        if "\u0900" <= char <= "\u097F":
            return "hi-IN"
        if "\u0C00" <= char <= "\u0C7F":
            return "te-IN"
        if "\u0B80" <= char <= "\u0BFF":
            return "ta-IN"
    return "en-IN"


def resolve_response_language(stt_language: str | None, transcript: str) -> str:
    if stt_language and stt_language.strip().lower() not in {"unknown", "auto", "null", ""}:
        normalized = normalize_language_code(stt_language)
        if normalized in SUPPORTED_LANGUAGES:
            return normalized
    return detect_language_from_text(transcript)


def detect_language_switch_request(text: str) -> str | None:
    """Return the language requested by the user, if they explicitly ask to switch."""
    if not text or not text.strip():
        return None

    for language_code, patterns in _LANGUAGE_SWITCH_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return language_code

    return None


def resolve_conversation_language(
    current_language: str | None,
    preferred_language: str | None,
    stt_language: str | None,
    transcript: str,
) -> str:
    """Resolve the active response language for the current turn.

    Priority:
    1. Explicit language-switch request in the transcript.
    2. The frontend's preferred conversation language.
    3. The backend's stored conversation language.
    4. STT or script-based detection as a fallback.
    """
    explicit_language = detect_language_switch_request(transcript)
    if explicit_language:
        return explicit_language

    if preferred_language:
        normalized_preference = normalize_language_code(preferred_language)
        if normalized_preference in SUPPORTED_LANGUAGES:
            return normalized_preference

    if current_language:
        normalized_current = normalize_language_code(current_language)
        if normalized_current in SUPPORTED_LANGUAGES:
            return normalized_current

    return resolve_response_language(stt_language, transcript)


def language_instruction(code: str) -> str:
    instructions = {
        "en-IN": (
            "Always respond in English. Keep property names, project names, locality names, builder names, "
            "prices, and other proper nouns exactly as they appear; only translate the conversational text."
        ),
        "hi-IN": (
            "Always respond in Hindi using Devanagari script. Use natural, conversational Hindi. Keep property "
            "names, project names, locality names, builder names, prices, and other proper nouns exactly as they "
            "appear; only translate the conversational text."
        ),
        "te-IN": (
            "Always respond in Telugu using Telugu script. Use natural, conversational Telugu. Keep property "
            "names, project names, locality names, builder names, prices, and other proper nouns exactly as they "
            "appear; only translate the conversational text."
        ),
        "ta-IN": (
            "Always respond in Tamil using Tamil script. Use natural, conversational Tamil. Keep property names, "
            "project names, locality names, builder names, prices, and other proper nouns exactly as they appear; "
            "only translate the conversational text."
        ),
    }
    return instructions.get(code, instructions["en-IN"])
