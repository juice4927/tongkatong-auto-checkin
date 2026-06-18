"""
节假日数据更新模块
打包环境下无需系统 Python，直接下载 wheel 解压到 packages/ 目录。
重启后 main.py 会优先从 packages/ 加载，覆盖内置版本。
"""
import sys
import re
import logging
import urllib.request
import zipfile
import tempfile
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MIRRORS = [
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://repo.huaweicloud.com/repository/pypi/simple/",
    "https://pypi.mirrors.ustc.edu.cn/simple/",
]

_PACKAGE = "chinesecalendar"


def _get_packages_dir() -> Path:
    """获取 packages 目录（exe 同级）"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / "packages"
    return Path(__file__).parent.parent.parent / "packages"


def _version_tuple(v: str) -> tuple:
    """'1.11.0' -> (1, 11, 0)，用于比较，不依赖 packaging"""
    return tuple(int(x) for x in re.findall(r'\d+', v))


def _get_current_version() -> str:
    """获取当前 chinese_calendar 版本"""
    try:
        import chinese_calendar
        return getattr(chinese_calendar, '__version__', 'unknown')
    except ImportError:
        return 'not_installed'


def _fetch_latest_version(mirror: str, timeout: int = 15) -> str:
    """从镜像源 simple 页面解析最新版本号"""
    try:
        url = mirror.rstrip('/') + f'/{_PACKAGE}/'
        req = urllib.request.Request(url, headers={'User-Agent': 'pip/24.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode('utf-8', errors='ignore')
        versions = re.findall(rf'{_PACKAGE}-(\d+\.\d+(?:\.\d+)*)', html)
        if versions:
            return max(set(versions), key=_version_tuple)
    except Exception as e:
        logger.debug(f"镜像 {mirror} 查询失败: {e}")
    return ""


def _find_wheel_url(mirror: str, timeout: int = 15) -> str:
    """从镜像源找到最新 wheel 的下载地址"""
    try:
        url = mirror.rstrip('/') + f'/{_PACKAGE}/'
        req = urllib.request.Request(url, headers={'User-Agent': 'pip/24.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode('utf-8', errors='ignore')

        wheels = re.findall(r'href="([^"]*\.whl)[^"]*"', html)
        if not wheels:
            return ""

        py3_wheels = [w for w in wheels if 'py3' in w or 'py2.py3' in w]
        if py3_wheels:
            wheels = py3_wheels

        def wheel_ver(w):
            m = re.search(r'-(\d+\.\d+[\.\d]*)-', w)
            return _version_tuple(m.group(1)) if m else (0,)

        best = max(wheels, key=wheel_ver)

        if best.startswith('http'):
            return best
        if best.startswith('/'):
            from urllib.parse import urlparse
            parsed = urlparse(mirror)
            return f"{parsed.scheme}://{parsed.netloc}{best}"
        return mirror.rstrip('/').rsplit('/simple', 1)[0] + '/packages/' + best.lstrip('/')
    except Exception as e:
        logger.debug(f"镜像 {mirror} 查找 wheel 失败: {e}")
    return ""


def check_holiday_update():
    """检查是否有更新，返回 {current, latest, need_update} 或 None"""
    current = _get_current_version()

    latest = ""
    for mirror in _MIRRORS:
        latest = _fetch_latest_version(mirror)
        if latest:
            break

    if not latest:
        return None

    need_update = False
    if current in ('unknown', 'not_installed'):
        need_update = True
    else:
        need_update = _version_tuple(latest) > _version_tuple(current)

    return {'current': current, 'latest': latest, 'need_update': need_update}


def update_holiday_calendar() -> bool:
    """
    下载最新 wheel 并解压到 packages/ 目录。
    重启后 main.py 会优先从 packages/ 加载新版本。
    """
    pkg_dir = _get_packages_dir()
    pkg_dir.mkdir(parents=True, exist_ok=True)

    for mirror in _MIRRORS:
        wheel_url = _find_wheel_url(mirror)
        if not wheel_url:
            continue

        logger.info(f"下载 wheel: {wheel_url}")
        tmp_path = None
        try:
            # 下载 wheel（带超时控制）
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.whl')
            os.close(tmp_fd)
            req = urllib.request.Request(wheel_url)
            with urllib.request.urlopen(req, timeout=30) as response:
                with open(tmp_path, 'wb') as out_file:
                    out_file.write(response.read())

            # 清理旧版本
            for old in pkg_dir.glob("chinese_calendar*"):
                if old.is_dir():
                    import shutil
                    shutil.rmtree(old, ignore_errors=True)
                elif old.is_file():
                    old.unlink(missing_ok=True)

            with zipfile.ZipFile(tmp_path, 'r') as zf:
                for member in zf.namelist():
                    member_path = (pkg_dir / member).resolve()
                    if not str(member_path).startswith(str(pkg_dir.resolve())):
                        logger.warning(f"跳过不安全路径: {member}")
                        continue
                    zf.extract(member, str(pkg_dir))

            # 确保 packages/ 在 sys.path 最前面
            pkg_str = str(pkg_dir)
            if pkg_str in sys.path:
                sys.path.remove(pkg_str)
            sys.path.insert(0, pkg_str)

            logger.info(f"节假日数据已更新到 {pkg_dir}")
            return True

        except Exception as e:
            logger.warning(f"镜像 {mirror} 更新失败: {e}")
            continue
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.debug(f"清理临时文件失败: {e}")

    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    info = check_holiday_update()
    if info:
        print(f"当前: {info['current']}, 最新: {info['latest']}, 需更新: {info['need_update']}")
        if info['need_update']:
            print("开始更新...")
            ok = update_holiday_calendar()
            print(f"更新{'成功' if ok else '失败'}")
    else:
        print("无法检查更新")
