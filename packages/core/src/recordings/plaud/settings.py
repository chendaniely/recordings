"""The `[plaud]` settings (stage 2b spec §17.4): one reader for the worker, doctor, the CLI and
Status.

- **Unset means:** `schedule_minutes` 20, `auto_import` true, `full_sweep_days` 7, `keep_out_word`
  "noarchive", `consent_list` none (a consent stop), `token_store` `/plaud/tokens.json`, and
  `api_base` the copied client's.
- **A wrong type,** a bad `api_base` or a bad pattern raises ConfigError naming the key, never its
  value. An invalid `keep_out_word` is flagged instead (`keep_out_word_ok`): the worker idles, and
  Status and doctor say BAD_WORD (§12.2), while the web app and the backup go on.
- **`API_BASE` and `check_api_base` come from `endpoint`,** which loads no network module, never
  from the client: the web app imports this module (for BAD_WORD and the defaults) and calls
  `plaud_settings` for its views, and it never loads the client, even at runtime (spec §9.1,
  CLAUDE.md; 2b phase 1 plan, decision 31).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recordings.config import Config, ConfigError
from recordings.plaud.consent import consent_list_path
from recordings.plaud.endpoint import API_BASE, check_api_base
from recordings.plaud.keepout import DEFAULT_WORD, valid_word
from recordings.plaud.reconcile import patterns_from_config

DEFAULT_SCHEDULE_MINUTES = 20
DEFAULT_FULL_SWEEP_DAYS = 7
DEFAULT_TOKEN_STORE = Path("/plaud/tokens.json")
BAD_WORD = "configuration: `keep_out_word` is invalid"


@dataclass(frozen=True)
class PlaudSettings:
    schedule_minutes: int  # 0 is off
    auto_import: bool
    full_sweep_days: int
    keep_out_word: str  # as configured
    keep_out_word_ok: bool  # False: the worker idles, and Status and doctor say BAD_WORD
    auto_private_patterns: tuple[str, ...]
    consent_list: Path | None  # None: unset, a consent stop
    token_store: Path
    api_base: str


def _whole(section: dict[str, Any], key: str, default: int, least: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise ConfigError(f"[plaud] {key} must be a whole number, {least} or more")
    return value


def plaud_settings(cfg: Config) -> PlaudSettings:
    section = cfg.data.get("plaud", {})
    if not isinstance(section, dict):
        raise ConfigError("[plaud] must be a table")
    auto_import = section.get("auto_import", True)
    if not isinstance(auto_import, bool):
        raise ConfigError("[plaud] auto_import must be true or false")
    consent = section.get("consent_list")
    if consent is not None and not isinstance(consent, str):
        raise ConfigError("[plaud] consent_list must be a path, in quotes")
    token_store = section.get("token_store", str(DEFAULT_TOKEN_STORE))
    if not isinstance(token_store, str) or not Path(token_store).expanduser().is_absolute():
        raise ConfigError("[plaud] token_store must be an absolute path, in quotes")
    word = section.get("keep_out_word", DEFAULT_WORD)
    return PlaudSettings(
        schedule_minutes=_whole(section, "schedule_minutes", DEFAULT_SCHEDULE_MINUTES, 0),
        auto_import=auto_import,
        full_sweep_days=_whole(section, "full_sweep_days", DEFAULT_FULL_SWEEP_DAYS, 1),
        keep_out_word=word if isinstance(word, str) else repr(word),
        keep_out_word_ok=valid_word(word),
        auto_private_patterns=patterns_from_config(cfg.data),
        consent_list=consent_list_path(cfg.data) if consent and consent.strip() else None,
        token_store=Path(token_store).expanduser(),
        api_base=check_api_base(section.get("api_base", API_BASE)),
    )
