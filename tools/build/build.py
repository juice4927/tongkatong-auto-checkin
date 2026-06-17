"""
打包脚本 - 自动升级版本号并打包
用法:
    python tools/build/build.py              # patch +1, 打包开源版
    python tools/build/build.py minor        # minor +1
    python tools/build/build.py major        # major +1
    python tools/build/build.py 2.0.0        # 指定版本号
    python tools/build/build.py 2.2.5 --publish-release
    python tools/build/build.py 2.2.5 --publish-public-update
    python tools/build/build.py 2.2.5 --installer     # 升级版本 + 打包 exe + 生成安装包
    python tools/build/build.py 2.2.5 --delta        # 升级版本 + 打包 exe + 生成增量补丁
    python tools/build/build.py 2.2.5 --delta --installer  # 全部

可选环境变量:
    APP_UPDATE_BASE_URL      更新文件公共前缀，例如 https://example.com/downloads
    APP_UPDATE_NOTES         写入 version.json 的更新说明
    GITHUB_OWNER             GitHub 用户或组织名
    GITHUB_REPO              GitHub 仓库名
    GITHUB_RELEASE_TAG       Release 标签，默认 v<版本号>
    GITHUB_RELEASE_ASSET_BASE 自定义 Release 资源前缀，优先级高于 owner/repo/tag
    PUBLIC_UPDATE_GITHUB_OWNER 公开更新仓库所属用户或组织
    PUBLIC_UPDATE_GITHUB_REPO  公开更新仓库名
    PUBLIC_UPDATE_GITHUB_RELEASE_TAG 公开更新 Release 标签，默认 v<版本号>
"""
import os
import re
import subprocess
import sys
import json
import hashlib
import base64
import tempfile
import time
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent.parent
VERSION_FILE = ROOT / "src" / "version.py"
SPEC_FILE = ROOT / "tools" / "build" / "build.spec"
GH_QUERY_TIMEOUT = int(os.environ.get("GH_QUERY_TIMEOUT", "60"))
GH_MUTATION_TIMEOUT = int(os.environ.get("GH_MUTATION_TIMEOUT", "1800"))
GH_RETRY_COUNT = int(os.environ.get("GH_RETRY_COUNT", "2"))


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    if not m:
        raise RuntimeError("无法从 version.py 读取版本号")
    return m.group(1)


