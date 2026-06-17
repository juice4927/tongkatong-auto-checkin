"""
应用更新模块

第一版采用“远端 version.json + exe 下载 + 外部脚本替换重启”的方式，
避免运行中的 Windows 进程直接覆盖自身文件。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)
_DOWNLOAD_CHUNK_SIZE = 1024 * 512
_DOWNLOAD_MAX_RETRIES = 4


class UpdateError(RuntimeError):
    """更新失败。"""


@dataclass
class UpdateAsset:
    version: str
    url: str
    file_name: str
    sha256: str = ""
    notes: str = ""
    published_at: str = ""
    size: int = 0
    # Delta (incremental) update fields
    delta_from_version: str = ""
    delta_url: str = ""
    delta_sha256: str = ""
    delta_size: int = 0


def _version_tuple(v: str) -> tuple[int, ...]:
    import re

    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums) if nums else (0,)


def get_runtime_root() -> Path:
    """获取运行根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def get_update_temp_dir() -> Path:
    update_dir = Path(tempfile.gettempdir()) / "tongkatong_updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    return update_dir


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
    line = f"{timestamp} | {message}\n"
    try:
        get_update_log_file().open("a", encoding="utf-8").write(line)
    except Exception:
        pass


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def detect_current_edition() -> str:
    """Return the release edition identifier used by update manifests."""
    return "opensource"


def get_edition_label(edition: Optional[str] = None) -> str:
    """返回当前版本的中文名称。"""
    return "开源版"


def _select_manifest_entry(entries: object, edition: str) -> Optional[dict]:
    if not isinstance(entries, dict):
        return None
    for key in (edition, "opensource", "default"):
        value = entries.get(key)
        if isinstance(value, dict):
            return value
    return None


def load_remote_manifest(manifest_url: str, timeout: int = 15) -> dict:
    if not manifest_url.strip():
        raise UpdateError("未配置更新清单地址")

    logger.info("开始拉取更新清单: %s", manifest_url)
    _append_update_trace(f"开始拉取更新清单: {manifest_url}")
    response = requests.get(manifest_url, timeout=timeout)
    response.raise_for_status()
    try:
        manifest = response.json()
        logger.info("更新清单拉取成功")
        _append_update_trace("更新清单拉取成功")
        return manifest
    except json.JSONDecodeError as e:
        logger.error("更新清单解析失败: %s", e)
        _append_update_trace(f"更新清单解析失败: {e}")
        raise UpdateError(f"更新清单不是有效 JSON: {e}") from e


def parse_update_asset(manifest: dict, edition: str, manifest_url: str) -> UpdateAsset:
    version = str(manifest.get("version", "")).strip()
    if not version:
        raise UpdateError("更新清单缺少 version")

    assets = manifest.get("assets")
    asset_info = _select_manifest_entry(assets, edition)
    if asset_info is None:
        if "url" in manifest:
            asset_info = manifest
        else:
            raise UpdateError("更新清单缺少可用下载项")

    url = str(asset_info.get("url", "")).strip()
    file_name = str(asset_info.get("file_name", "")).strip()
    if not file_name and url:
        file_name = Path(url.split("?", 1)[0]).name or "app_update.exe"
    if not file_name:
        raise UpdateError("更新清单缺少 file_name")
    if not url:
        url = urljoin(manifest_url, file_name)

    delta_info = manifest.get("delta")
    delta_asset = _select_manifest_entry(delta_info, edition) if delta_info else None

    return UpdateAsset(
        version=version,
        url=url,
        file_name=file_name,
        sha256=str(asset_info.get("sha256", "")).strip().lower(),
        notes=str(manifest.get("notes", "") or asset_info.get("notes", "") or "").strip(),
        published_at=str(manifest.get("published_at", "") or asset_info.get("published_at", "") or "").strip(),
        size=int(asset_info.get("size", 0) or 0),
        delta_from_version=str(delta_info.get("from_version", "")).strip() if isinstance(delta_info, dict) else "",
        delta_url=str((delta_asset or {}).get("url", "")).strip(),
        delta_sha256=str((delta_asset or {}).get("sha256", "")).strip().lower(),
        delta_size=int((delta_asset or {}).get("size", 0) or 0),
    )


