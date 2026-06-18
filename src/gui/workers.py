"""
GUI 后台线程模块 — 从 settings.py 中提取的 QThread 子类

避免 UI 冻结，在后台线程中执行网络/ADB 操作。
"""
from PyQt6.QtCore import QThread, pyqtSignal

from src.utils.velopack_updater import (
    VelopackUpdateResult,
    check_for_updates,
    download_updates,
)


class HolidayCheckWorker(QThread):
    """检查节假日更新的后台线程"""
    done = pyqtSignal(object)  # info dict or None

    def run(self):
        from src.utils.holiday_updater import check_holiday_update
        try:
            self.done.emit(check_holiday_update())
        except Exception:
            self.done.emit(None)


class HolidayUpdateWorker(QThread):
    """执行节假日更新的后台线程"""
    done = pyqtSignal(bool)

    def run(self):
        from src.utils.holiday_updater import update_holiday_calendar
        try:
            self.done.emit(update_holiday_calendar())
        except Exception:
            self.done.emit(False)


class AppUpdateCheckWorker(QThread):
    """检查软件更新的后台线程"""

    done = pyqtSignal(object, object)  # result, error

    def __init__(self, source_url: str):
        super().__init__()
        self._source_url = source_url

    def run(self):
        try:
            result = check_for_updates(self._source_url)
            self.done.emit(result, None)
        except Exception as e:
            self.done.emit(None, str(e))


class AppUpdateDownloadWorker(QThread):
    """下载软件更新包的后台线程"""

    progress = pyqtSignal(int, int)  # downloaded percent, total percent
    status = pyqtSignal(str)  # status text
    done = pyqtSignal(object, object)  # result, error

    def __init__(self, result: VelopackUpdateResult):
        super().__init__()
        self._result = result

    def run(self):
        try:
            self.status.emit("正在下载更新包...")

            def _progress(percent):
                self.progress.emit(int(percent), 100)

            download_updates(self._result, _progress)
            self.done.emit(self._result, None)
        except Exception as e:
            self.done.emit(None, str(e))


class AdbConnectWorker(QThread):
    """测试 ADB 连接的后台线程"""
    done = pyqtSignal(bool, str)  # success, message

    def __init__(self, adb_path, host, port):
        super().__init__()
        self._adb_path = adb_path
        self._host = host
        self._port = port

    def run(self):
        try:
            from src.utils.adb_helper import ADBHelper
            adb = ADBHelper(self._adb_path) if self._adb_path else ADBHelper()
            success, message = adb.connect(self._host, self._port)
            if success:
                info = adb.get_device_info()
                detail = (f"\n设备: {info.get('brand','Unknown')} {info.get('model','Unknown')}"
                          f"\nAndroid: {info.get('android_version','Unknown')}")
                self.done.emit(True, message + detail)
            else:
                self.done.emit(False, message)
        except Exception as e:
            self.done.emit(False, str(e))


class AdbListPackagesWorker(QThread):
    """列出设备已安装包名的后台线程"""
    done = pyqtSignal(bool, list, str)  # success, packages, error_msg

    def __init__(self, adb_path, host, port):
        super().__init__()
        self._adb_path = adb_path
        self._host = host
        self._port = port

    def run(self):
        try:
            from src.utils.adb_helper import ADBHelper
            adb = ADBHelper(self._adb_path) if self._adb_path else ADBHelper()
            success, message = adb.connect(self._host, self._port)
            if not success:
                self.done.emit(False, [], "请先连接设备")
                return
            packages = adb.list_packages()
            self.done.emit(True, packages, "")
        except Exception as e:
            self.done.emit(False, [], str(e))
