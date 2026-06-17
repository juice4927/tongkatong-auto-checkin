"""
Build and package TongKaTong with PyInstaller + Velopack.

Usage:
    python tools/build/build.py              # patch +1 and package
    python tools/build/build.py minor        # minor +1
    python tools/build/build.py major        # major +1
    python tools/build/build.py 2.3.0        # set exact version
    python tools/build/build.py 2.3.0 --publish-release

Optional environment variables:
    APP_UPDATE_NOTES     Markdown release notes for Velopack
    GITHUB_REPO_URL      Repository URL used by vpk upload github
                         default: https://github.com/juice4927/tongkatong-auto-checkin
    GITHUB_TOKEN         Token passed to vpk upload github, if needed
    VELOPACK_SIGN_PARAMS Parameters passed to signtool.exe via vpk --signParams
    VELOPACK_SIGN_TEMPLATE
                         Custom signing command passed to vpk --signTemplate
    VELOPACK_AZURE_TRUSTED_SIGN_FILE
                         Azure Trusted Signing metadata file passed to vpk
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from shutil import which


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent.parent
VERSION_FILE = ROOT / "src" / "version.py"
SPEC_FILE = ROOT / "tools" / "build" / "build.spec"
ICON_FILE = ROOT / "src" / "gui" / "assets" / "app_icon.ico"

PACK_ID = "TongKaTong"
PACK_TITLE = "通卡通"
PACK_AUTHORS = "TongKaTong contributors"
UPDATE_REPO_URL = "https://github.com/juice4927/tongkatong-auto-checkin"


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("无法从 version.py 读取版本号")
    return match.group(1)


def bump(current: str, part: str) -> str:
    major, minor, patch = map(int, current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def update_version_file(new_ver: str) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    text = re.sub(r'VERSION\s*=\s*"[^"]+"', f'VERSION = "{new_ver}"', text)
    text = re.sub(r'BUILD_DATE\s*=\s*"[^"]+"', f'BUILD_DATE = "{date.today()}"', text)
    VERSION_FILE.write_text(text, encoding="utf-8")


def update_spec_file(new_ver: str) -> None:
    text = SPEC_FILE.read_text(encoding="utf-8")
    text = re.sub(r"name='tongkatong_v[^']+'", f"name='tongkatong_v{new_ver}'", text)
    SPEC_FILE.write_text(text, encoding="utf-8")


def run_step(label: str, cmd: list[str]) -> None:
    print(f"\n执行检查: {label}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"检查失败: {label} (exit code {result.returncode})")
    print(f"检查通过: {label}")


def run_health_checks() -> None:
    run_step("compileall", [sys.executable, "-m", "compileall", "src", "tests"])
    run_step("unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    run_step(
        "import_smoke",
        [sys.executable, "-c", "import src.main; import src.gui.main_window; print('import_ok')"],
    )


def clean_pycache() -> None:
    import shutil

    for root, dirs, files in os.walk(str(ROOT)):
        if ".git" in root or "venv" in root or ".venv" in root:
            continue
        for dirname in list(dirs):
            if dirname == "__pycache__":
                shutil.rmtree(Path(root) / dirname, ignore_errors=True)
        for filename in files:
            if filename.endswith((".pyc", ".bak")):
                try:
                    (Path(root) / filename).unlink()
                except OSError:
                    pass


def dist_dir_for_version(ver: str) -> Path:
    base = ROOT / "dist" / "releases" / f"v{ver}"
    if not base.exists():
        return base
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "dist" / "releases" / f"v{ver}_{suffix}"


def format_size(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MiB"


def directory_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def find_vpk() -> str:
    detected = which("vpk")
    if detected:
        return detected

    fallback = Path.home() / ".dotnet" / "tools" / "vpk.exe"
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("未找到 Velopack CLI，请先运行: dotnet tool install -g vpk")


def write_release_notes(ver: str, release_dir: Path) -> Path:
    notes = (os.environ.get("APP_UPDATE_NOTES") or "").strip()
    if not notes:
        notes = (
            f"v{ver}\n\n"
            "- 使用 Velopack 安装和更新。\n"
            "- 公开版继续移除授权校验。\n"
        )

    notes_path = release_dir / f"release-notes-v{ver}.md"
    notes_path.write_text(notes, encoding="utf-8")
    return notes_path


def run_pyinstaller(ver: str, pyinstaller_dist_dir: Path) -> Path:
    print("\n开始 PyInstaller onedir 打包...")
    work_dir = ROOT / "build_out" / pyinstaller_dist_dir.parent.name / SPEC_FILE.stem
    work_dir.mkdir(parents=True, exist_ok=True)
    pyinstaller_dist_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(SPEC_FILE),
            "--clean",
            "--noconfirm",
            "--distpath",
            str(pyinstaller_dist_dir),
            "--workpath",
            str(work_dir),
        ],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller 打包失败 (exit code {result.returncode})")

    app_dir = pyinstaller_dist_dir / f"tongkatong_v{ver}"
    main_exe = app_dir / f"tongkatong_v{ver}.exe"
    if not main_exe.exists():
        raise RuntimeError(f"PyInstaller 产物缺少入口 exe: {main_exe}")

    print(f"PyInstaller 输出: {app_dir} ({format_size(directory_size(app_dir))})")
    return app_dir


def signing_args_from_env() -> list[str]:
    args: list[str] = []
    sign_params = os.environ.get("VELOPACK_SIGN_PARAMS", "").strip()
    sign_template = os.environ.get("VELOPACK_SIGN_TEMPLATE", "").strip()
    azure_trusted_sign_file = os.environ.get("VELOPACK_AZURE_TRUSTED_SIGN_FILE", "").strip()

    if sign_params and sign_template:
        raise RuntimeError("VELOPACK_SIGN_PARAMS 和 VELOPACK_SIGN_TEMPLATE 只能配置一个。")

    if sign_params:
        args.extend(["--signParams", sign_params])
    if sign_template:
        args.extend(["--signTemplate", sign_template])
    if azure_trusted_sign_file:
        args.extend(["--azureTrustedSignFile", azure_trusted_sign_file])
    return args


def build_velopack_pack_command(
    vpk: str,
    ver: str,
    app_dir: Path,
    output_dir: Path,
    release_notes: Path,
) -> list[str]:
    main_exe = f"tongkatong_v{ver}.exe"

    cmd = [
        vpk,
        "pack",
        "--packId",
        PACK_ID,
        "--packVersion",
        ver,
        "--packDir",
        str(app_dir),
        "--outputDir",
        str(output_dir),
        "--packTitle",
        PACK_TITLE,
        "--packAuthors",
        PACK_AUTHORS,
        "--mainExe",
        main_exe,
        "--releaseNotes",
        str(release_notes),
        "--channel",
        "win",
    ]
    if ICON_FILE.exists():
        cmd.extend(["--icon", str(ICON_FILE)])
    cmd.extend(signing_args_from_env())
    return cmd


def run_velopack_pack(ver: str, app_dir: Path, output_dir: Path, release_notes: Path) -> None:
    print("\n开始 Velopack 打包...")
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_velopack_pack_command(find_vpk(), ver, app_dir, output_dir, release_notes)
    if "--signParams" in cmd or "--signTemplate" in cmd or "--azureTrustedSignFile" in cmd:
        print("Velopack 签名已启用。")
    else:
        print("Velopack 签名未启用；如需签名，请配置 VELOPACK_SIGN_PARAMS 或 VELOPACK_SIGN_TEMPLATE。")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"Velopack 打包失败 (exit code {result.returncode})")

    assets = sorted(p for p in output_dir.iterdir() if p.is_file())
    if not assets:
        raise RuntimeError(f"Velopack 未生成发布资产: {output_dir}")

    print("Velopack 输出:")
    for asset in assets:
        print(f"  {asset.name} ({format_size(asset.stat().st_size)})")


def velopack_assets(output_dir: Path) -> list[Path]:
    patterns = ("*.exe", "*.nupkg", "*.json", "RELEASES*")
    assets: list[Path] = []
    for pattern in patterns:
        assets.extend(output_dir.glob(pattern))
    return sorted(set(assets))


def publish_release(ver: str, output_dir: Path) -> None:
    repo_url = os.environ.get("GITHUB_REPO_URL", UPDATE_REPO_URL).strip() or UPDATE_REPO_URL
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    tag = os.environ.get("GITHUB_RELEASE_TAG", "").strip() or f"v{ver}"
    vpk = find_vpk()

    assets = velopack_assets(output_dir)
    if not assets:
        raise RuntimeError(f"没有可发布的 Velopack 资产: {output_dir}")

    cmd = [
        vpk,
        "upload",
        "github",
        "--outputDir",
        str(output_dir),
        "--repoUrl",
        repo_url,
        "--publish",
        "true",
        "--merge",
        "true",
        "--tag",
        tag,
        "--releaseName",
        tag,
    ]
    if token:
        cmd.extend(["--token", token])

    print(f"\n开始发布 GitHub Release: {repo_url} {tag}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"Velopack 发布失败 (exit code {result.returncode})")
    print(f"Release 已发布: {repo_url}/releases/tag/{tag}")


def parse_args(args: list[str]) -> tuple[str, bool]:
    publish_enabled = "--publish-release" in args
    unsupported = {"--installer", "--delta", "--publish-public-update", "--all", "--keygen"} & set(args)
    if unsupported:
        joined = ", ".join(sorted(unsupported))
        raise RuntimeError(f"{joined} 已废弃；当前发布链路使用 Velopack。")

    clean_args = [arg for arg in args if arg != "--publish-release"]
    if len(clean_args) > 1:
        raise RuntimeError("用法: python tools/build/build.py [patch|minor|major|x.y.z] [--publish-release]")

    requested = clean_args[0] if clean_args else "patch"
    current = read_version()
    if re.match(r"^\d+\.\d+\.\d+$", requested):
        return requested, publish_enabled
    if requested in ("major", "minor", "patch"):
        return bump(current, requested), publish_enabled
    raise RuntimeError("用法: python tools/build/build.py [patch|minor|major|x.y.z] [--publish-release]")


def main() -> None:
    try:
        new_ver, publish_enabled = parse_args(sys.argv[1:])
        old_ver = read_version()
        print(f"版本升级: {old_ver} -> {new_ver}")
        print(f"构建日期: {date.today()}")

        update_version_file(new_ver)
        update_spec_file(new_ver)
        print(f"  已更新 {VERSION_FILE}")
        print(f"  已更新 {SPEC_FILE}")

        run_health_checks()
        clean_pycache()

        release_dir = dist_dir_for_version(new_ver)
        pyinstaller_dir = release_dir / "pyinstaller"
        velopack_dir = release_dir / "velopack"
        release_dir.mkdir(parents=True, exist_ok=True)

        notes_path = write_release_notes(new_ver, release_dir)
        app_dir = run_pyinstaller(new_ver, pyinstaller_dir)
        run_velopack_pack(new_ver, app_dir, velopack_dir, notes_path)

        if publish_enabled:
            publish_release(new_ver, velopack_dir)

        print(f"\n全部完成 v{new_ver}")
        print(f"产物目录: {release_dir}")
    except Exception as exc:
        print(f"\n构建失败: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