def check_app_update(manifest_url: str, current_version: str, edition: str) -> dict:
    manifest = load_remote_manifest(manifest_url)
    asset = parse_update_asset(manifest, edition, manifest_url)
    need_update = _version_tuple(asset.version) > _version_tuple(current_version)
    logger.info(
        "检查更新完成: 当前=v%s, 最新=v%s, 版本=%s, 需要更新=%s",
        current_version,
        asset.version,
        edition,
        need_update,
    )
    _append_update_trace(
        f"检查更新完成: 当前=v{current_version}, 最新=v{asset.version}, 版本={edition}, 需要更新={need_update}"
    )
    return {
        "need_update": need_update,
        "current_version": current_version,
        "latest_version": asset.version,
        "asset": asset,
    }


def silent_check_and_pre_download(
    manifest_url: str,
    current_version: str,
    edition: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Optional[Path]:
    """
    Background silent check + pre-download.
    Checks for update, and if available, pre-downloads the package quietly.
    Returns path to downloaded file, or None if no update / download fails silently.

    Designed to be called from a background thread on startup.
    """
    try:
        manifest = load_remote_manifest(manifest_url, timeout=10)
        asset = parse_update_asset(manifest, edition, manifest_url)
        if _version_tuple(asset.version) <= _version_tuple(current_version):
            logger.info("Silent check: no update needed (current=v%s)", current_version)
            return None

        logger.info("Silent check: new version v%s found, pre-downloading...", asset.version)
        _report_download_status(status_callback, f"发现新版本 v{asset.version}，正在后台静默下载...")
        file_path = download_update_asset(asset, status_callback=status_callback, current_version=current_version)
        logger.info("Silent check: pre-download complete: v%s -> %s", asset.version, file_path)
        _report_download_status(status_callback, f"新版本 v{asset.version} 已就绪，下次启动将自动安装。")
        return file_path
    except Exception as e:
        logger.debug("Silent check: pre-download failed (will retry later): %s", e)
        return None


def _parse_update_state_file(state_file: Path) -> Optional[dict]:
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def describe_update_state(state: dict, current_version: str = "") -> Optional[dict]:
    """Simplify update state to 4 terminal states: success / failed / rolled_back / pending."""
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
        status_text = "success"
        if current_version and target_version == current_version:
            base.update(level="info", title="更新成功",
                        message=detail or f"软件已从 v{previous_version or '?'} 更新到 v{current_version}。")
        else:
            base.update(level="info", title="已启动新版",
                        message=detail or f"已启动目标版本 v{target_version or '?'}。")
        return base

    if status in {"rolled_back", "failed"}:
        base.update(level="warning", title="更新失败",
                    message=detail or "更新未能完成，已自动回滚到旧版本。" if status == "rolled_back" else "更新未能完成，请查看日志后重试。")
        return base

    if status in {"pending", "waiting_exit"}:
        target_ok = current_version and target_version == current_version
        if target_ok:
            base.update(level="info", title="更新成功", message="已切换到新版本。")
        else:
            base.update(level="warning", title="等待替换", message=detail or "更新包已下载，等待进入替换阶段。")
        return base

    if status:
        base.update(level="info", title=f"状态: {status}", message=detail or "检测到更新状态记录。")
        return base

    return None


def read_update_result(current_version: str = "") -> Optional[dict]:
    """
    读取最近一次更新状态，不消费状态文件，用于界面持续展示。
    优先读取当前状态文件，若不存在则读取最近一次缓存。
    """
    state = _parse_update_state_file(get_update_state_file())
    if state is None:
        state = _parse_update_state_file(get_update_state_cache_file())
    if state is None:
        return None
    return describe_update_state(state, current_version=current_version)


def consume_update_result(current_version: str) -> Optional[dict]:
    """
    读取并消费上一次自更新的结果，避免每次启动都重复提示。
    """
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
        f"读取更新结果: status={result.get('raw_status', '?') if result else '?'}, previous=v{state.get('previous_version', '?') or '?'}, target=v{state.get('target_version', '?') or '?'}, current=v{current_version}, detail={state.get('detail', '-') or '-'}"
    )
    if result:
        return result
    if state:
        return {
            "level": "warning",
            "title": "更新状态异常",
            "message": "检测到无法识别的更新状态记录，请查看 update.log。",
            "raw_status": str(state.get("status", "")).strip().lower() or "unknown",
            "detail": str(state.get("detail", "")).strip(),
            "updated_at": str(state.get("updated_at", "")).strip(),
        }
    return None


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _is_download_valid(file_path: Path, asset: UpdateAsset) -> bool:
    if not file_path.exists():
        return False
    if asset.size and file_path.stat().st_size != asset.size:
        return False
    if asset.sha256:
        return _sha256_file(file_path) == asset.sha256
    return file_path.stat().st_size > 0


