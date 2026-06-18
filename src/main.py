"""
通卡通 - 程序入口
"""
import sys
import os
from pathlib import Path


def get_base_path():
    """获取基础路径（兼容打包和开发环境）"""
    if getattr(sys, 'frozen', False):
        # 打包后的环境
        return Path(sys.executable).parent
    else:
        # 开发环境
        return Path(__file__).parent.parent


def get_resource_path(relative_path):
    """获取资源文件路径（用于打包后的资源）"""
    if getattr(sys, 'frozen', False):
        # 打包后，资源在 _MEIPASS 目录
        base_path = Path(sys._MEIPASS)
    else:
        # 开发环境
        base_path = Path(__file__).parent.parent
    
    return base_path / relative_path


# 设置基础路径
BASE_PATH = get_base_path()

# 添加项目根目录到路径
sys.path.insert(0, str(BASE_PATH))

# 打包环境：packages/ 目录用于节假日数据热更新
# 必须在 import chinese_calendar 之前设置，且用 importlib 确保优先级高于 FrozenImporter
if getattr(sys, 'frozen', False):
    _pkg_dir = BASE_PATH / 'packages'
    _pkg_dir.mkdir(exist_ok=True)
    _pkg_str = str(_pkg_dir)
    if _pkg_str not in sys.path:
        sys.path.insert(0, _pkg_str)
    # 如果 packages/ 里有 chinese_calendar，预先加载它（覆盖内置版本）
    _cc_init = _pkg_dir / 'chinese_calendar' / '__init__.py'
    if _cc_init.exists():
        import importlib, importlib.util
        _spec = importlib.util.spec_from_file_location('chinese_calendar', str(_cc_init),
            submodule_search_locations=[str(_pkg_dir / 'chinese_calendar')])
        if _spec and _spec.loader:
            _mod = importlib.util.module_from_spec(_spec)
            sys.modules['chinese_calendar'] = _mod
            _spec.loader.exec_module(_mod)

# 设置工作目录（确保配置文件读写正确）
os.chdir(str(BASE_PATH))


from src.utils.logger import setup_logging
from src.utils.app_updater import get_edition_label
from src.utils.velopack_updater import run_startup_update_hooks
from src.gui.main_window import main


def _check_single_instance():
    """
    检查是否已有实例在运行（Windows 命名 Mutex，不可继承）
    """
    import ctypes
    import ctypes.wintypes

    _MUTEX_NAME = "Global\\TongKaTong_SingleInstance_v1"

    # 用 SECURITY_ATTRIBUTES 创建不可继承的 Mutex
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", ctypes.wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", ctypes.wintypes.BOOL),
        ]

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(sa)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = False  # 不可继承

    mutex = ctypes.windll.kernel32.CreateMutexW(
        ctypes.byref(sa), True, _MUTEX_NAME
    )
    last_err = ctypes.windll.kernel32.GetLastError()

    if last_err == 183:  # ERROR_ALREADY_EXISTS — 已有实例
        ctypes.windll.kernel32.CloseHandle(mutex)
        return False

    # 保持句柄，进程退出时自动释放
    _check_single_instance._mutex = mutex
    return True


if __name__ == "__main__":
    run_startup_update_hooks()

    # 设置日志目录
    # 打包后 console=False，sys.stdout 为 None，禁用控制台输出
    _console_output = sys.stdout is not None
    try:
        log_dir = BASE_PATH / "logs"
        log_dir_str = str(log_dir) if log_dir else None
        if sys.stderr is not None:
            print(f"日志目录: {log_dir_str}", file=sys.stderr)
        
        # 生产环境（打包）使用 INFO 级别，开发环境使用 DEBUG
        log_level = "INFO" if getattr(sys, 'frozen', False) else "DEBUG"
        
        if log_dir_str:
            log_path = Path(log_dir_str)
            log_path.mkdir(parents=True, exist_ok=True)
            setup_logging(log_dir=str(log_path), log_level=log_level, console_output=_console_output)
        else:
            setup_logging(log_dir=None, log_level=log_level, console_output=_console_output)
    except Exception as e:
        if sys.stderr is not None:
            print(f"日志设置失败: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
        setup_logging(log_dir=None, log_level="INFO", console_output=False)

    # 检查重复启动
    if not _check_single_instance():
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.warning(
            None, f"通卡通 {get_edition_label()}",
            f"通卡通{get_edition_label()}已经在运行中！\n\n请检查系统托盘，不要重复启动。"
        )
        sys.exit(0)

    # 启动应用
    main()
