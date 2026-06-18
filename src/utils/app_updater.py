"""
Small compatibility helpers shared by the settings UI.

The application update flow itself is handled by ``velopack_updater``.
This module only keeps edition labels and old update-state diagnostics so
existing logs remain readable after migrating away from the custom updater.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def get_runtime_root() -> Path:
    """Return the directory used for runtime logs and state files."""
    from src.core.config import get_runtime_root as _get_runtime_root

    return _get_runtime_root()


def get_update_state_file() -> Path:
    return get_runtime_root() / "update_state.json"


def get_update_state_cache_file() -> Path:
    return get_runtime_root() / "update_state.latest.json"


def get_update_log_file() -> Path:
    log_dir = get_runtime_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "update.log"


def _append_update_trace(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        get_update_log_file().open("a", encoding="utf-8").write(f"{timestamp} | {message}\n")
    except Exception:
        pass


def detect_current_edition() -> str:
    return "opensource"


def get_edition_label(edition: Optional[str] = None) -> str:
    return "开源版"


def _parse_update_state_file(state_file: Path) -> Optional[dict]:
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def describe_update_state(state: dict, current_version: str = "") -> Optional[dict]:
    """Convert legacy update state JSON into text shown by the settings page."""
    if not state:
        return None

    status = str(state.get("status", "")).strip().lower()
    target_version = str(state.get("target_version", "")).strip()
    detail = str(state.get("detail", "")).strip()
    previous_version = str(state.get("previous_version", "")).strip()
    updated_at = str(state.get("updated_at", "")).strip()

    base = {
        "raw_status": status or "unknown",
        "target_version": target_version,
        "previous_version": previous_version,
        "detail": detail,
        "updated_at": updated_at,
        "level": "info",
        "title": "最近更新",
        "message": detail or "暂无更新状态详情。",
    }

    if status in {"success", "copied", "starting_new", "launched"}:
        if current_version and target_version == current_version:
            base.update(
                level="info",
                title="更新成功",
                message=detail or f"软件已从 v{previous_version or '?'} 更新到 v{current_version}。",
            )
        else:
            base.update(level="info", title="已启动新版", message=detail or f"已启动目标版本 v{target_version or '?'}。")
        return base

    if status in {"rolled_back", "failed"}:
        message = detail or ("更新未能完成，已自动回滚到旧版本。" if status == "rolled_back" else "更新未能完成，请查看日志后重试。")
        base.update(level="warning", title="更新失败", message=message)
        return base

    if status in {"pending", "waiting_exit"}:
        if current_version and target_version == current_version:
            base.update(level="info", title="更新成功", message="已切换到新版本。")
        else:
            base.update(level="warning", title="等待替换", message=detail or "更新包已下载，等待进入替换阶段。")
        return base

    if status:
        base.update(level="info", title=f"状态: {status}", message=detail or "检测到更新状态记录。")
        return base

    return None


def read_update_result(current_version: str = "") -> Optional[dict]:
    state = _parse_update_state_file(get_update_state_file())
    if state is None:
        state = _parse_update_state_file(get_update_state_cache_file())
    if state is None:
        return None
    return describe_update_state(state, current_version=current_version)


def consume_update_result(current_version: str) -> Optional[dict]:
    state_file = get_update_state_file()
    state = _parse_update_state_file(state_file)
    if state is None:
        return None

    result = describe_update_state(state, current_version=current_version)
    state_file.unlink(missing_ok=True)
    logger.info(
        "读取更新结果: status=%s, previous=v%s, target=v%s, current=v%s, detail=%s",
        result.get("raw_status", "?") if result else "?",
        state.get("previous_version", "?") or "?",
        state.get("target_version", "?") or "?",
        current_version,
        state.get("detail", "-") or "-",
    )
    _append_update_trace(
        f"读取更新结果: status={result.get('raw_status', '?') if result else '?'}, "
        f"previous=v{state.get('previous_version', '?') or '?'}, "
        f"target=v{state.get('target_version', '?') or '?'}, current=v{current_version}, "
        f"detail={state.get('detail', '-') or '-'}"
    )
    if result:
        return result
    return {
        "level": "warning",
        "title": "更新状态异常",
        "message": "检测到无法识别的更新状态记录，请查看 update.log。",
        "raw_status": str(state.get("status", "")).strip().lower() or "unknown",
        "detail": str(state.get("detail", "")).strip(),
        "updated_at": str(state.get("updated_at", "")).strip(),
    }
