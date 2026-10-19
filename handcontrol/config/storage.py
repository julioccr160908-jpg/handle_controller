"""Configuration persistence, atomic write, backup recovery, and migrations (Section 11.3).

Responsibilities:
1. Path resolution: `%APPDATA%\\HandControl\\config.json`.
2. Atomic saving: write to temporary file and atomic swap (`os.replace`).
3. Auto-backup: keeps `.bak` file before overwriting.
4. Corruption recovery: restores from `.bak` or falls back to defaults without crashing.
5. Migration logic for future schema increments.
6. Safe profile export and import with strict Pydantic validation.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
from pathlib import Path

from pydantic import ValidationError

from handcontrol.config.models import AppConfig, ProfileModel, create_default_app_config

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1


def get_default_config_dir() -> Path:
    """Resolve %APPDATA%\\HandControl directory, with safe fallback."""
    appdata = os.getenv("APPDATA")
    base = Path(appdata) / "HandControl" if appdata else Path.home() / ".handcontrol"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_default_config_path() -> Path:
    """Default config file path: %APPDATA%\\HandControl\\config.json."""
    return get_default_config_dir() / "config.json"


def migrate_config_dict(raw: dict) -> dict:
    """Apply schema migrations to raw configuration dictionary.

    Args:
        raw: Raw dict loaded from JSON.

    Returns:
        Migrated dict compatible with current schema.
    """
    version = raw.get("schema_version", 0)

    # Example migration path from v0 (or unversioned) to v1
    if version < 1:
        raw["schema_version"] = 1
        if "profiles" not in raw:
            raw["profiles"] = []

    return raw


class ConfigManager:
    """Manages loading, saving, backup, and import/export of configuration files."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = Path(config_path) if config_path else get_default_config_path()
        self.backup_path = self.config_path.with_suffix(".bak")
        self.temp_path = self.config_path.with_suffix(".tmp")
        self._config: AppConfig | None = None

    @property
    def config(self) -> AppConfig:
        """Currently loaded configuration."""
        if self._config is None:
            self._config = self.load()
        return self._config

    def load(self) -> AppConfig:
        """Load configuration from disk with backup fallback and recovery.

        Returns:
            Validated AppConfig instance (never raises on corrupted file).
        """
        if not self.config_path.exists():
            logger.info("Configuration file not found at %s. Creating defaults.", self.config_path)
            default_cfg = create_default_app_config()
            self._config = default_cfg
            self.save(default_cfg)
            return default_cfg

        # 1. Attempt loading main file
        try:
            return self._load_file(self.config_path)
        except Exception as exc:
            logger.warning(
                "Failed to parse main configuration '%s' (%s). Trying backup.",
                self.config_path,
                exc,
            )

        # 2. Attempt loading backup file
        if self.backup_path.exists():
            try:
                cfg = self._load_file(self.backup_path)
                logger.info("Successfully recovered configuration from backup %s", self.backup_path)
                self.save(cfg)  # Restore main file from backup
                return cfg
            except Exception as exc_bak:
                logger.error("Backup file '%s' is also corrupted (%s).", self.backup_path, exc_bak)

        # 3. Fallback to clean default configuration
        logger.error("Loading clean default configuration due to unrecoverable file corruption.")
        default_cfg = create_default_app_config()
        self.save(default_cfg)
        return default_cfg

    def save(self, config: AppConfig | None = None) -> bool:
        """Atomically save configuration to disk with backup creation.

        Args:
            config: AppConfig to save. If None, uses current config.

        Returns:
            True if saved successfully.
        """
        to_save = config or self._config
        if to_save is None:
            return False

        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Backup existing file before overwriting
            if self.config_path.exists():
                try:
                    shutil.copy2(self.config_path, self.backup_path)
                except Exception:
                    logger.exception("Failed to create config backup")

            # 2. Write to temporary file
            data_dict = to_save.model_dump(mode="json")
            with open(self.temp_path, "w", encoding="utf-8") as f:
                json.dump(data_dict, f, indent=2, ensure_ascii=False)

            # 3. Atomic replacement
            os.replace(self.temp_path, self.config_path)
            self._config = to_save
            logger.debug("Configuration atomically saved to %s", self.config_path)
            return True

        except Exception:
            logger.exception("Failed to atomically save configuration to %s", self.config_path)
            if self.temp_path.exists():
                with contextlib.suppress(OSError):
                    self.temp_path.unlink()
            return False

    def export_profile(self, profile_id: str, dest_path: Path | str) -> tuple[bool, str]:
        """Export a single profile to a standalone JSON file.

        Returns:
            (success, message).
        """
        cfg = self.config
        profile = next((p for p in cfg.profiles if p.profile_id == profile_id), None)
        if not profile:
            return False, f"Perfil '{profile_id}' não encontrado."

        try:
            dest = Path(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(profile.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            return True, f"Perfil '{profile.name}' exportado com sucesso."
        except Exception as exc:
            logger.exception("Error exporting profile")
            return False, f"Erro ao exportar perfil: {exc}"

    def import_profile(self, src_path: Path | str) -> tuple[bool, str, ProfileModel | None]:
        """Import and validate a profile from a standalone JSON file.

        Returns:
            (success, message, ProfileModel | None).
        """
        src = Path(src_path)
        if not src.exists():
            return False, f"Arquivo '{src}' não encontrado.", None

        try:
            with open(src, encoding="utf-8") as f:
                raw = json.load(f)

            profile = ProfileModel.model_validate(raw)

            # Check if profile with this id already exists, rename if necessary
            cfg = self.config
            existing_ids = {p.profile_id for p in cfg.profiles}
            if profile.profile_id in existing_ids:
                # Assign a unique profile_id
                suffix = 2
                new_id = f"{profile.profile_id}_{suffix}"
                while new_id in existing_ids:
                    suffix += 1
                    new_id = f"{profile.profile_id}_{suffix}"
                profile = ProfileModel(
                    profile_id=new_id,
                    name=f"{profile.name} ({suffix})",
                    is_builtin=False,
                    gestures=profile.gestures,
                    mappings=profile.mappings,
                )

            # Add to current profiles and persist
            cfg.profiles.append(profile)
            self.save(cfg)
            return True, f"Perfil '{profile.name}' importado com sucesso.", profile

        except json.JSONDecodeError as exc:
            msg = f"Arquivo JSON malformado: {exc}"
            logger.warning("Import rejected: %s", msg)
            return False, msg, None
        except ValidationError as exc:
            msg = f"Estrutura de perfil inválida: {exc.errors()[0].get('msg', 'erro de validação')}"
            logger.warning("Import rejected: %s", msg)
            return False, msg, None
        except Exception as exc:
            msg = f"Erro inesperado ao importar: {exc}"
            logger.exception("Import error")
            return False, msg, None

    # ---------------------------------------------------------------- private

    def _load_file(self, path: Path) -> AppConfig:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        migrated = migrate_config_dict(raw)
        return AppConfig.model_validate(migrated)
