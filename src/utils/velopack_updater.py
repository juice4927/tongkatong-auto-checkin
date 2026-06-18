from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from velopack import App, GithubSource, HttpSource, UpdateInfo, UpdateManager

DEFAULT_GITHUB_UPDATE_SOURCE = "https://github.com/juice4927/tongkatong-auto-checkin"


@dataclass
class VelopackUpdateResult:
    current_version: str
    latest_version: str
    need_update: bool
    manager: UpdateManager
    update_info: Optional[UpdateInfo] = None
    notes: str = ""


def run_startup_update_hooks() -> None:
    """Run Velopack startup hooks before the GUI initializes."""
    try:
        App().set_auto_apply_on_startup(True).run()
    except RuntimeError as exc:
        if _is_not_installed_error(exc):
            return
        raise


def _build_source(source_url: str):
    source = (source_url or "").strip() or DEFAULT_GITHUB_UPDATE_SOURCE
    if "github.com/" in source.lower():
        return GithubSource(source)
    return HttpSource(source)


def create_update_manager(source_url: str) -> UpdateManager:
    return UpdateManager(_build_source(source_url))


def _is_not_installed_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "not properly installed" in message or "notinstalled" in message or "app manifest" in message


def _target_version(update_info: UpdateInfo) -> str:
    return str(update_info.TargetFullRelease.Version)


def _target_notes(update_info: UpdateInfo) -> str:
    return str(update_info.TargetFullRelease.NotesMarkdown or "").strip()


def check_for_updates(source_url: str) -> VelopackUpdateResult:
    try:
        manager = create_update_manager(source_url)
    except RuntimeError as exc:
        if _is_not_installed_error(exc):
            raise RuntimeError("当前程序不是通过 Velopack 安装的，请先下载新版安装包完成一次安装。") from exc
        raise
    current_version = str(manager.get_current_version())
    update_info = manager.check_for_updates()
    if update_info is None:
        return VelopackUpdateResult(
            current_version=current_version,
            latest_version=current_version,
            need_update=False,
            manager=manager,
        )
    return VelopackUpdateResult(
        current_version=current_version,
        latest_version=_target_version(update_info),
        need_update=True,
        manager=manager,
        update_info=update_info,
        notes=_target_notes(update_info),
    )


def download_updates(
    result: VelopackUpdateResult,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> None:
    if not result.update_info:
        return
    result.manager.download_updates(result.update_info, progress_callback)


def apply_updates_and_restart(result: VelopackUpdateResult) -> None:
    if not result.update_info:
        return
    result.manager.apply_updates_and_restart(result.update_info)