def _get_partial_download_path(asset: UpdateAsset) -> Path:
    return get_update_temp_dir() / f"{asset.file_name}.part"


def _extract_total_size(headers: requests.structures.CaseInsensitiveDict, downloaded: int = 0) -> int:
    content_range = headers.get("content-range", "").strip()
    if "/" in content_range:
        try:
            return int(content_range.rsplit("/", 1)[1])
        except ValueError:
            pass
    content_length = headers.get("content-length", "").strip()
    if content_length:
        try:
            return int(content_length) + max(downloaded, 0)
        except ValueError:
            pass
    return 0


def _report_download_status(status_callback: Optional[Callable[[str], None]], message: str) -> None:
    if status_callback:
        try:
            status_callback(message)
        except Exception:
            pass


def _get_bspatch_path() -> Optional[Path]:
    """Locate the bundled bspatch.exe for applying delta patches."""
    # In frozen exe: bundled via spec datas -> _MEIPASS (single-file) or exe dir
    if is_frozen_runtime():
        candidates = []
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(Path(meipass) / "bspatch.exe")
        candidates.append(Path(sys.executable).parent / "bspatch.exe")
        candidates.append(get_runtime_root() / "bspatch.exe")
        for c in candidates:
            if c.exists():
                return c
    # In dev: project tools/delta/
    dev_path = Path(__file__).resolve().parents[2] / "tools" / "delta" / "bspatch.exe"
    if dev_path.exists():
        return dev_path
    return None


@dataclass
class _DeltaResult:
    """Result of a delta update attempt."""
    success: bool
    path: Optional[Path] = None
    error: str = ""