def bump(current: str, part: str) -> str:
    major, minor, patch = map(int, current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def update_version_file(new_ver: str):
    text = VERSION_FILE.read_text(encoding="utf-8")
    text = re.sub(r'VERSION\s*=\s*"[^"]+"', f'VERSION = "{new_ver}"', text)
    text = re.sub(r'BUILD_DATE\s*=\s*"[^"]+"', f'BUILD_DATE = "{date.today()}"', text)
    VERSION_FILE.write_text(text, encoding="utf-8")


def _update_spec_name(spec_file: Path, pattern: str, replacement: str):
    text = spec_file.read_text(encoding="utf-8")
    text = re.sub(pattern, replacement, text)
    spec_file.write_text(text, encoding="utf-8")


def update_spec_files(new_ver: str):
    """同步所有 spec 文件里的版本号。"""
    _update_spec_name(SPEC_FILE, r"name='tongkatong_v[^']+'", f"name='tongkatong_v{new_ver}'")


def run_step(label: str, cmd: list[str]) -> bool:
    print(f"\n执行检查: {label}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode == 0:
        print(f"检查通过: {label}")
        return True
    print(f"检查失败: {label} (exit code {result.returncode})")
    return False


def run_health_checks() -> bool:
    checks = [
        ("compileall", [sys.executable, "-m", "compileall", "src", "tests"]),
        ("unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]),
        (
            "import_smoke",
            [sys.executable, "-c", "import src.main; import src.gui.main_window; print('import_ok')"],
        ),
    ]
    for label, cmd in checks:
        if not run_step(label, cmd):
            return False
    return True


def _clean_pycache():
    """清理 __pycache__ 和 .pyc 文件。"""
    import shutil

    for root, dirs, files in os.walk(str(ROOT)):
        if ".git" in root or "venv" in root or ".venv" in root:
            continue
        for d in dirs:
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        for f in files:
            if f.endswith(".pyc") or f.endswith(".bak"):
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass


def _dist_dir_for_version(ver: str) -> Path:
    base = ROOT / "dist" / "releases" / f"v{ver}"
    if not base.exists():
        return base
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "dist" / "releases" / f"v{ver}_{suffix}"


def _find_previous_release(ver: str) -> Optional[str]:
    """Scan dist/releases/ for the most recent version < ver."""
    releases_dir = ROOT / "dist" / "releases"
    if not releases_dir.exists():
        return None
    current_tuple = tuple(int(x) for x in ver.split("."))
    best = None
    best_tuple = (0, 0, 0)
    for entry in releases_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name.lstrip("v")
        try:
            vt = tuple(int(x) for x in name.split("."))
        except ValueError:
            continue
        if vt < current_tuple and vt > best_tuple:
            best = name
            best_tuple = vt
    return best


def _generate_delta_patches(ver: str, prev_ver: str, dist_dir: Path) -> Optional[dict]:
    """Generate bsdiff delta patches from previous release's exe to the current one.
    
    Returns a delta manifest dict, or None if generation fails.
    """
    bsdiff_path = ROOT / "tools" / "delta" / "bsdiff.exe"
    if not bsdiff_path.exists():
        print("  跳过增量补丁: 未找到 bsdiff.exe")
        return None

    prev_dir = ROOT / "dist" / "releases" / f"v{prev_ver}"
    if not prev_dir.exists():
        print(f"  跳过增量补丁: 未找到前版本目录 v{prev_ver}")
        return None

    base_url = _resolve_update_base_url(ver)
    delta_assets = {}
    editions = [
        ("opensource", f"tongkatong_v{ver}.exe", f"tongkatong_v{prev_ver}.exe",
         f"tongkatong_v{prev_ver}_to_v{ver}_opensource.patch"),
    ]

    for edition, cur_name, prev_name, patch_name in editions:
        cur_file = dist_dir / cur_name
        prev_file = prev_dir / prev_name
        patch_file = dist_dir / patch_name

        if not cur_file.exists():
            print(f"  跳过 {edition} 增量补丁: 当前 exe 不存在")
            continue
        if not prev_file.exists():
            print(f"  跳过 {edition} 增量补丁: 前版本 exe 不存在 -> {prev_file}")
            continue

        print(f"  生成 {edition} 增量补丁: bsdiff {prev_name} → {cur_name} ...")
        result = subprocess.run(
            [str(bsdiff_path), str(prev_file), str(cur_file), str(patch_file)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  增量补丁生成失败 ({edition}): {result.stderr.strip()}")
            patch_file.unlink(missing_ok=True)
            continue

        patch_size = patch_file.stat().st_size
        saved_mb = (cur_file.stat().st_size - patch_size) / 1024 / 1024
        print(f"  增量补丁完成: {patch_name} ({_format_size(patch_size)}, 节省 {saved_mb:.0f} MB)")

        delta_assets[edition] = {
            "file_name": patch_name,
            "sha256": _sha256_file(patch_file),
            "size": patch_size,
            "url": (base_url.rstrip("/") + "/" + patch_name) if base_url else "",
        }

    if not delta_assets:
        return None
    return {
        "from_version": prev_ver,
        **delta_assets,
    }


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _public_release_asset_name(ver: str) -> str:
    configured = os.environ.get("PUBLIC_UPDATE_ASSET_NAME", "").strip()
    if configured:
        return configured
    return f"tongkatong_v{ver}.exe"


def _release_asset_name(ver: str, edition: str) -> str:
    return f"tongkatong_v{ver}.exe"


def _resolve_github_asset_base(ver: str) -> str:
    custom_base = os.environ.get("GITHUB_RELEASE_ASSET_BASE", "").strip()
    if custom_base:
        return custom_base.rstrip("/")

    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    env_tag = os.environ.get("GITHUB_RELEASE_TAG", "").strip()
    tag = env_tag if env_tag == f"v{ver}" else f"v{ver}"
    if owner and repo:
        return f"https://github.com/{owner}/{repo}/releases/download/{tag}"
    return ""


def _resolve_update_base_url(ver: str) -> str:
    base_url = os.environ.get("APP_UPDATE_BASE_URL", "").strip()
    if base_url:
        return base_url.rstrip("/")
    return _resolve_github_asset_base(ver)


def _resolve_public_update_base_url(ver: str) -> str:
    custom_base = os.environ.get("PUBLIC_UPDATE_BASE_URL", "").strip()
    if custom_base:
        return custom_base.rstrip("/")

    owner = os.environ.get("PUBLIC_UPDATE_GITHUB_OWNER", "").strip()
    repo = os.environ.get("PUBLIC_UPDATE_GITHUB_REPO", "").strip()
    env_tag = os.environ.get("PUBLIC_UPDATE_GITHUB_RELEASE_TAG", "").strip()
    tag = env_tag if env_tag == f"v{ver}" else f"v{ver}"
    if owner and repo:
        return f"https://github.com/{owner}/{repo}/releases/download/{tag}"
    return ""


def _build_manifest_payload(ver: str, dist_dir: Path, base_url: str, delta: Optional[dict] = None) -> dict:
    app_file = dist_dir / f"tongkatong_v{ver}.exe"
    manifest = {
        "version": ver,
        "build_date": str(date.today()),
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "notes": os.environ.get("APP_UPDATE_NOTES", "").strip(),
        "assets": {
            "opensource": {
                "file_name": _release_asset_name(ver, "opensource"),
                "sha256": _sha256_file(app_file),
                "size": app_file.stat().st_size,
                "url": base_url.rstrip("/") + "/" + _release_asset_name(ver, "opensource") if base_url else "",
            },
        },
    }
    if delta:
        manifest["delta"] = delta
    return manifest


def _build_public_manifest_payload(ver: str, dist_dir: Path, base_url: str, delta: Optional[dict] = None) -> dict:
    src_file = dist_dir / f"tongkatong_v{ver}.exe"
    asset_name = _public_release_asset_name(ver)
    manifest = {
        "version": ver,
        "build_date": str(date.today()),
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "notes": os.environ.get("APP_UPDATE_NOTES", "").strip(),
        "assets": {
            "opensource": {
                "file_name": asset_name,
                "sha256": _sha256_file(src_file),
                "size": src_file.stat().st_size,
                "url": base_url.rstrip("/") + "/" + asset_name if base_url else "",
            },
        },
    }
    if delta and "opensource" in delta:
        public_delta = dict(delta["opensource"])
        if base_url and public_delta.get("file_name"):
            public_delta["url"] = base_url.rstrip("/") + "/" + public_delta["file_name"]
        manifest["delta"] = {
            "from_version": delta["from_version"],
            "opensource": public_delta,
        }
    return manifest


def _format_size(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MB"


def _validate_release_assets(ver: str, dist_dir: Path) -> dict[str, Path]:
    assets = {
        "opensource": dist_dir / f"tongkatong_v{ver}.exe",
        "manifest": dist_dir / "version.json",
        "public_manifest": dist_dir / "version.public.json",
    }

    missing = [f"{name}: {path}" for name, path in assets.items() if not path.exists()]
    if missing:
        raise RuntimeError("发布前资产校验失败，缺少以下文件:\n" + "\n".join(missing))

    manifest = json.loads(assets["manifest"].read_text(encoding="utf-8"))
    public_manifest = json.loads(assets["public_manifest"].read_text(encoding="utf-8"))

    if manifest.get("version") != ver:
        raise RuntimeError("version.json 中的版本号与当前构建版本不一致")
    if public_manifest.get("version") != ver:
        raise RuntimeError("version.public.json 中的版本号与当前构建版本不一致")

    return assets


def _build_release_notes(ver: str, dist_dir: Path) -> str:
    manifest = json.loads((dist_dir / "version.json").read_text(encoding="utf-8"))
    lines = [
        f"v{ver} 发布",
        "",
        "包含资产：",
        f"- 开源版: {manifest['assets']['opensource']['file_name']} ({_format_size(manifest['assets']['opensource']['size'])})",
        "- 更新清单: version.json",
    ]
    notes = (manifest.get("notes") or "").strip()
    if notes:
        lines.extend(["", "更新说明：", notes])
    return "\n".join(lines)


def _build_public_release_notes(ver: str, dist_dir: Path) -> str:
    manifest = json.loads((dist_dir / "version.public.json").read_text(encoding="utf-8"))
    asset = manifest["assets"]["opensource"]
    lines = [
        f"v{ver} 公开更新源",
        "",
        "包含资产：",
        f"- 开源版: {asset['file_name']} ({_format_size(asset['size'])})",
        "- 更新清单: version.json",
    ]
    notes = (manifest.get("notes") or "").strip()
    if notes:
        lines.extend(["", "更新说明：", notes])
    return "\n".join(lines)


def write_manifest_template(dist_dir: Path):
    template = {
        "version": "2.2.3",
        "build_date": "2026-06-09",
        "published_at": "2026-06-09T12:00:00",
        "notes": "这里填写本次版本更新说明",
        "assets": {
            "opensource": {
                "file_name": "tongkatong_v2.2.3.exe",
                "sha256": "替换为开源版 exe 的 sha256",
                "size": 123456789,
                "url": "https://github.com/<owner>/<repo>/releases/download/v2.2.3/tongkatong_v2.2.3.exe",
            },
        },
    }
    template_path = dist_dir / "version.template.json"
    template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已生成更新模板: {template_path}")


def write_public_manifest_template(dist_dir: Path):
    template = {
        "version": "2.2.3",
        "build_date": "2026-06-09",
        "published_at": "2026-06-09T12:00:00",
        "notes": "这里填写本次版本更新说明",
        "assets": {
            "opensource": {
                "file_name": "tongkatong_v2.2.3.exe",
                "sha256": "替换为开源版 exe 的 sha256",
                "size": 123456789,
                "url": "https://github.com/<public-update-repo>/releases/download/v2.2.3/tongkatong_v2.2.3.exe",
            }
        },
    }
    template_path = dist_dir / "version.public.template.json"
    template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已生成公开更新模板: {template_path}")


def write_update_manifest(ver: str, dist_dir: Path, generate_delta: bool = False):
    """
    在发布目录生成 version.json，便于后续直接上传到 GitHub Raw / Release 附件地址。
    可通过环境变量 APP_UPDATE_BASE_URL 注入下载前缀。
    """
    base_url = _resolve_update_base_url(ver)
    public_base_url = _resolve_public_update_base_url(ver) or base_url

    # 生成增量补丁
    delta = None
    if generate_delta:
        prev_ver = _find_previous_release(ver)
        if prev_ver:
            print(f"  检测到前版本: v{prev_ver}，开始生成增量补丁...")
            delta = _generate_delta_patches(ver, prev_ver, dist_dir)
            if delta:
                print(f"  增量补丁生成完成 (from v{prev_ver})")
        else:
            print("  未检测到前版本，跳过增量补丁")

    manifest = _build_manifest_payload(ver, dist_dir, base_url, delta=delta)
    manifest_path = dist_dir / "version.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已生成更新清单: {manifest_path}")

    public_manifest = _build_public_manifest_payload(ver, dist_dir, public_base_url, delta=delta)
    public_manifest_path = dist_dir / "version.public.json"
    public_manifest_path.write_text(json.dumps(public_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已生成公开更新清单: {public_manifest_path}")

    if base_url:
        print(f"  更新地址前缀: {base_url}")
    if public_base_url and public_base_url != base_url:
        print(f"  公开更新地址前缀: {public_base_url}")
    write_manifest_template(dist_dir)
    write_public_manifest_template(dist_dir)


def _find_gh_executable() -> str:
    from shutil import which

    gh_path = os.environ.get("GH_PATH", "").strip()
    if gh_path and Path(gh_path).exists():
        return gh_path

    detected = which("gh")
    if detected:
        return detected

    fallback = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("未找到 GitHub CLI，请先安装 gh 或设置 GH_PATH")


def _gh_command_text(cmd: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in cmd])


def _gh_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env["GH_NO_EXTENSION_UPDATE_NOTIFIER"] = "1"
    env["PAGER"] = ""
    env["GH_PAGER"] = ""
    return env


def _format_process_output(output: str | None) -> str:
    text = (output or "").strip()
    return text if text else "<empty>"


def _run_gh(
    cmd: list[str],
    *,
    label: str,
    timeout: int,
    retries: int = 0,
    retry_delay: float = 2.0,
    capture_output: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    last_error: Exception | None = None
    total_attempts = max(retries, 0) + 1

    for attempt in range(1, total_attempts + 1):
        print(f"执行 {label} ({attempt}/{total_attempts}): {_gh_command_text(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                capture_output=capture_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=_gh_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _format_process_output(getattr(exc, "stdout", None))
            stderr = _format_process_output(getattr(exc, "stderr", None))
            last_error = RuntimeError(
                f"{label} 超时（>{timeout} 秒）\n命令: {_gh_command_text(cmd)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        else:
            if result.returncode == 0:
                if capture_output:
                    stdout = (result.stdout or "").strip()
                    if stdout:
                        print(stdout)
                return result

            stdout = _format_process_output(result.stdout if capture_output else None)
            stderr = _format_process_output(result.stderr if capture_output else None)
            last_error = RuntimeError(
                f"{label} 失败，exit code {result.returncode}\n命令: {_gh_command_text(cmd)}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
            if not check:
                return result

        if attempt < total_attempts:
            print(f"{label} 失败，{retry_delay:.0f} 秒后重试...")
            time.sleep(retry_delay)

    assert last_error is not None
    raise last_error


def _gh_release_exists(gh: str, repo_name: str, tag: str) -> bool:
    result = _run_gh(
        [gh, "release", "view", tag, "--repo", repo_name],
        label=f"检查 Release 是否存在 {repo_name} {tag}",
        timeout=GH_QUERY_TIMEOUT,
        retries=max(GH_RETRY_COUNT - 1, 0),
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _sync_public_latest_manifest(gh: str, repo_name: str, ver: str, public_manifest: Path):
    """同步公开仓库根目录 version.json，提供稳定下载地址。"""
    api_path = f"repos/{repo_name}/contents/version.json"
    current_sha = ""

    query = _run_gh(
        [gh, "api", api_path],
        label=f"查询公开仓库 latest manifest {repo_name}",
        timeout=GH_QUERY_TIMEOUT,
        retries=max(GH_RETRY_COUNT - 1, 0),
        capture_output=True,
        check=False,
    )
    if query.returncode == 0:
        try:
            current_sha = str(json.loads(query.stdout or "{}").get("sha", "") or "").strip()
        except json.JSONDecodeError:
            current_sha = ""

    cmd = [
        gh,
        "api",
        "--method",
        "PUT",
        api_path,
        "-f",
        f"message=chore: update latest manifest to v{ver}",
        "-f",
        "branch=main",
        "-f",
        f"content={base64.b64encode(public_manifest.read_bytes()).decode('ascii')}",
    ]
    if current_sha:
        cmd.extend(["-f", f"sha={current_sha}"])

    _run_gh(
        cmd,
        label=f"同步公开仓库 latest manifest {repo_name}",
        timeout=GH_MUTATION_TIMEOUT,
        retries=max(GH_RETRY_COUNT - 1, 0),
        capture_output=True,
    )
    print(f"公开仓库最新清单已同步: https://raw.githubusercontent.com/{repo_name}/main/version.json")


def publish_release(ver: str, dist_dir: Path):
    import shutil

    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    env_tag = os.environ.get("GITHUB_RELEASE_TAG", "").strip()
    tag = env_tag if env_tag == f"v{ver}" else f"v{ver}"
    notes = _build_release_notes(ver, dist_dir)

    if not owner or not repo:
        raise RuntimeError("缺少 GITHUB_OWNER / GITHUB_REPO，无法发布 Release")

    gh = _find_gh_executable()
    repo_name = f"{owner}/{repo}"
    assets = _validate_release_assets(ver, dist_dir)
    app_asset = assets["opensource"]
    manifest = assets["manifest"]

    staging_dir = Path(tempfile.mkdtemp(prefix="tongkatong_release_"))
    staged_app = staging_dir / _release_asset_name(ver, "opensource")
    staged_manifest = staging_dir / "version.json"
    staged_assets = [str(staged_app), str(staged_manifest)]
    staged_app.write_bytes(app_asset.read_bytes())
    staged_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    exists = _gh_release_exists(gh, repo_name, tag)

    if exists:
        cmd = [
            gh,
            "release",
            "upload",
            tag,
            *staged_assets,
            "--clobber",
            "--repo",
            repo_name,
        ]
        action = "更新"
    else:
        cmd = [
            gh,
            "release",
            "create",
            tag,
            *staged_assets,
            "--repo",
            repo_name,
            "--title",
            tag,
            "--notes",
            notes,
        ]
        action = "创建"

    print(f"\n开始{action} Release: {repo_name} {tag}")
    try:
        _run_gh(
            cmd,
            label=f"{action} Release {repo_name} {tag}",
            timeout=GH_MUTATION_TIMEOUT,
            retries=max(GH_RETRY_COUNT - 1, 0) if action == "更新" else 0,
            capture_output=True,
        )
        print(f"Release 已{action}: https://github.com/{repo_name}/releases/tag/{tag}")
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def publish_public_update_release(ver: str, dist_dir: Path):
    import shutil

    owner = os.environ.get("PUBLIC_UPDATE_GITHUB_OWNER", "").strip()
    repo = os.environ.get("PUBLIC_UPDATE_GITHUB_REPO", "").strip()
    env_tag = os.environ.get("PUBLIC_UPDATE_GITHUB_RELEASE_TAG", "").strip()
    tag = env_tag if env_tag == f"v{ver}" else f"v{ver}"
    notes = _build_public_release_notes(ver, dist_dir)

    if not owner or not repo:
        raise RuntimeError("缺少 PUBLIC_UPDATE_GITHUB_OWNER / PUBLIC_UPDATE_GITHUB_REPO，无法发布公开更新")

    gh = _find_gh_executable()
    repo_name = f"{owner}/{repo}"
    assets = _validate_release_assets(ver, dist_dir)
    release_asset = assets["opensource"]
    public_manifest = assets["public_manifest"]
    public_asset_name = _public_release_asset_name(ver)

    # 收集 delta patch 文件（如果存在）
    delta_patches = sorted(dist_dir.glob("tongkatong_v*_to_v*_opensource.patch"))

    staging_dir = Path(tempfile.mkdtemp(prefix="tongkatong_public_release_"))
    staged_asset = staging_dir / public_asset_name
    staged_manifest = staging_dir / "version.json"
    staged_asset.write_bytes(release_asset.read_bytes())
    staged_manifest.write_text(public_manifest.read_text(encoding="utf-8"), encoding="utf-8")

    # 复制 delta patch 到 staging
    staged_patches = []
    for pf in delta_patches:
        sp = staging_dir / pf.name
        sp.write_bytes(pf.read_bytes())
        staged_patches.append(sp)

    upload_files = [str(staged_asset), str(staged_manifest)] + [str(sp) for sp in staged_patches]

    exists = _gh_release_exists(gh, repo_name, tag)

    if exists:
        cmd = [
            gh,
            "release",
            "upload",
            tag,
            *upload_files,
            "--clobber",
            "--repo",
            repo_name,
        ]
        action = "更新"
    else:
        cmd = [
            gh,
            "release",
            "create",
            tag,
            *upload_files,
            "--repo",
            repo_name,
            "--title",
            tag,
            "--notes",
            notes,
        ]
        action = "创建"

    print(f"\n开始{action}公开更新 Release: {repo_name} {tag}")
    try:
        _run_gh(
            cmd,
            label=f"{action}公开更新 Release {repo_name} {tag}",
            timeout=GH_MUTATION_TIMEOUT,
            retries=max(GH_RETRY_COUNT - 1, 0) if action == "更新" else 0,
            capture_output=True,
        )
        _sync_public_latest_manifest(gh, repo_name, ver, public_manifest)
        print(f"公开更新 Release 已{action}: https://github.com/{repo_name}/releases/tag/{tag}")
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def run_pyinstaller(spec_path: Path, label: str, dist_dir: Path) -> bool:
    print(f"\n开始打包 {label} ...")
    work_dir = ROOT / "build_out" / dist_dir.name / spec_path.stem
    work_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(spec_path),
            "--clean",
            "--noconfirm",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_dir),
        ],
        cwd=str(ROOT),
    )
    if result.returncode == 0:
        print(f"打包完成: {label}\n输出目录: {dist_dir}")
        return True
    print(f"打包失败: {label} (exit code {result.returncode})")
    return False


def run_innosetup(ver: str, dist_dir: Path) -> bool:
    """
    使用 Inno Setup 生成安装包。
    安装包生成到 dist_dir 同级目录（dist/releases/ 下）。
    需要先安装 Inno Setup：https://jrsoftware.org/isdl.php
    """
    iss_path = ROOT / "tools" / "installer" / "setup.iss"
    if not iss_path.exists():
        print(f"  跳过安装包: 未找到 {iss_path}")
        return False

    # 查找 ISCC.exe
    iscc_candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup\ISCC.exe"),
    ]
    iscc = None
    for candidate in iscc_candidates:
        if candidate.exists():
            iscc = candidate
            break

    if iscc is None:
        env_iscc = os.environ.get("ISCC_PATH", "").strip()
        if env_iscc:
            p = Path(env_iscc)
            if p.exists():
                iscc = p

    if iscc is None:
        print("  跳过安装包: 未找到 Inno Setup (ISCC.exe)，请安装 https://jrsoftware.org/isdl.php")
        print(f"  安装后可通过环境变量 ISCC_PATH 指定路径")
        return False

    print(f"\n开始生成安装包 (Inno Setup) ...")
    installer_dir = ROOT / "dist" / "releases"
    installer_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            str(iscc),
            f"/DAppVersion={ver}",
            f"/O{installer_dir}",
            str(iss_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # 从输出中提取安装包文件名
        for line in (result.stdout or "").splitlines():
            if "Output" in line and ".exe" in line:
                print(f"  {line.strip()}")
        print(f"安装包生成完成\n输出目录: {installer_dir}")
        return True

    print(f"安装包生成失败 (exit code {result.returncode})")
    if result.stdout:
        print(result.stdout[-2000:])
    if result.stderr:
        print(result.stderr[-2000:])
    return False


def main():
    args = sys.argv[1:]

    removed_args = {"--keygen", "--all"} & set(args)
    if removed_args:
        print("授权生成器和双版本打包已移除；当前项目只构建开源版。")
        print("用法: python tools/build/build.py [patch|minor|major|x.y.z] [--installer] [--delta]")
        sys.exit(1)

    build_installer = "--installer" in args
    build_delta = "--delta" in args
    publish_public_update = "--publish-public-update" in args
    publish_release_enabled = "--publish-release" in args
    args = [
        a for a in args
        if a not in ("--installer", "--delta", "--publish-public-update", "--publish-release")
    ]

    ver = read_version()

    arg = args[0] if args else "patch"
    if re.match(r"^\d+\.\d+\.\d+$", arg):
        new_ver = arg
    elif arg in ("major", "minor", "patch"):
        new_ver = bump(ver, arg)
    else:
        print("用法: python tools/build/build.py [patch|minor|major|x.y.z] [--installer] [--delta]")
        sys.exit(1)

    print(f"版本升级: {ver} -> {new_ver}")
    print(f"构建日期: {date.today()}")

    update_version_file(new_ver)
    print(f"  已更新 {VERSION_FILE.name}")

    update_spec_files(new_ver)
    print(f"  已更新 {SPEC_FILE.name}")
    ver = new_ver

    if not run_health_checks():
        sys.exit(2)

    _clean_pycache()

    dist_dir = _dist_dir_for_version(ver)

    ok = True
    ok = run_pyinstaller(SPEC_FILE, f"tongkatong_v{ver}.exe", dist_dir)

    if ok:
        write_update_manifest(ver, dist_dir, generate_delta=build_delta)
        if build_installer:
            run_innosetup(ver, dist_dir)
        if publish_release_enabled:
            publish_release(ver, dist_dir)
        if publish_public_update:
            publish_public_update_release(ver, dist_dir)
        print(f"\n全部完成 v{ver}\n产物目录: {dist_dir}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
