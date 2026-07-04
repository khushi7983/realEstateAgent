"""Language helpers for Hindi, English, and Telugu voice support."""

SUPPORTED_LANGUAGES = frozenset({"en-IN", "hi-IN", "te-IN"})

LANGUAGE_LABELS = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
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
    return "en-IN"


def resolve_response_language(stt_language: str | None, transcript: str) -> str:
    if stt_language and stt_language.strip().lower() not in {"unknown", "auto", "null", ""}:
        normalized = normalize_language_code(stt_language)
        if normalized in SUPPORTED_LANGUAGES:
            return normalized
    return detect_language_from_text(transcript)


def language_instruction(code: str) -> str:
    instructions = {
        "en-IN": "Always respond in English.",
        "hi-IN": "Always respond in Hindi using Devanagari script. Use natural, conversational Hindi.",
        "te-IN": "Always respond in Telugu using Telugu script. Use natural, conversational Telugu.",
    }
    return instructions.get(code, instructions["en-IN"])
