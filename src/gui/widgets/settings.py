"""
设置组件
"""
import logging
import sys
import subprocess
import zipfile
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QApplication,
    QLineEdit, QPushButton, QCheckBox, QFileDialog, QMessageBox,
    QSpinBox, QListWidget, QListWidgetItem, QDialog, QDialogButtonBox,
    QGridLayout,
    QScrollArea, QSizePolicy, QProgressBar
)
from PyQt6.QtCore import pyqtSignal, QThread, Qt
from pathlib import Path

from src.core.config import DEFAULT_PUBLIC_UPDATE_MANIFEST_URL
from src.utils.app_updater import (
    UpdateAsset,
    UpdateError,
    consume_update_result,
    detect_current_edition,
    get_edition_label,
    get_runtime_root,
    get_update_log_file,
    get_update_state_cache_file,
    get_update_state_file,
    launch_self_update,
    read_update_result,
)
from src.version import VERSION
from src.gui.workers import (
    HolidayCheckWorker,
    HolidayUpdateWorker,
    AppUpdateCheckWorker,
    AppUpdateDownloadWorker,
    AdbConnectWorker,
    AdbListPackagesWorker,
)

logger = logging.getLogger(__name__)


_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "通卡通"
_TASK_NAME = "TongKaTong-AutoStart"
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _get_python_windowless_executable() -> str:
    """源码模式下优先使用 pythonw，避免开机自启时弹控制台。"""
    exe_path = Path(sys.executable)
    if exe_path.name.lower() == "python.exe":
        pythonw_path = exe_path.with_name("pythonw.exe")
        if pythonw_path.exists():
            return str(pythonw_path)
    return sys.executable