def _try_delta_update(asset: UpdateAsset, current_exe: Path) -> _DeltaResult:
    """
    Attempt a delta (incremental) update using bsdiff patch.
    Falls back gracefully by returning _DeltaResult(success=False) on any failure.
    """
    if not asset.delta_from_version or not asset.delta_url:
        return _DeltaResult(False, error="No delta patch available")

    bspatch = _get_bspatch_path()
    if not bspatch:
        return _DeltaResult(False, error="bspatch.exe not found")

    if not current_exe.exists():
        return _DeltaResult(False, error="Current exe not found for delta patching")

    temp_dir = get_update_temp_dir()
    patch_file = temp_dir / f"{asset.file_name}.patch"
    patched_file = temp_dir / f"{asset.file_name}.patched"

    _report_download_status(None, f"正在下载增量补丁 ({_format_size(asset.delta_size)})...")
    logger.info("Delta update: downloading patch from %s", asset.delta_url)
    _append_update_trace(f"Delta update: downloading patch from {asset.delta_url}")

    try:
        # Download patch with up to 3 retries
        for attempt in range(3):
            try:
                p = requests.get(asset.delta_url, timeout=30, stream=True)
                p.raise_for_status()
                with patch_file.open("wb") as f:
                    for chunk in p.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                break
            except requests.RequestException:
                if attempt == 2:
                    patch_file.unlink(missing_ok=True)
                    return _DeltaResult(False, error="Patch download failed after 3 attempts")
                logger.info("Delta patch download attempt %d failed, retrying...", attempt + 1)
                time.sleep(1)

        # Verify patch SHA256
        if asset.delta_sha256:
            actual = _sha256_file(patch_file)
            if actual != asset.delta_sha256:
                patch_file.unlink(missing_ok=True)
                return _DeltaResult(False, error=f"Patch SHA256 mismatch: expected {asset.delta_sha256}, got {actual}")

        logger.info("Delta update: patch downloaded, applying bspatch...")
        _append_update_trace("Delta update: applying bspatch...")

        result = subprocess.run(
            [str(bspatch), str(current_exe), str(patch_file), str(patched_file)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            patched_file.unlink(missing_ok=True)
            patch_file.unlink(missing_ok=True)
            stderr = (result.stderr or "").strip()[:200]
            return _DeltaResult(False, error=f"bspatch failed (exit {result.returncode}): {stderr}")

        if not patched_file.exists():
            return _DeltaResult(False, error="Patched file not created")

        # Verify patched file SHA256 against the full asset SHA256
        if asset.sha256:
            actual = _sha256_file(patched_file)
            if actual != asset.sha256:
                patched_file.unlink(missing_ok=True)
                patch_file.unlink(missing_ok=True)
                return _DeltaResult(False, error="Patched file SHA256 mismatch")

        # Clean up patch file
        patch_file.unlink(missing_ok=True)

        size_mb = patched_file.stat().st_size / 1024 / 1024
        logger.info(f"Delta update succeeded: {patched_file} ({size_mb:.1f} MB)")
        _append_update_trace(f"Delta update succeeded: {patched_file}")
        return _DeltaResult(True, path=patched_file)

    except (OSError, subprocess.TimeoutExpired) as e:
        patch_file.unlink(missing_ok=True)
        patched_file.unlink(missing_ok=True)
        return _DeltaResult(False, error=str(e))


def _format_size(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB" if size else ""


# ── 下载入口 ─────────────────────────────────────────────────

NEED_PATCH_DOWNLOAD = True  # marker for delta logic


def download_update_asset(
    asset: UpdateAsset,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
    current_version: str = "",
) -> Path:
    temp_dir = get_update_temp_dir()
    target_path = temp_dir / asset.file_name
    partial_path = _get_partial_download_path(asset)

    # ── Step 1: Try delta (incremental) update first ────────────
    if asset.delta_from_version and asset.delta_url:
        if current_version and current_version != asset.delta_from_version:
            logger.info("Delta update from v%s does not match current v%s, skipping", asset.delta_from_version, current_version)
        else:
            current_exe = Path(sys.executable) if is_frozen_runtime() else None
            if current_exe and current_exe.exists():
                logger.info("Delta update available (from v%s), attempting...", asset.delta_from_version)
                _append_update_trace(f"Delta update available (from v{asset.delta_from_version}), attempting...")
                _report_download_status(status_callback, f"检测到增量更新 (v{asset.delta_from_version})，正在应用...")
                result = _try_delta_update(asset, current_exe)
                if result.success and result.path:
                    logger.info("Delta update succeeded: %s", result.path)
                    _append_update_trace(f"Delta update succeeded: {result.path}")
                    _report_download_status(status_callback, "增量更新完成")
                    if progress_callback:
                        total = result.path.stat().st_size
                        progress_callback(total, total)
                    return result.path
                else:
                    logger.info("Delta update failed (%s), falling back to full download", result.error)
                    _append_update_trace(f"Delta update failed ({result.error}), falling back to full download")
                    _report_download_status(status_callback, "增量更新失败，回退到完整下载...")

    # ── Step 2: Full download (original logic) ──────────────────
    logger.info("开始下载完整更新包: %s -> %s", asset.url, target_path)
    _append_update_trace(f"开始下载更新包: {asset.url} -> {target_path}")

    if _is_download_valid(target_path, asset):
        logger.info("复用已下载完成的更新包: %s", target_path)
        _append_update_trace(f"复用已下载完成的更新包: {target_path}")
        _report_download_status(status_callback, "已复用已下载更新包")
        if progress_callback:
            total = asset.size or target_path.stat().st_size
            progress_callback(total, total)
        partial_path.unlink(missing_ok=True)
        return target_path

    target_path.unlink(missing_ok=True)

    last_error = ""
    for attempt in range(1, _DOWNLOAD_MAX_RETRIES + 1):
        resumed = False
        downloaded = partial_path.stat().st_size if partial_path.exists() else 0
        headers: dict[str, str] = {}
        if downloaded > 0:
            resumed = True
            headers["Range"] = f"bytes={downloaded}-"
            logger.info("检测到未完成下载，准备断点续传: 已有=%s 字节", downloaded)
            _append_update_trace(f"检测到未完成下载，准备断点续传: 已有={downloaded} 字节")
            _report_download_status(
                status_callback,
                f"正在续传（第{attempt}次，已下载 {downloaded / 1024 / 1024:.1f} MB）",
            )
        elif attempt > 1:
            _report_download_status(status_callback, f"下载失败，正在自动重试（第{attempt}次）")
        else:
            _report_download_status(status_callback, "正在下载更新包...")

        try:
            with requests.get(asset.url, timeout=30, stream=True, headers=headers) as response:
                if response.status_code == 416:
                    total = _extract_total_size(response.headers)
                    if downloaded > 0 and total and downloaded >= total:
                        logger.info("服务端返回 416，但本地分片已完整，直接进入校验")
                        _append_update_trace("服务端返回 416，但本地分片已完整，直接进入校验")
                    else:
                        partial_path.unlink(missing_ok=True)
                        raise UpdateError("服务端拒绝续传，本地缓存已失效，将重新下载")
                else:
                    response.raise_for_status()

                    if resumed and response.status_code != 206:
                        logger.warning("服务端未接受 Range 续传，回退为整包重新下载")
                        _append_update_trace("服务端未接受 Range 续传，回退为整包重新下载")
                        partial_path.unlink(missing_ok=True)
                        downloaded = 0
                        resumed = False

                    total = _extract_total_size(response.headers, downloaded)
                    if progress_callback:
                        progress_callback(downloaded, total)

                    mode = "ab" if resumed else "wb"
                    with partial_path.open(mode) as f:
                        for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total)

            if asset.size and partial_path.stat().st_size != asset.size:
                raise UpdateError(
                    f"下载文件大小不完整，期望 {asset.size} 字节，实际 {partial_path.stat().st_size} 字节"
                )

            if asset.sha256:
                actual = _sha256_file(partial_path)
                if actual != asset.sha256:
                    logger.error("更新包校验失败: 期望=%s, 实际=%s", asset.sha256, actual)
                    _append_update_trace(f"更新包校验失败: 期望={asset.sha256}, 实际={actual}")
                    if partial_path.exists():
                        partial_path.unlink(missing_ok=True)
                    raise UpdateError("下载文件校验失败，sha256 不匹配")

            partial_path.replace(target_path)
            if progress_callback:
                total = asset.size or target_path.stat().st_size
                progress_callback(total, total)
            logger.info("更新包下载完成: %s", target_path)
            _append_update_trace(f"更新包下载完成: {target_path}")
            _report_download_status(status_callback, "下载完成，准备安装...")
            return target_path
        except (requests.RequestException, OSError, UpdateError) as e:
            last_error = str(e)
            logger.warning("更新包下载失败，第 %s 次尝试: %s", attempt, e)
            _append_update_trace(f"更新包下载失败，第 {attempt} 次尝试: {e}")
            if attempt >= _DOWNLOAD_MAX_RETRIES:
                break
            if isinstance(e, UpdateError) and "sha256" in str(e).lower():
                partial_path.unlink(missing_ok=True)
            _report_download_status(status_callback, f"下载中断，准备自动续传（第{attempt + 1}次）")
            time.sleep(min(attempt, 3))

    raise UpdateError(f"更新包下载失败，已自动重试 {_DOWNLOAD_MAX_RETRIES} 次：{last_error or '未知错误'}")


def _write_update_state(status: str, target_version: str, previous_version: str, detail: str = "") -> None:
    state = {
        "status": status,
        "target_version": target_version,
        "previous_version": previous_version,
        "detail": detail,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    get_update_state_file().write_text(payload, encoding="utf-8")
    get_update_state_cache_file().write_text(payload, encoding="utf-8")
    logger.info(
        "更新状态写入: status=%s, previous=v%s, target=v%s, detail=%s",
        status,
        previous_version or "?",
        target_version or "?",
        detail or "-",
    )
    _append_update_trace(
        f"更新状态写入: status={status}, previous=v{previous_version or '?'}, target=v{target_version or '?'}, detail={detail or '-'}"
    )


def _build_self_update_bat_lines(
    source_path: Path,
    target_path: Path,
    state_path: Path,
    update_log_path: Path,
    current_pid: int,
    target_version: str,
    current_version: str,
) -> list[str]:
    return [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal enabledelayedexpansion",
        "",
        f'set "source={source_path}"',
        f'set "target={target_path}"',
        f'set "backup={target_path}.old"',
        f'set "state={state_path}"',
        f'set "log={update_log_path}"',
        f'set "pid={current_pid}"',
        f'set "targetVer={target_version}"',
        f'set "prevVer={current_version}"',
        "",
        "call :write_log \"Script started.\"",
        "call :write_state waiting_exit \"Waiting for old process to exit.\"",
        "",
        ":: Wait for old process (max 16s)",
        "for /l %%i in (1,1,16) do (",
        "    tasklist /fi \"pid eq %pid%\" 2>nul | findstr /r \"%pid%\" >nul",
        "    if errorlevel 1 goto :exited",
        "    ping -n 2 127.0.0.1 >nul",
        ")",
        "taskkill /f /pid %pid% 2>nul",
        "",
        ":exited",
        "",
        ":: Replace file (retry up to 40x)",
        "set attempt=0",
        ":retry_loop",
        "set /a attempt+=1",
        "if %attempt% gtr 40 (",
        "    exit /b 1",
        ")",
        "if exist \"%backup%\" del /f /q \"%backup%\" >nul 2>&1",
        "if exist \"%target%\" move /y \"%target%\" \"%backup%\" >nul 2>&1",
        "copy /y \"%source%\" \"%target%\" >nul 2>&1",
        "if not exist \"%target%\" (",
        "    call :write_state failed \"File copy failed\"",
        "    if exist \"%backup%\" (",
        "        copy /y \"%backup%\" \"%target%\" >nul 2>&1",
        "        call :write_state rolled_back \"Rolled back.\"",
        "    )",
        "    exit /b 1",
        ")",
        "call :write_state success \"New version file replaced, new program launched.\"",
        "",
        'start "" "%target%"',
        "",
        ":: Cleanup",
        "del /f /q \"%source%\" >nul 2>&1",
        "if exist \"%backup%\" del /f /q \"%backup%\" >nul 2>&1",
        "del /f /q %~f0 >nul 2>&1",
        "exit /b 0",
        "",
        ":write_log",
        "echo %date% %time% >> \"%log%\"",
        "echo %* >> \"%log%\"",
        "goto :eof",
        "",
        ":write_state",
        "echo { > \"%state%\"",
        "echo   \"status\": \"%~1\", >> \"%state%\"",
        "echo   \"target_version\": \"%targetVer%\", >> \"%state%\"",
        "echo   \"previous_version\": \"%prevVer%\", >> \"%state%\"",
        "echo   \"detail\": \"%~2\", >> \"%state%\"",
        "echo   \"updated_at\": \"%date% %time%\" >> \"%state%\"",
        "echo } >> \"%state%\"",
        "echo %date% %time% : status=%~1 : %~2 >> \"%log%\"",
        "goto :eof",
    ]


def launch_self_update(downloaded_file: Path, target_version: str, current_version: str) -> None:
    """
    Launch self-update via .bat script (hidden window via cmd /c start /min).
    .bat handles: wait for old process exit, backup, copy, rollback.
    """
    if not is_frozen_runtime():
        raise UpdateError("Development mode does not support self-replacement.")

    target_exe = Path(sys.executable)
    script_dir = Path(tempfile.mkdtemp(prefix="tongkatong_updater_"))
    bat_path = script_dir / "apply_update.bat"
    source_path = downloaded_file.resolve()
    target_path = target_exe.resolve()
    state_path = get_update_state_file().resolve()
    update_log_path = get_update_log_file().resolve()
    current_pid = os.getpid()

    logger.info(
        "Preparing self-update: source=%s, target=%s, current=v%s, target=v%s",
        source_path, target_path, current_version, target_version,
    )
    _append_update_trace(f"Preparing self-update: source={source_path}, target={target_path}")
    _write_update_state("pending", target_version, current_version, "Update package downloaded.")

    _bat_lines = _build_self_update_bat_lines(
        source_path=source_path,
        target_path=target_path,
        state_path=state_path,
        update_log_path=update_log_path,
        current_pid=current_pid,
        target_version=target_version,
        current_version=current_version,
    )
    bat_path.write_text("\r\n".join(_bat_lines), encoding="utf-8")

    # Launch batch directly via cmd /c start /min (hidden window, Unicode-safe, no VBS/PS deps)
    subprocess.Popen(
        ["cmd", "/c", "start", "/min", "", str(bat_path)],
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    logger.info("Update script launched: %s", bat_path)
    _append_update_trace(f"Update script launched: {bat_path}")
