LANGUAGES = {
    "English": {"code": "en-IN", "model_name": "English"},
    "Hindi": {"code": "hi-IN", "model_name": "Hindi"},
    "Marathi": {"code": "mr-IN", "model_name": "Marathi"},
    "Gujarati": {"code": "gu-IN", "model_name": "Gujarati"},
    "Bengali": {"code": "bn-IN", "model_name": "Bengali"},
    "Tamil": {"code": "ta-IN", "model_name": "Tamil"},
    "Telugu": {"code": "te-IN", "model_name": "Telugu"},
    "Kannada": {"code": "kn-IN", "model_name": "Kannada"},
    "Malayalam": {"code": "ml-IN", "model_name": "Malayalam"},
    "Punjabi": {"code": "pa-IN", "model_name": "Punjabi"},
    "Urdu": {"code": "ur-IN", "model_name": "Urdu"},
}
AUTO_DETECT = "Auto-detect from question"
LANGUAGE_OPTIONS = [AUTO_DETECT, *LANGUAGES.keys()]


def language_code(selected: str) -> str:
    return LANGUAGES.get(selected, {}).get("code", "")


def model_language_name(selected: str) -> str:
    return LANGUAGES.get(selected, {}).get("model_name", "the same language as the user's question")


def normalize_detected_language(value: str) -> str | None:
    """Map Gemini's spoken language label to a supported display language."""
    lowered = (value or "").strip().casefold()
    for name in LANGUAGES:
        if lowered == name.casefold() or lowered.startswith(name.casefold() + " ") or lowered.startswith(name.casefold() + " ("):
            return name
    aliases = {"english": "English", "hindi": "Hindi", "marathi": "Marathi", "gujarati": "Gujarati",
               "bengali": "Bengali", "tamil": "Tamil", "telugu": "Telugu", "kannada": "Kannada",
               "malayalam": "Malayalam", "punjabi": "Punjabi", "urdu": "Urdu"}
    return aliases.get(lowered)


def resolve_voice_response_language(detected: str, selected: str) -> str:
    """Prefer the language identified in speech; otherwise honor the UI selection."""
    return normalize_detected_language(detected) or (selected if selected in LANGUAGES else "English")