def _get_startup_target() -> str:
    """获取启动目标路径"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return str(Path(__file__).resolve().parents[2] / "main.py")


def _get_startup_command() -> str:
    """获取开机自启使用的完整命令"""
    target = _get_startup_target()
    if getattr(sys, 'frozen', False):
        return f'"{target}"'
    return f'"{_get_python_windowless_executable()}" "{target}"'


def _is_boot_startup_enabled() -> bool:
    """检查开机自启是否启用（注册表 + 任务计划程序）"""
    # 检查注册表
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            winreg.QueryValueEx(key, _REG_NAME)
            return True
    except Exception:
        pass
    
    # 检查任务计划程序
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK_NAME, "/V", "/FO", "LIST"],
            capture_output=True, text=True, timeout=10,
            creationflags=_SUBPROCESS_FLAGS,
        )
        return "Ready" in result.stdout or "准备就绪" in result.stdout
    except Exception:
        return False


def _set_boot_startup(enable: bool):
    """设置开机自启（优先使用任务计划程序，回退到注册表）"""
    if enable:
        # 尝试使用任务计划程序（更可靠，支持管理员权限）
        startup_cmd = _get_startup_command()
        try:
            subprocess.run(
                ["schtasks", "/Create", "/TN", _TASK_NAME, "/TR", startup_cmd,
                 "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
                capture_output=True, text=True, timeout=10, check=True,
                creationflags=_SUBPROCESS_FLAGS,
            )
            return
        except subprocess.CalledProcessError:
            pass  # 回退到注册表
        
        # 回退到注册表方式
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, startup_cmd)
        except Exception as e:
            raise Exception(f"设置开机自启失败: {e}")
    else:
        # 同时清理注册表和任务计划程序
        try:
            subprocess.run(
                ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
                capture_output=True, text=True, timeout=10,
                creationflags=_SUBPROCESS_FLAGS,
            )
        except Exception:
            pass
        
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, _REG_NAME)
                except FileNotFoundError:
                    pass
        except Exception:
            pass


class SettingsWidget(QWidget):
    """设置组件"""

    config_changed = pyqtSignal()

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._label_width = 96
        self._pending_update_info = None
        self._setup_ui()
        self._load_config()

    def _field_label(self, text: str, width: int | None = None) -> QLabel:
        label = QLabel(text)
        label.setMinimumWidth(width or self._label_width)
        label.setStyleSheet("color: #4B5563;")
        return label

    def _hint_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #616161; font-size: 11px;")
        label.setWordWrap(True)
        return label

    def _setup_ui(self):
        """设置界面"""
        # 外层布局 + ScrollArea，防止内容被截断
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        content_grid = QGridLayout()
        content_grid.setHorizontalSpacing(6)
        content_grid.setVerticalSpacing(6)
        content_grid.setColumnStretch(0, 1)
        content_grid.setColumnStretch(1, 1)
        content_grid.setRowStretch(0, 1)
        content_grid.setRowStretch(1, 1)

        # ── 交建通 APP 设置（单独一行）────────────────────────────
        app_group = QGroupBox("交建通 APP 设置")
        app_layout = QHBoxLayout(app_group)
        app_layout.setSpacing(5)
        app_layout.setContentsMargins(8, 10, 8, 6)
        app_layout.addWidget(self._field_label("应用包名:"))
        self.package_edit = QLineEdit()
        self.package_edit.setPlaceholderText("例如: com.tencent.weworklocal")
        self.package_edit.setMinimumHeight(28)
        app_layout.addWidget(self.package_edit)
        self.detect_btn = QPushButton("自动检测")
        self.detect_btn.setFixedWidth(88)
        self.detect_btn.setToolTip("列出模拟器中已安装的应用，选择交建通即可")
        self.detect_btn.clicked.connect(self._detect_package)
        app_layout.addWidget(self.detect_btn)
        layout.addWidget(app_group)

        # ── MuMu 模拟器设置 ──────────────────────────────────────
        mumu_group = QGroupBox("MuMu 模拟器设置")
        mumu_layout = QVBoxLayout(mumu_group)
        mumu_layout.setSpacing(5)
        mumu_layout.setContentsMargins(8, 10, 8, 6)

        # ADB 路径
        adb_layout = QHBoxLayout()
        adb_layout.setSpacing(6)
        adb_layout.addWidget(self._field_label("ADB 路径:"))
        self.adb_path_edit = QLineEdit()
        self.adb_path_edit.setPlaceholderText("留空则自动查找 MuMu 自带的 adb")
        self.adb_path_edit.setMinimumHeight(28)
        adb_layout.addWidget(self.adb_path_edit)
        self.browse_adb_btn = QPushButton("浏览...")
        self.browse_adb_btn.setFixedWidth(68)
        self.browse_adb_btn.clicked.connect(self._browse_adb)
        adb_layout.addWidget(self.browse_adb_btn)
        mumu_layout.addLayout(adb_layout)

        # MuMu 安装路径
        mumu_exe_layout = QHBoxLayout()
        mumu_exe_layout.setSpacing(6)
        mumu_exe_layout.addWidget(self._field_label("安装目录:"))
        self.mumu_exe_path_edit = QLineEdit()
        self.mumu_exe_path_edit.setPlaceholderText("留空则自动查找（C/D盘 Program Files）")
        self.mumu_exe_path_edit.setMinimumHeight(28)
        mumu_exe_layout.addWidget(self.mumu_exe_path_edit)
        self.browse_mumu_btn = QPushButton("浏览...")
        self.browse_mumu_btn.setFixedWidth(68)
        self.browse_mumu_btn.clicked.connect(self._browse_mumu_exe)
        mumu_exe_layout.addWidget(self.browse_mumu_btn)
        mumu_layout.addLayout(mumu_exe_layout)

        # GPS 坐标
        gps_layout = QHBoxLayout()
        gps_layout.setSpacing(6)
        gps_layout.addWidget(self._field_label("打卡 GPS:"))
        gps_layout.addWidget(self._field_label("纬度", 36))
        self.gps_lat_edit = QLineEdit()
        self.gps_lat_edit.setPlaceholderText("如 31.3191")
        self.gps_lat_edit.setFixedWidth(104)
        self.gps_lat_edit.setMinimumHeight(28)
        gps_layout.addWidget(self.gps_lat_edit)
        gps_layout.addWidget(self._field_label("经度", 36))
        self.gps_lon_edit = QLineEdit()
        self.gps_lon_edit.setPlaceholderText("如 120.5583")
        self.gps_lon_edit.setFixedWidth(104)
        self.gps_lon_edit.setMinimumHeight(28)
        gps_layout.addWidget(self.gps_lon_edit)
        gps_layout.addStretch()
        mumu_layout.addLayout(gps_layout)
        self.gps_lat_edit.setToolTip("留空则不设置，可直接填入高德坐标")
        self.gps_lon_edit.setToolTip("留空则不设置，可直接填入高德坐标")

        # 主机 / 端口
        conn_layout = QHBoxLayout()
        conn_layout.setSpacing(6)
        conn_layout.addWidget(self._field_label("主机地址:"))
        self.host_edit = QLineEdit()
        self.host_edit.setText("127.0.0.1")
        self.host_edit.setFixedWidth(128)
        self.host_edit.setMinimumHeight(28)
        conn_layout.addWidget(self.host_edit)
        conn_layout.addSpacing(4)
        conn_layout.addWidget(self._field_label("端口:", 44))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(5555)
        self.port_spin.setFixedWidth(88)
        self.port_spin.setMinimumHeight(28)
        conn_layout.addWidget(self.port_spin)
        conn_layout.addStretch()
        mumu_layout.addLayout(conn_layout)

        # 测试连接
        test_conn_layout = QHBoxLayout()
        test_conn_layout.setSpacing(6)
        self.test_conn_btn = QPushButton("测试连接")
        self.test_conn_btn.setFixedWidth(88)
        self.test_conn_btn.clicked.connect(self._test_connection)
        test_conn_layout.addWidget(self.test_conn_btn)

        self.copy_diag_btn = QPushButton("复制诊断信息")
        self.copy_diag_btn.setFixedWidth(104)
        self.copy_diag_btn.clicked.connect(self._copy_diagnostics)
        test_conn_layout.addWidget(self.copy_diag_btn)
        self.export_diag_btn = QPushButton("导出日志包")
        self.export_diag_btn.setFixedWidth(104)
        self.export_diag_btn.clicked.connect(self._export_diagnostics_bundle)
        test_conn_layout.addWidget(self.export_diag_btn)

        test_conn_layout.addStretch()
        mumu_layout.addLayout(test_conn_layout)

        mumu_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── 通知设置 ────────────────────────────────────────────
        notify_group = QGroupBox("通知设置")
        notify_layout = QVBoxLayout(notify_group)
        notify_layout.setSpacing(5)
        notify_layout.setContentsMargins(8, 10, 8, 6)

        notify_toggle_row = QHBoxLayout()
        notify_toggle_row.setSpacing(10)
        self.notify_cb = QCheckBox("启用通知（Server酱推送到微信）")
        self.notify_cb.setChecked(False)
        notify_toggle_row.addWidget(self.notify_cb)

        self.verify_tls_cb = QCheckBox("验证 HTTPS 证书")
        self.verify_tls_cb.setChecked(True)
        self.verify_tls_cb.setEnabled(False)
        notify_toggle_row.addWidget(self.verify_tls_cb)
        notify_toggle_row.addStretch()
        notify_layout.addLayout(notify_toggle_row)

        webhook_layout = QHBoxLayout()
        webhook_layout.setSpacing(6)
        webhook_layout.addWidget(self._field_label("Server酱 Key:"))
        self.webhook_edit = QLineEdit()
        self.webhook_edit.setPlaceholderText("SCTxxxxxxxx（从 sct.ftqq.com 获取）")
        self.webhook_edit.setEnabled(False)
        self.webhook_edit.setMinimumHeight(28)
        self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Password)
        webhook_layout.addWidget(self.webhook_edit)
        self.webhook_toggle_btn = QPushButton("显示")
        self.webhook_toggle_btn.setFixedWidth(60)
        self.webhook_toggle_btn.setEnabled(False)
        self.webhook_toggle_btn.clicked.connect(self._toggle_webhook_echo)
        webhook_layout.addWidget(self.webhook_toggle_btn)
        notify_layout.addLayout(webhook_layout)
        self.verify_tls_cb.setToolTip("建议保持开启，仅在证书异常时再关闭")

        self.notify_cb.stateChanged.connect(self._on_notify_state_changed)

        notify_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── 启动行为 ─────────────────────────────────────────────
        startup_group = QGroupBox("启动行为")
        startup_layout = QVBoxLayout(startup_group)
        startup_layout.setSpacing(5)
        startup_layout.setContentsMargins(8, 10, 8, 6)

        startup_toggle_row = QHBoxLayout()
        startup_toggle_row.setSpacing(8)
        self.auto_connect_cb = QCheckBox("启动时自动连接设备")
        startup_toggle_row.addWidget(self.auto_connect_cb)
        self.auto_start_cb = QCheckBox("连接成功后自动开启打卡")
        startup_toggle_row.addWidget(self.auto_start_cb)
        startup_toggle_row.addStretch()
        startup_layout.addLayout(startup_toggle_row)

        keep_alive_row = QHBoxLayout()
        keep_alive_row.setSpacing(8)
        self.keep_alive_cb = QCheckBox("始终保持运行（常驻守护断线恢复）")
        keep_alive_row.addWidget(self.keep_alive_cb)
        self.boot_startup_cb = QCheckBox("开机自启（登录后自动启动）")
        self.boot_startup_cb.stateChanged.connect(self._on_boot_startup_changed)
        keep_alive_row.addWidget(self.boot_startup_cb)
        keep_alive_row.addStretch()
        startup_layout.addLayout(keep_alive_row)
        if not getattr(sys, 'frozen', False):
            self.boot_startup_cb.setEnabled(False)
            self.boot_startup_cb.setToolTip("仅打包版可用（exe）")

        backoff_layout = QHBoxLayout()
        backoff_layout.setSpacing(6)
        backoff_layout.addWidget(self._field_label("恢复基准退避:", 110))
        self.recovery_base_backoff_spin = QSpinBox()
        self.recovery_base_backoff_spin.setRange(0, 3600)
        self.recovery_base_backoff_spin.setValue(5)
        self.recovery_base_backoff_spin.setSuffix(" 秒")
        self.recovery_base_backoff_spin.setMinimumWidth(88)
        self.recovery_base_backoff_spin.setMinimumHeight(28)
        backoff_layout.addWidget(self.recovery_base_backoff_spin)
        backoff_layout.addWidget(self._field_label("最大退避:", 90))
        self.recovery_max_backoff_spin = QSpinBox()
        self.recovery_max_backoff_spin.setRange(0, 86400)
        self.recovery_max_backoff_spin.setValue(300)
        self.recovery_max_backoff_spin.setSuffix(" 秒")
        self.recovery_max_backoff_spin.setMinimumWidth(88)
        self.recovery_max_backoff_spin.setMinimumHeight(28)
        backoff_layout.addWidget(self.recovery_max_backoff_spin)
        backoff_layout.addStretch()
        startup_layout.addLayout(backoff_layout)

        policy_layout = QHBoxLayout()
        policy_layout.setSpacing(6)
        policy_layout.addWidget(self._field_label("连续失败阈值:", 110))
        self.recovery_max_failures_spin = QSpinBox()
        self.recovery_max_failures_spin.setRange(0, 1000)
        self.recovery_max_failures_spin.setValue(20)
        self.recovery_max_failures_spin.setToolTip("0 表示不限制")
        self.recovery_max_failures_spin.setMinimumWidth(88)
        self.recovery_max_failures_spin.setMinimumHeight(28)
        policy_layout.addWidget(self.recovery_max_failures_spin)
        policy_layout.addWidget(self._field_label("触发后暂停:", 90))
        self.recovery_pause_minutes_spin = QSpinBox()
        self.recovery_pause_minutes_spin.setRange(1, 1440)
        self.recovery_pause_minutes_spin.setValue(30)
        self.recovery_pause_minutes_spin.setSuffix(" 分钟")
        self.recovery_pause_minutes_spin.setMinimumWidth(88)
        self.recovery_pause_minutes_spin.setMinimumHeight(28)
        policy_layout.addWidget(self.recovery_pause_minutes_spin)
        policy_layout.addStretch()
        startup_layout.addLayout(policy_layout)

        quiet_layout = QHBoxLayout()
        quiet_layout.setSpacing(6)
        self.recovery_quiet_enabled_cb = QCheckBox("启用恢复静默时段")
        quiet_layout.addWidget(self.recovery_quiet_enabled_cb)
        quiet_layout.addWidget(self._field_label("开始小时:", 72))
        self.recovery_quiet_start_spin = QSpinBox()
        self.recovery_quiet_start_spin.setRange(0, 23)
        self.recovery_quiet_start_spin.setSuffix(" 时")
        self.recovery_quiet_start_spin.setMinimumWidth(72)
        self.recovery_quiet_start_spin.setMinimumHeight(28)
        quiet_layout.addWidget(self.recovery_quiet_start_spin)
        quiet_layout.addWidget(self._field_label("结束小时:", 72))
        self.recovery_quiet_end_spin = QSpinBox()
        self.recovery_quiet_end_spin.setRange(0, 23)
        self.recovery_quiet_end_spin.setSuffix(" 时")
        self.recovery_quiet_end_spin.setMinimumWidth(72)
        self.recovery_quiet_end_spin.setMinimumHeight(28)
        quiet_layout.addWidget(self.recovery_quiet_end_spin)
        quiet_layout.addStretch()
        startup_layout.addLayout(quiet_layout)
        startup_group.setToolTip("建议先开启自动连接，再按需开启自动打卡与守护恢复")

        startup_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── 节假日设置 ──────────────────────────────────────────
        holiday_group = QGroupBox("节假日设置")
        holiday_layout = QVBoxLayout(holiday_group)
        holiday_layout.setSpacing(5)
        holiday_layout.setContentsMargins(8, 10, 8, 6)

        holiday_toggle_row = QHBoxLayout()
        holiday_toggle_row.setSpacing(8)
        self.skip_weekend_cb = QCheckBox("跳过周末")
        self.skip_weekend_cb.setChecked(True)
        holiday_toggle_row.addWidget(self.skip_weekend_cb)
        self.skip_holiday_cb = QCheckBox("跳过法定节假日")
        self.skip_holiday_cb.setChecked(True)
        holiday_toggle_row.addWidget(self.skip_holiday_cb)
        holiday_toggle_row.addStretch()
        holiday_layout.addLayout(holiday_toggle_row)

        update_layout = QHBoxLayout()
        update_layout.setSpacing(6)
        self.holiday_version_label = QLabel("节假日数据: 加载中...")
        update_layout.addWidget(self.holiday_version_label)
        update_layout.addStretch()
        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.setFixedWidth(80)
        self.check_update_btn.clicked.connect(self._check_holiday_update)
        update_layout.addWidget(self.check_update_btn)
        holiday_layout.addLayout(update_layout)

        extra_layout = QHBoxLayout()
        extra_layout.setSpacing(6)
        self.extra_workdays_btn = QPushButton("添加工作日")
        self.extra_workdays_btn.clicked.connect(self._add_extra_workday)
        extra_layout.addWidget(self.extra_workdays_btn)
        self.extra_holidays_btn = QPushButton("添加休息日")
        self.extra_holidays_btn.clicked.connect(self._add_extra_holiday)
        extra_layout.addWidget(self.extra_holidays_btn)
        self.remove_extra_date_btn = QPushButton("删除选中")
        self.remove_extra_date_btn.clicked.connect(self._remove_selected_extra_date)
        extra_layout.addWidget(self.remove_extra_date_btn)
        extra_layout.addStretch()
        holiday_layout.addLayout(extra_layout)

        self.extra_dates_list = QListWidget()
        self.extra_dates_list.setMaximumHeight(84)
        self.extra_dates_list.itemDoubleClicked.connect(lambda _: self._remove_selected_extra_date())
        holiday_layout.addWidget(self.extra_dates_list)
        holiday_layout.addWidget(
            self._hint_label("支持按日期范围批量添加；手动日期优先于系统节假日规则，重复添加会自动去重，同一天改成另一种类型会自动替换。双击或选中后可删除。")
        )

        holiday_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        content_grid.addWidget(mumu_group, 0, 0)
        content_grid.addWidget(startup_group, 0, 1)
        content_grid.addWidget(notify_group, 1, 0)
        content_grid.addWidget(holiday_group, 1, 1)
        layout.addLayout(content_grid)

        # ── 软件更新 ─────────────────────────────────────────────
        update_group = QGroupBox("软件更新")
        update_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        update_group_layout = QVBoxLayout(update_group)
        update_group_layout.setSpacing(5)
        update_group_layout.setContentsMargins(8, 10, 8, 6)

        version_row = QHBoxLayout()
        version_row.setSpacing(6)
        self.app_version_label = QLabel(f"当前版本: v{VERSION}")
        version_row.addWidget(self.app_version_label)
        version_row.addStretch()
        self.app_update_status_label = QLabel("未检查")
        self.app_update_status_label.setStyleSheet("color: #6B7280;")
        version_row.addWidget(self.app_update_status_label)
        update_group_layout.addLayout(version_row)

        manifest_row = QHBoxLayout()
        manifest_row.setSpacing(6)
        manifest_row.addWidget(self._field_label("清单地址:"))
        self.update_manifest_edit = QLineEdit()
        self.update_manifest_edit.setPlaceholderText(DEFAULT_PUBLIC_UPDATE_MANIFEST_URL)
        self.update_manifest_edit.setMinimumHeight(28)
        manifest_row.addWidget(self.update_manifest_edit)
        update_group_layout.addLayout(manifest_row)

        update_group_layout.addWidget(
            self._hint_label("默认已预填公开更新地址，建议保持为公开仓库根目录的 version.json，便于持续获取最新版。")
        )

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self.app_check_update_btn = QPushButton("检查更新")
        self.app_check_update_btn.setFixedWidth(88)
        self.app_check_update_btn.clicked.connect(self._check_app_update)
        button_row.addWidget(self.app_check_update_btn)
        self.app_apply_update_btn = QPushButton("立即更新")
        self.app_apply_update_btn.setFixedWidth(88)
        self.app_apply_update_btn.setEnabled(False)
        self.app_apply_update_btn.clicked.connect(self._download_and_apply_update)
        button_row.addWidget(self.app_apply_update_btn)
        self.app_refresh_status_btn = QPushButton("刷新状态")
        self.app_refresh_status_btn.setFixedWidth(88)
        self.app_refresh_status_btn.clicked.connect(self._refresh_update_status)
        button_row.addWidget(self.app_refresh_status_btn)
        self.app_auto_check_cb = QCheckBox("启动时自动检查")
        button_row.addWidget(self.app_auto_check_cb)
        button_row.addStretch()
        update_group_layout.addLayout(button_row)
        self.app_update_detail_label = QLabel("最近更新状态: 暂无")
        self.app_update_detail_label.setWordWrap(True)
        self.app_update_detail_label.setStyleSheet("color: #6B7280;")
        update_group_layout.addWidget(self.app_update_detail_label)
        self.app_update_progress = QProgressBar()
        self.app_update_progress.setRange(0, 100)
        self.app_update_progress.setValue(0)
        self.app_update_progress.setTextVisible(True)
        self.app_update_progress.setVisible(False)
        update_group_layout.addWidget(self.app_update_progress)
        layout.addWidget(update_group)

        # ── 底部按钮 ─────────────────────────────────────────────
        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self._reset_to_default)
        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        self.save_btn = QPushButton("保存设置")
        self.save_btn.clicked.connect(self._save_settings)
        button_layout.addWidget(self.save_btn)
        layout.addLayout(button_layout)

    def _on_notify_state_changed(self, state):
        enabled = state == Qt.CheckState.Checked.value
        self.webhook_edit.setEnabled(enabled)
        self.verify_tls_cb.setEnabled(enabled)
        self.webhook_toggle_btn.setEnabled(enabled)

    def _toggle_webhook_echo(self):
        if self.webhook_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.webhook_toggle_btn.setText("隐藏")
        else:
            self.webhook_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.webhook_toggle_btn.setText("显示")

    def _load_config(self):
        """加载配置"""
        config = self.config_manager.config

        # MuMu 设置
        self.adb_path_edit.setText(config.mumu.adb_path or "")
        self.mumu_exe_path_edit.setText(config.mumu.mumu_exe_path or "")
        self.gps_lat_edit.setText(str(config.mumu.gps_latitude) if config.mumu.gps_latitude else "")
        self.gps_lon_edit.setText(str(config.mumu.gps_longitude) if config.mumu.gps_longitude else "")
        self.host_edit.setText(config.mumu.host)
        self.port_spin.setValue(config.mumu.port)

        # APP 设置
        self.package_edit.setText(config.app.package_name)

        # 节假日设置
        self.skip_weekend_cb.setChecked(config.holiday.skip_weekend)
        self.skip_holiday_cb.setChecked(config.holiday.skip_holiday)

        # 额外日期
        self._load_extra_dates(config.holiday.extra_workdays, config.holiday.extra_holidays)

        # 通知设置
        self.notify_cb.setChecked(config.notification.enabled)
        self.webhook_edit.setText(config.notification.webhook or "")
        self.verify_tls_cb.setChecked(getattr(config.notification, "verify_tls", True))

        # 软件更新
        self.update_manifest_edit.setText(getattr(config.update, "manifest_url", "") or "")
        self.app_auto_check_cb.setChecked(getattr(config.update, "auto_check_on_startup", False))
        self._show_app_version()
        self._show_last_update_result()
        self._refresh_update_status()

        # 启动行为
        self.auto_connect_cb.setChecked(config.app_state.auto_connect)
        self.auto_start_cb.setChecked(config.app_state.auto_start)
        self.keep_alive_cb.setChecked(getattr(config.app_state, "keep_alive_enabled", True))
        self.recovery_base_backoff_spin.setValue(getattr(config.app_state, "recovery_base_backoff_seconds", 5))
        self.recovery_max_backoff_spin.setValue(getattr(config.app_state, "recovery_max_backoff_seconds", 300))
        self.recovery_max_failures_spin.setValue(getattr(config.app_state, "recovery_max_failures", 20))
        self.recovery_pause_minutes_spin.setValue(getattr(config.app_state, "recovery_pause_minutes_after_max_failures", 30))
        self.recovery_quiet_enabled_cb.setChecked(getattr(config.app_state, "recovery_quiet_hours_enabled", False))
        self.recovery_quiet_start_spin.setValue(getattr(config.app_state, "recovery_quiet_start_hour", 0))
        self.recovery_quiet_end_spin.setValue(getattr(config.app_state, "recovery_quiet_end_hour", 0))
        self.boot_startup_cb.setChecked(_is_boot_startup_enabled())

        # 显示节假日数据版本
        self._show_holiday_version()

    def _reset_to_default(self):
        """恢复默认"""
        default_config = self.config_manager.get_default_config()

        self.adb_path_edit.setText(default_config.mumu.adb_path or "")
        self.mumu_exe_path_edit.setText(default_config.mumu.mumu_exe_path or "")
        self.gps_lat_edit.setText(str(default_config.mumu.gps_latitude) if default_config.mumu.gps_latitude else "")
        self.gps_lon_edit.setText(str(default_config.mumu.gps_longitude) if default_config.mumu.gps_longitude else "")
        self.host_edit.setText(default_config.mumu.host)
        self.port_spin.setValue(default_config.mumu.port)
        self.package_edit.setText(default_config.app.package_name)
        self.skip_weekend_cb.setChecked(default_config.holiday.skip_weekend)
        self.skip_holiday_cb.setChecked(default_config.holiday.skip_holiday)
        self._load_extra_dates(default_config.holiday.extra_workdays, default_config.holiday.extra_holidays)
        self.notify_cb.setChecked(default_config.notification.enabled)
        self.webhook_edit.setText(default_config.notification.webhook or "")
        self.verify_tls_cb.setChecked(default_config.notification.verify_tls)
        self.update_manifest_edit.setText(default_config.update.manifest_url or "")
        self.app_auto_check_cb.setChecked(default_config.update.auto_check_on_startup)
        self.auto_connect_cb.setChecked(default_config.app_state.auto_connect)
        self.auto_start_cb.setChecked(default_config.app_state.auto_start)
        self.keep_alive_cb.setChecked(default_config.app_state.keep_alive_enabled)
        self.recovery_base_backoff_spin.setValue(default_config.app_state.recovery_base_backoff_seconds)
        self.recovery_max_backoff_spin.setValue(default_config.app_state.recovery_max_backoff_seconds)
        self.recovery_max_failures_spin.setValue(default_config.app_state.recovery_max_failures)
        self.recovery_pause_minutes_spin.setValue(default_config.app_state.recovery_pause_minutes_after_max_failures)
        self.recovery_quiet_enabled_cb.setChecked(default_config.app_state.recovery_quiet_hours_enabled)
        self.recovery_quiet_start_spin.setValue(default_config.app_state.recovery_quiet_start_hour)
        self.recovery_quiet_end_spin.setValue(default_config.app_state.recovery_quiet_end_hour)

    def _save_settings(self):
        """保存设置"""
        ok, msg = self._validate_inputs()
        if not ok:
            QMessageBox.warning(self, "设置无效", msg)
            return
        self.save_to_config()
        self.config_manager.save()
        self.config_changed.emit()
        QMessageBox.information(self, "成功", "设置已保存")

    def save_to_config(self):
        """保存到配置对象"""
        config = self.config_manager.config

        config.mumu.adb_path = self.adb_path_edit.text()
        config.mumu.mumu_exe_path = self.mumu_exe_path_edit.text()
        try:
            config.mumu.gps_latitude = float(self.gps_lat_edit.text()) if self.gps_lat_edit.text().strip() else 0.0
            config.mumu.gps_longitude = float(self.gps_lon_edit.text()) if self.gps_lon_edit.text().strip() else 0.0
        except ValueError:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "GPS 坐标无效", "GPS 坐标必须为有效数字（如 31.3191），已重置为 0（不设置）")
            config.mumu.gps_latitude = 0.0
            config.mumu.gps_longitude = 0.0
        config.mumu.host = self.host_edit.text()
        config.mumu.port = self.port_spin.value()

        config.app.package_name = self.package_edit.text()

        config.holiday.skip_weekend = self.skip_weekend_cb.isChecked()
        config.holiday.skip_holiday = self.skip_holiday_cb.isChecked()

        # 收集额外日期
        extra_workdays = []
        extra_holidays = []
        for i in range(self.extra_dates_list.count()):
            item = self.extra_dates_list.item(i)
            date_type, date_str = item.data(Qt.ItemDataRole.UserRole)
            if date_type == 'workday':
                extra_workdays.append(date_str)
            else:
                extra_holidays.append(date_str)

        config.holiday.extra_workdays = extra_workdays
        config.holiday.extra_holidays = extra_holidays

        config.notification.enabled = self.notify_cb.isChecked()
        config.notification.webhook = self.webhook_edit.text()
        config.notification.verify_tls = self.verify_tls_cb.isChecked()
        config.update.manifest_url = self.update_manifest_edit.text().strip()
        config.update.auto_check_on_startup = self.app_auto_check_cb.isChecked()

        config.app_state.auto_connect = self.auto_connect_cb.isChecked()
        config.app_state.auto_start = self.auto_start_cb.isChecked()
        config.app_state.keep_alive_enabled = self.keep_alive_cb.isChecked()
        config.app_state.recovery_base_backoff_seconds = self.recovery_base_backoff_spin.value()
        config.app_state.recovery_max_backoff_seconds = self.recovery_max_backoff_spin.value()
        config.app_state.recovery_max_failures = self.recovery_max_failures_spin.value()
        config.app_state.recovery_pause_minutes_after_max_failures = self.recovery_pause_minutes_spin.value()
        config.app_state.recovery_quiet_hours_enabled = self.recovery_quiet_enabled_cb.isChecked()
        config.app_state.recovery_quiet_start_hour = self.recovery_quiet_start_spin.value()
        config.app_state.recovery_quiet_end_hour = self.recovery_quiet_end_spin.value()

    def _on_boot_startup_changed(self, state):
        try:
            _set_boot_startup(state == 2)
        except Exception as e:
            QMessageBox.warning(self, "设置失败", f"开机自启设置失败: {e}")
            # 回滚复选框状态
            self.boot_startup_cb.blockSignals(True)
            self.boot_startup_cb.setChecked(_is_boot_startup_enabled())
            self.boot_startup_cb.blockSignals(False)

    def _validate_inputs(self) -> tuple[bool, str]:
        host = (self.host_edit.text() or "").strip()
        if not host:
            return False, "主机地址不能为空"
        adb_path = (self.adb_path_edit.text() or "").strip()
        if adb_path and not Path(adb_path).exists():
            return False, "ADB 路径不存在，请选择正确的 adb.exe，或留空让程序自动查找"
        mumu_dir = (self.mumu_exe_path_edit.text() or "").strip()
        if mumu_dir and not Path(mumu_dir).exists():
            return False, "MuMu 安装目录不存在，请选择正确目录，或留空让程序自动查找"
        base_backoff = self.recovery_base_backoff_spin.value()
        max_backoff = self.recovery_max_backoff_spin.value()
        if max_backoff and base_backoff > max_backoff:
            return False, "恢复策略无效：基准退避不能大于最大退避"
        if self.recovery_quiet_enabled_cb.isChecked():
            if self.recovery_quiet_start_spin.value() == self.recovery_quiet_end_spin.value():
                return False, "恢复静默时段启用时，开始小时和结束小时不能相同"
        return True, ""

    def _copy_diagnostics(self):
        host = (self.host_edit.text() or "").strip()
        port = self.port_spin.value()
        adb_path = (self.adb_path_edit.text() or "").strip()
        mumu_dir = (self.mumu_exe_path_edit.text() or "").strip()
        pkg = (self.package_edit.text() or "").strip()
        lines = [
            f"host={host}",
            f"port={port}",
            f"adb_path={adb_path or '(auto)'}",
            f"mumu_dir={mumu_dir or '(auto)'}",
            f"package={pkg or '(empty)'}",
        ]
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText("\n".join(lines))
            QMessageBox.information(self, "已复制", "诊断信息已复制到剪贴板")
        except Exception:
            QMessageBox.information(self, "诊断信息", "\n".join(lines))

    def _export_diagnostics_bundle(self):
        runtime_root = get_runtime_root()
        config_dir = runtime_root / "config"
        logs_dir = runtime_root / "logs"
        export_dir = runtime_root / "diagnostics"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_name = f"tongkatong_diagnostics_{VERSION}_{Path(runtime_root).name}.zip"
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志诊断包",
            str(export_dir / export_name),
            "ZIP 文件 (*.zip)",
        )
        if not target_path:
            return

        files_to_pack = [
            get_update_log_file(),
            get_update_state_file(),
            get_update_state_cache_file(),
            config_dir / "user_config.json",
            config_dir / "default.json",
        ]
        written = 0
        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in files_to_pack:
                if file_path.exists() and file_path.is_file():
                    zf.write(file_path, arcname=file_path.relative_to(runtime_root))
                    written += 1
            if logs_dir.exists():
                for log_file in logs_dir.glob("*.log"):
                    if log_file.is_file():
                        zf.write(log_file, arcname=log_file.relative_to(runtime_root))
                        written += 1
            summary = [
                f"version={VERSION}",
                f"edition={get_edition_label(detect_current_edition())}",
                f"runtime_root={runtime_root}",
                f"manifest_url={self.update_manifest_edit.text().strip() or DEFAULT_PUBLIC_UPDATE_MANIFEST_URL}",
            ]
            zf.writestr("summary.txt", "\n".join(summary))
            written += 1

        QMessageBox.information(self, "导出完成", f"已导出 {written} 个文件到：\n{target_path}")

    def _browse_mumu_exe(self):
        """浏览 MuMu 安装目录"""
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "选择 MuMu 安装目录")
        if folder:
            self.mumu_exe_path_edit.setText(folder)

    def _browse_adb(self):
        """浏览 ADB 路径"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ADB 可执行文件",
            "",
            "可执行文件 (*.exe);;所有文件 (*)"
        )
        if file_path:
            self.adb_path_edit.setText(file_path)

    def _test_connection(self):
        """测试连接（后台线程，避免 UI 冻结）"""
        self.test_conn_btn.setEnabled(False)
        self.test_conn_btn.setText("连接中...")

        self._test_worker = AdbConnectWorker(
            self.adb_path_edit.text() or None,
            self.host_edit.text(),
            self.port_spin.value(),
        )

        def _on_done(success, message):
            self.test_conn_btn.setEnabled(True)
            self.test_conn_btn.setText("测试连接")
            if success:
                QMessageBox.information(self, "连接成功", f"已成功连接到设备\n{message}")
            else:
                QMessageBox.warning(self, "连接失败", message)

        self._test_worker.done.connect(_on_done)
        self._test_worker.start()

    def _detect_package(self):
        """检测包名（后台线程，避免 UI 冻结）"""
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("检测中...")

        self._detect_worker = AdbListPackagesWorker(
            self.adb_path_edit.text() or None,
            self.host_edit.text(),
            self.port_spin.value(),
        )

        def _on_done(success, packages, error_msg):
            self.detect_btn.setEnabled(True)
            self.detect_btn.setText("自动检测")
            if not success:
                QMessageBox.warning(self, "连接失败", error_msg)
                return

            keywords = ['jiantong', 'checkin', 'work', 'oa', 'office', 'attendance']
            filtered = [p for p in packages if any(k in p.lower() for k in keywords)]

            dialog = QDialog(self)
            dialog.setWindowTitle("选择应用")
            dialog.setMinimumSize(400, 300)
            layout = QVBoxLayout(dialog)

            list_widget = QListWidget()
            list_widget.addItems(filtered if filtered else packages[:50])
            layout.addWidget(list_widget)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = list_widget.currentItem()
                if selected:
                    self.package_edit.setText(selected.text())

        self._detect_worker.done.connect(_on_done)
        self._detect_worker.start()

    def _add_extra_workday(self):
        """添加额外工作日"""
        self._add_extra_date('workday')

    def _add_extra_holiday(self):
        """添加额外休息日"""
        self._add_extra_date('holiday')

    @staticmethod
    def _extra_date_label(date_type: str) -> str:
        return "工作日" if date_type == "workday" else "休息日"

    def _create_extra_date_item(self, date_type: str, date_str: str) -> QListWidgetItem:
        item = QListWidgetItem(f"{self._extra_date_label(date_type)}: {date_str}")
        item.setData(Qt.ItemDataRole.UserRole, (date_type, date_str))
        return item

    def _iter_extra_dates(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for i in range(self.extra_dates_list.count()):
            item = self.extra_dates_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                entries.append(data)
        return entries

    def _find_extra_date_row(self, date_str: str) -> int:
        for i, (_, existing_date) in enumerate(self._iter_extra_dates()):
            if existing_date == date_str:
                return i
        return -1

    def _load_extra_dates(self, workdays: list[str], holidays: list[str]) -> None:
        date_map: dict[str, str] = {}
        for date_str in sorted(holidays):
            date_map[date_str] = "holiday"
        for date_str in sorted(workdays):
            date_map[date_str] = "workday"
        self._rebuild_extra_dates(date_map)

    def _rebuild_extra_dates(self, date_map: dict[str, str]) -> None:
        self.extra_dates_list.clear()
        for date_str, date_type in sorted(date_map.items(), key=lambda item: (item[0], 0 if item[1] == "workday" else 1)):
            self.extra_dates_list.addItem(self._create_extra_date_item(date_type, date_str))

    def _sort_extra_dates(self) -> None:
        entries = sorted(self._iter_extra_dates(), key=lambda item: (item[1], 0 if item[0] == "workday" else 1))
        self.extra_dates_list.clear()
        for date_type, date_str in entries:
            self.extra_dates_list.addItem(self._create_extra_date_item(date_type, date_str))

    @staticmethod
    def _date_range_strings(start_date, end_date) -> list[str]:
        total_days = start_date.daysTo(end_date)
        return [start_date.addDays(offset).toString("yyyy-MM-dd") for offset in range(total_days + 1)]

    def _remove_selected_extra_date(self):
        item = self.extra_dates_list.currentItem()
        if not item:
            QMessageBox.information(self, "未选择日期", "请先选中一条手动日期规则。")
            return

        date_type, date_str = item.data(Qt.ItemDataRole.UserRole)
        label = self._extra_date_label(date_type)
        reply = QMessageBox.question(
            self,
            "删除日期",
            f"确定删除“{label}: {date_str}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.extra_dates_list.takeItem(self.extra_dates_list.row(item))

    def _add_extra_date(self, date_type: str):
        """添加额外日期"""
        from PyQt6.QtWidgets import QDateEdit
        from PyQt6.QtCore import QDate

        dialog = QDialog(self)
        label = self._extra_date_label(date_type)
        dialog.setWindowTitle(f"添加{label}")

        layout = QVBoxLayout(dialog)
        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(8)
        form_layout.setVerticalSpacing(8)
        form_layout.addWidget(QLabel("开始日期:"), 0, 0)
        start_date_edit = QDateEdit()
        start_date_edit.setCalendarPopup(True)
        start_date_edit.setDate(QDate.currentDate())
        form_layout.addWidget(start_date_edit, 0, 1)
        form_layout.addWidget(QLabel("结束日期:"), 1, 0)
        end_date_edit = QDateEdit()
        end_date_edit.setCalendarPopup(True)
        end_date_edit.setDate(QDate.currentDate())
        form_layout.addWidget(end_date_edit, 1, 1)
        layout.addLayout(form_layout)
        layout.addWidget(self._hint_label(f"会一次性把开始到结束之间的所有日期加入“{label}”。"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            start_date = start_date_edit.date()
            end_date = end_date_edit.date()
            if start_date > end_date:
                QMessageBox.warning(self, "日期范围无效", "结束日期不能早于开始日期。")
                return

            date_values = self._date_range_strings(start_date, end_date)
            date_map = {existing_date: existing_type for existing_type, existing_date in self._iter_extra_dates()}
            duplicate_count = 0
            replace_candidates: list[str] = []
            for date_str in date_values:
                existing_type = date_map.get(date_str)
                if existing_type == date_type:
                    duplicate_count += 1
                elif existing_type and existing_type != date_type:
                    replace_candidates.append(date_str)

            replace_conflicts = True
            if replace_candidates:
                old_label = self._extra_date_label("holiday" if date_type == "workday" else "workday")
                if len(replace_candidates) == 1:
                    message = f"{replace_candidates[0]} 当前已设置为“{old_label}”，是否改成“{label}”？"
                else:
                    message = (
                        f"所选范围内有 {len(replace_candidates)} 天已设置为“{old_label}”，"
                        f"是否统一改成“{label}”？"
                    )
                reply = QMessageBox.question(
                    self,
                    "替换日期规则",
                    message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                replace_conflicts = reply == QMessageBox.StandardButton.Yes

            added_count = 0
            replaced_count = 0
            skipped_count = 0
            for date_str in date_values:
                existing_type = date_map.get(date_str)
                if existing_type == date_type:
                    skipped_count += 1
                    continue
                if existing_type and existing_type != date_type:
                    if not replace_conflicts:
                        skipped_count += 1
                        continue
                    replaced_count += 1
                else:
                    added_count += 1
                date_map[date_str] = date_type

            if added_count == 0 and replaced_count == 0:
                if duplicate_count == len(date_values):
                    QMessageBox.information(self, "无需添加", f"所选日期已经全部在“{label}”列表中。")
                else:
                    QMessageBox.information(self, "未修改", "没有新增日期，冲突日期也未替换。")
                return

            self._rebuild_extra_dates(date_map)
            QMessageBox.information(
                self,
                "添加完成",
                f"已处理 {len(date_values)} 天：新增 {added_count} 天，替换 {replaced_count} 天，跳过 {skipped_count} 天。",
            )

    def _show_holiday_version(self):
        """显示节假日数据版本及覆盖年份"""
        try:
            import chinese_calendar
            from datetime import date

            version = getattr(chinese_calendar, '__version__', 'unknown')

            # 探测数据覆盖到哪一年（用春节/国庆交叉验证）
            covered_year = date.today().year - 1
            for year in range(date.today().year, date.today().year + 3):
                # 用国庆节（固定10月1日）测试
                test = date(year, 10, 1)
                try:
                    detail = chinese_calendar.get_holiday_detail(test)
                    if detail and detail[0]:
                        covered_year = year
                    else:
                        break
                except Exception:
                    break

            self.holiday_version_label.setText(
                f"节假日数据: v{version}（覆盖至 {covered_year} 年）"
            )
        except ImportError:
            self.holiday_version_label.setText("节假日数据: 未安装")

    def _show_app_version(self):
        edition = get_edition_label(detect_current_edition())
        self.app_version_label.setText(f"当前版本: v{VERSION} · {edition}")

    def _show_last_update_result(self):
        result = consume_update_result(VERSION)
        if not result:
            return

        self.app_update_status_label.setText(result["title"])
        self._apply_update_status(result)
        if result["level"] == "info":
            QMessageBox.information(self, result["title"], result["message"])
        else:
            QMessageBox.warning(self, result["title"], result["message"])

    def _apply_update_status(self, result: dict | None):
        if not result:
            self.app_update_status_label.setText("未检查")
            self.app_update_status_label.setStyleSheet("color: #6B7280;")
            self.app_update_detail_label.setText("最近更新状态: 暂无")
            return

        self.app_update_status_label.setText(result["title"])
        color = "#B45309" if result.get("level") == "warning" else "#2563EB"
        self.app_update_status_label.setStyleSheet(f"color: {color};")
        detail = result.get("message", "").strip() or "暂无更新状态详情。"
        updated_at = result.get("updated_at", "").strip()
        suffix = f" 最后记录时间: {updated_at}" if updated_at else ""
        self.app_update_detail_label.setText(f"最近更新状态: {detail}{suffix}")

    def _refresh_update_status(self):
        self._apply_update_status(read_update_result(VERSION))

    def trigger_auto_update_check(self):
        if self.app_auto_check_cb.isChecked():
            self._check_app_update(silent=True)

    def _quit_for_update(self):
        window = self.window()
        if window and hasattr(window, "_on_quit"):
            logger.info("更新已开始，通知主窗口执行退出清理流程")
            window._on_quit()
            return

        app = QApplication.instance()
        if app:
            logger.info("更新已开始，未找到主窗口退出入口，回退为 QApplication.quit()")
            app.quit()

    def _check_holiday_update(self):
        """检查节假日数据更新（子线程，不阻塞 UI）"""
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("检查中...")

        self._check_worker = HolidayCheckWorker()

        def _on_done(info):
            self.check_update_btn.setEnabled(True)
            self.check_update_btn.setText("检查更新")

            if info is None:
                QMessageBox.warning(self, "检查失败",
                    "无法获取最新版本信息，请检查网络连接。\n（已尝试阿里云、清华、腾讯、华为、中科大镜像）")
                return

            current = info['current']
            latest = info['latest']

            if info['need_update']:
                reply = QMessageBox.question(
                    self, "发现新版本",
                    f"节假日数据有新版本可用：\n\n当前版本：v{current}\n最新版本：v{latest}\n\n是否立即更新？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._do_holiday_update()
            else:
                QMessageBox.information(self, "已是最新",
                    f"节假日数据已是最新版本 v{current}，无需更新。")

        self._check_worker.done.connect(_on_done)
        self._check_worker.start()

    def _do_holiday_update(self):
        """执行节假日数据更新（子线程，不阻塞 UI）"""
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("更新中...")

        self._update_worker = HolidayUpdateWorker()

        def _on_done(success):
            self.check_update_btn.setEnabled(True)
            self.check_update_btn.setText("检查更新")
            if success:
                self._show_holiday_version()
                QMessageBox.information(self, "更新成功", "节假日数据已更新，重启软件后生效。")
            else:
                QMessageBox.critical(self, "更新失败",
                    "所有镜像源均失败，请检查网络连接或手动运行：\n"
                    "pip install -i https://mirrors.aliyun.com/pypi/simple/ --upgrade chinesecalendar")

        self._update_worker.done.connect(_on_done)
        self._update_worker.start()

    def _check_app_update(self, silent: bool = False):
        manifest_url = (self.update_manifest_edit.text() or "").strip()
        if not manifest_url:
            if not silent:
                QMessageBox.information(self, "未配置地址", "请先填写更新清单地址（version.json）。")
            return

        self.app_check_update_btn.setEnabled(False)
        self.app_check_update_btn.setText("检查中...")
        self.app_apply_update_btn.setEnabled(False)
        self.app_update_status_label.setText("正在检查...")

        self._app_update_check_worker = AppUpdateCheckWorker(manifest_url)

        def _on_done(result, error):
            self.app_check_update_btn.setEnabled(True)
            self.app_check_update_btn.setText("检查更新")

            if error:
                self._pending_update_info = None
                self.app_update_status_label.setText("检查失败")
                logger.warning("软件更新检查失败: %s", error)
                if not silent:
                    QMessageBox.warning(self, "检查失败", str(error))
                return

            self._pending_update_info = result
            latest = result["latest_version"]
            if result["need_update"]:
                asset = result["asset"]
                logger.info("检测到可用更新: 当前=v%s, 最新=v%s, 文件=%s", result["current_version"], latest, asset.file_name)
                self.app_apply_update_btn.setEnabled(True)
                self.app_update_status_label.setText(f"发现新版本 v{latest}")
                notes = asset.notes.strip() if asset.notes else "无更新说明"
                QMessageBox.information(
                    self,
                    "发现新版本",
                    f"当前版本：v{result['current_version']}\n"
                    f"最新版本：v{latest}\n\n"
                    f"更新说明：\n{notes}\n\n"
                    "点击“立即更新”即可下载并自动替换当前程序。",
                )
            else:
                logger.info("软件已是最新版本: v%s", result["current_version"])
                self.app_apply_update_btn.setEnabled(False)
                self.app_update_status_label.setText("已是最新")
                if not silent:
                    QMessageBox.information(self, "已是最新", f"当前已是最新版本 v{result['current_version']}。")

        self._app_update_check_worker.done.connect(_on_done)
        self._app_update_check_worker.start()

    def _download_and_apply_update(self):
        if not self._pending_update_info or not self._pending_update_info.get("need_update"):
            QMessageBox.information(self, "暂无更新", "请先检查更新，并确认有可用新版本。")
            return

        asset = self._pending_update_info["asset"]
        reply = QMessageBox.question(
            self,
            "确认更新",
            f"将下载并安装 v{asset.version}。\n\n更新过程中会自动关闭当前软件并重启，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        logger.info("用户确认开始更新: 目标版本=v%s, 文件=%s", asset.version, asset.file_name)
        self.app_check_update_btn.setEnabled(False)
        self.app_apply_update_btn.setEnabled(False)
        self.app_apply_update_btn.setText("下载中...")
        self.app_update_status_label.setText("正在下载...")
        self.app_update_progress.setVisible(True)
        self.app_update_progress.setValue(0)
        self.app_update_progress.setFormat("准备下载...")

        self._app_update_download_worker = AppUpdateDownloadWorker(asset)

        def _on_progress(downloaded, total):
            if total > 0:
                percent = int(downloaded * 100 / total)
                self.app_update_progress.setValue(percent)
                self.app_update_progress.setFormat(
                    f"已下载 {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB"
                )
                self.app_update_status_label.setText(f"正在下载... {percent}%")
            else:
                dots = "." * ((downloaded // (1024 * 1024)) % 3 + 1)
                self.app_update_progress.setRange(0, 0)
                self.app_update_progress.setFormat(f"正在下载{dots}")
                self.app_update_status_label.setText("正在下载...")

        def _on_status(message):
            if message:
                self.app_update_status_label.setText(message)

        def _on_done(downloaded_path, error):
            self.app_check_update_btn.setEnabled(True)
            self.app_apply_update_btn.setText("立即更新")
            self.app_update_progress.setRange(0, 100)

            if error:
                self.app_apply_update_btn.setEnabled(True)
                self.app_update_status_label.setText("更新失败")
                self.app_update_progress.setVisible(False)
                logger.error("更新包下载失败: %s", error)
                QMessageBox.critical(self, "更新失败", str(error))
                return

            try:
                logger.info("更新包已下载，准备启动替换流程: %s", downloaded_path)
                launch_self_update(Path(downloaded_path), asset.version, VERSION)
            except UpdateError as e:
                self.app_apply_update_btn.setEnabled(True)
                self.app_update_status_label.setText("更新失败")
                self.app_update_progress.setVisible(False)
                logger.error("启动更新替换流程失败: %s", e)
                QMessageBox.warning(self, "无法启动更新", str(e))
                return

            self.app_update_status_label.setText("即将重启")
            self.app_update_progress.setValue(100)
            self.app_update_progress.setFormat("下载完成，准备重启...")
            logger.info("更新替换流程已启动，准备退出当前程序")
            QMessageBox.information(self, "更新开始", "更新包已下载完成，软件将自动退出并替换为新版本。")
            self._quit_for_update()

        self._app_update_download_worker.progress.connect(_on_progress)
        self._app_update_download_worker.status.connect(_on_status)
        self._app_update_download_worker.done.connect(_on_done)
        self._app_update_download_worker.start()
