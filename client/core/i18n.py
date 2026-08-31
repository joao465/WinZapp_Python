import json
import locale
import logging
import os
from app_paths import resource_path

# Fallback used only if languages/language_map.json is missing or unreadable
# — should never happen in a normal install, but adding a language must not
# require a rebuild, so the real source of truth is that JSON file.
_FALLBACK_LANGUAGE_NAMES = {
    "pt-BR": "Português (Brasil)",
    "en-US": "English (United States)",
}


def _load_language_names() -> dict:
    """Load { lang_code: display_name } from languages/language_map.json.

    Dict order (== file order, preserved by json.load) determines the order
    shown in the Settings combobox. Adding a new locale only requires
    dropping languages/<code>.json + a new entry here — no rebuild.
    """
    try:
        with open(resource_path("languages", "language_map.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            return data
    except Exception:
        logging.warning("Failed to load languages/language_map.json — using fallback list", exc_info=True)
    return dict(_FALLBACK_LANGUAGE_NAMES)


# Human-readable display names for each supported locale.
LANGUAGE_NAMES = _load_language_names()

# Module-level translation cache: { lang_code: { key: value } }
_TRANSLATIONS_CACHE: dict = {}


def _normalize_locale_name(value: str | None) -> str:
    """Convert locale spellings such as pt_BR.UTF-8 into pt-BR."""
    if not value:
        return ""
    value = str(value).strip()
    if not value:
        return ""
    value = value.split(".", 1)[0].split("@", 1)[0].replace("_", "-")
    if value.upper() in {"C", "POSIX"}:
        return ""
    parts = [part for part in value.split("-") if part]
    if not parts:
        return ""
    language = parts[0].lower()
    if len(parts) == 1:
        return language
    return f"{language}-{parts[1].upper()}"


def detect_system_language() -> str:
    """Return the closest WinZapp translation for the operating-system locale.

    Linux desktop sessions normally expose the language through LC_ALL,
    LC_MESSAGES or LANG. Exact regional translations win; if only the base
    language matches, the first translation for that language is used.
    """
    candidates: list[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = _normalize_locale_name(os.environ.get(key))
        if value and value not in candidates:
            candidates.append(value)

    for getter in (
        lambda: locale.getlocale(locale.LC_MESSAGES)[0],
        lambda: locale.getlocale()[0],
    ):
        try:
            value = _normalize_locale_name(getter())
        except Exception:
            value = ""
        if value and value not in candidates:
            candidates.append(value)

    supported = list(LANGUAGE_NAMES)
    supported_lower = {code.lower(): code for code in supported}

    for candidate in candidates:
        exact = supported_lower.get(candidate.lower())
        if exact:
            return exact

    for candidate in candidates:
        base = candidate.split("-", 1)[0].lower()
        for code in supported:
            if code.split("-", 1)[0].lower() == base:
                return code

    # Keep the project's historical default if the system language has no
    # bundled translation.
    return "pt-BR" if "pt-BR" in LANGUAGE_NAMES else supported[0]


def _load_translations(lang_code: str) -> dict:
    """Load the JSON file for *lang_code* into the cache and return it."""
    try:
        with open(resource_path("languages", f"{lang_code}.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    _TRANSLATIONS_CACHE[lang_code] = data
    return data


class I18n:
    def __init__(self, main_window):
        self.main_window = main_window
        self.language = detect_system_language()

    def get_language(self):
        """Use a saved override when valid, otherwise follow the system locale."""
        configured = self.main_window.settings.get("general", {}).get("language", "")
        self.language = configured if configured in LANGUAGE_NAMES else detect_system_language()
        return self.language

    def t(self, key: str) -> str:
        """Translate *key* using the language currently stored in self.language."""
        lang = self.language
        translations = _TRANSLATIONS_CACHE.get(lang)
        if translations is None:
            translations = _load_translations(lang)
        return translations.get(key, key)

    @staticmethod
    def invalidate_cache():
        """Clear the module-level translation cache (call after a language change)."""
        _TRANSLATIONS_CACHE.clear()
