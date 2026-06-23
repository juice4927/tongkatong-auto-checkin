"""
主窗口模块
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QStatusBar, QSystemTrayIcon,
    QMenu, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QIcon, QAction, QFont, QShortcut, QKeySequence

from src.core.config import ConfigManager, Config, get_runtime_root
from src.core.scheduler import CheckinOrchestrator
from src.core.automator import UIAutomator2Impl, MockAutomator, CheckinAction
from src.core.holiday import HolidayChecker
from src.utils.logger import setup_logging, get_log_manager, LogManager
from src.utils.app_updater import get_edition_label
from src.version import VERSION, BUILD_DATE, APP_NAME

logger = logging.getLogger(__name__)


class UiState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CHECKING = "checking"


def _style_asset_url(file_name: str) -> str:
    """返回可供 Qt 样式表使用的本地资源路径。"""
    for path in _asset_candidates(file_name):
        if path.exists():
            return path.resolve().as_posix()
    candidates = _asset_candidates(file_name)
    return candidates[0].resolve().as_posix() if candidates else ""


def _asset_candidates(file_name: str) -> list[Path]:
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "src" / "gui" / "assets" / file_name)
        candidates.append(Path(sys.executable).parent / "src" / "gui" / "assets" / file_name)
    candidates.append(Path(__file__).resolve().parents[1] / "gui" / "assets" / file_name)
    return candidates


def _load_app_icon(file_name: str = "app_icon.ico") -> QIcon:
    for path in _asset_candidates(file_name):
        if path.exists():
            return QIcon(str(path.resolve()))
    return QIcon()


class MainWindow(QMainWindow):
    """主窗口"""
    
    # 信号
    log_signal = pyqtSignal(str, str)  # message, level
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._app_icon = _load_app_icon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)

        # 初始化配置
        self.config_manager = ConfigManager()
        self.config = self.config_manager.config

        # 初始化日志：注册 LogManager 回调 → Qt 信号 → GUI（线程安全）
        self.log_manager = get_log_manager()
        self.log_signal.connect(self._on_log_message)
        self.log_manager.add_callback(self._on_log_from_manager)
        
        # 初始化组件
        self.automator = None
        self.holiday_checker = None
        self.orchestrator = None
        
        # 状态
        self.is_running = False
        self.is_connected = False
        self._ui_state = UiState.DISCONNECTED
        self._pending_recover = False
        self._recover_show_error = True
        self._desired_running = False
        self._recovery_in_progress = False
        self._recovery_fail_count = 0
        self._recovery_next_retry_at = None
        self._recovery_started_at = None
        self._recovery_paused_until = None
        self._last_recovery_time = None
        self._last_recovery_result = "暂无"
        self._last_recovery_error = ""
        self._last_recovery_reason = ""
        self._last_recovery_action = ""
        
        # 设置窗口
        self._setup_window()
        self._create_widgets()
        self._create_tray()
        self._connect_signals()
        
        # 定时器
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(1000)  # 每秒更新

        # 自动连接（延迟100ms，等窗口完全显示后再触发）
        if self.config.app_state.auto_connect:
            QTimer.singleShot(100, self._connect)

        # 打卡前预检定时器（每分钟检查一次）
        self._guard_timer = QTimer()
        self._guard_timer.timeout.connect(self._keep_alive_guard)
        self._guard_timer.start(15 * 1000)

        self._apply_ui_state()
        QTimer.singleShot(1200, self._run_startup_update_check)
        QTimer.singleShot(1500, self._maybe_show_first_run_setup_prompt)
    
    def _setup_window(self):
        """设置窗口属性"""
        self.setWindowTitle(f"{APP_NAME} v{VERSION} · {get_edition_label()}")
        self.setMinimumSize(860, 640)
        self.resize(1020, 700)
        
        # 设置字体
        font = QFont("Microsoft YaHei", 8)
        self.setFont(font)

        self._quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._quit_shortcut.activated.connect(self._on_quit)
    
    def _create_widgets(self):
        """创建界面组件"""
        # 中央部件
        central_widget = QWidget()
        central_widget.setObjectName("appRoot")
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        
        # 顶部状态栏
        self._create_status_bar(main_layout)
        
        # 标签页
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        main_layout.addWidget(self.tab_widget)
        
        # 创建各标签页
        self._create_dashboard_tab()
        self._create_time_config_tab()
        self._create_log_tab()
        self._create_settings_tab()
        
        # 底部按钮
        self._create_bottom_buttons(main_layout)
    
    def _create_status_bar(self, layout: QVBoxLayout):
        """创建顶部状态栏"""
        status_frame = QFrame()
        status_frame.setObjectName("surfaceCard")
        status_frame.setFrameShape(QFrame.Shape.NoFrame)
        status_frame.setLineWidth(0)
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(12, 6, 12, 6)
        status_layout.setSpacing(8)

        connection_title = QLabel("连接")
        connection_title.setObjectName("statusInlineTitle")
        status_layout.addWidget(connection_title)
        self.connection_label = QLabel("未连接")
        self.connection_label.setObjectName("statusInlineValue")
        status_layout.addWidget(self.connection_label)

        separator_one = QLabel("·")
        separator_one.setObjectName("statusInlineSeparator")
        status_layout.addWidget(separator_one)

        running_title = QLabel("运行")
        running_title.setObjectName("statusInlineTitle")
        status_layout.addWidget(running_title)
        self.running_label = QLabel("已停止")
        self.running_label.setObjectName("statusInlineValue")
        status_layout.addWidget(self.running_label)

        status_layout.addStretch()

        time_title = QLabel("时间")
        time_title.setObjectName("statusInlineTitle")
        status_layout.addWidget(time_title)
        self.time_label = QLabel("--:--:--")
        self.time_label.setObjectName("statusInlineValue")
        status_layout.addWidget(self.time_label)
        
        layout.addWidget(status_frame)

    def _set_status_panel_tone(self, label: QLabel, text: str, color: str, background: str, border: str):
        label.setText(text)
        label.setStyleSheet(
            f"color: {color};"
            "font-size: 13px;"
            "font-weight: 700;"
            "background: transparent;"
            "border: none;"
            "padding: 0px;"
        )
    
    def _create_dashboard_tab(self):
        """创建仪表盘标签页"""
        from .widgets.dashboard import DashboardWidget
        self.dashboard_widget = DashboardWidget(self.config_manager)
        self.dashboard_widget.set_guard_state_provider(self.get_guard_status_snapshot)
        self.tab_widget.addTab(self.dashboard_widget, "仪表盘")
    
    def _create_time_config_tab(self):
        """创建时间配置标签页"""
        from .widgets.time_config import TimeConfigWidget
        self.time_config_widget = TimeConfigWidget(self.config_manager)
        self.tab_widget.addTab(self.time_config_widget, "时间配置")
    
    def _create_log_tab(self):
        """创建日志标签页"""
        from .widgets.log_viewer import LogViewerWidget
        self.log_viewer_widget = LogViewerWidget(base_dir=get_runtime_root())
        self.tab_widget.addTab(self.log_viewer_widget, "日志")
    
    def _create_settings_tab(self):
        """创建设置标签页"""
        from .widgets.settings import SettingsWidget
        self.settings_widget = SettingsWidget(self.config_manager)
        self.tab_widget.addTab(self.settings_widget, "设置")

    def _run_startup_update_check(self):
        try:
            self.settings_widget.trigger_auto_update_check()
        except Exception as e:
            logger.debug(f"启动自动检查更新失败: {e}")
    
    def _create_bottom_buttons(self, layout: QVBoxLayout):
        """创建底部按钮"""
        bar = QFrame()
        bar.setObjectName("actionBar")
        bar.setFrameShape(QFrame.Shape.NoFrame)
        bar.setLineWidth(0)
        button_layout = QHBoxLayout(bar)
        button_layout.setContentsMargins(10, 7, 10, 7)
        button_layout.setSpacing(8)
        
        # 连接按钮
        self.connect_btn = QPushButton("连接设备")
        self.connect_btn.setObjectName("secondary_btn")
        self.connect_btn.setMinimumHeight(32)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        button_layout.addWidget(self.connect_btn)
        
        # 启动/停止按钮
        self.start_btn = QPushButton("启动")
        self.start_btn.setMinimumHeight(32)
        self.start_btn.setMinimumWidth(90)
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.start_btn.setEnabled(False)
        button_layout.addWidget(self.start_btn)
        
        # 立即打卡按钮
        self.checkin_btn = QPushButton("立即打卡")
        self.checkin_btn.setObjectName("accent_btn")
        self.checkin_btn.setMinimumHeight(32)
        self.checkin_btn.clicked.connect(self._on_checkin_clicked)
        self.checkin_btn.setEnabled(False)
        button_layout.addWidget(self.checkin_btn)
        
        button_layout.addStretch()

        # 关于按钮
        self.about_btn = QPushButton("关于")
        self.about_btn.setObjectName("ghost_btn")
        self.about_btn.setMinimumHeight(32)
        self.about_btn.clicked.connect(self._on_about_clicked)
        button_layout.addWidget(self.about_btn)
        
        # 保存配置按钮
        self.save_btn = QPushButton("保存配置")
        self.save_btn.setObjectName("primary_btn")
        self.save_btn.setMinimumHeight(32)
        self.save_btn.clicked.connect(self._on_save_clicked)
        button_layout.addWidget(self.save_btn)
        
        layout.addWidget(bar)
    
    def _create_tray(self):
        """创建系统托盘"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        self.tray_icon = QSystemTrayIcon(self)
        if not self._app_icon.isNull():
            self.tray_icon.setIcon(self._app_icon)
        else:
            # 缺少图标资源时回退到内置托盘图形
            from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
            from PyQt6.QtCore import Qt, QRect

            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            if painter.isActive():
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor("#4CAF50"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(4, 4, 56, 56)
                painter.setPen(QColor("white"))
                font = QFont()
                font.setPixelSize(36)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "✓")
                painter.end()

            self.tray_icon.setIcon(QIcon(pixmap))
        
        # 设置提示文本
        self.tray_icon.setToolTip("通卡通")
        
        # 托盘菜单
        tray_menu = QMenu()
        
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)
        
        start_action = QAction("启动", self)
        start_action.triggered.connect(self._start)
        tray_menu.addAction(start_action)

        stop_action = QAction("停止", self)
        stop_action.triggered.connect(self._stop)
        tray_menu.addAction(stop_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._on_quit)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        
        # 显示托盘图标
        self.tray_icon.show()
    
    def _connect_signals(self):
        """连接信号"""
        # 配置变更信号
        self.time_config_widget.config_changed.connect(self._on_config_changed)
        self.settings_widget.config_changed.connect(self._on_config_changed)

    def _first_run_setup_items(self) -> list[str]:
        items = []
        if not (self.config.mumu.gps_latitude and self.config.mumu.gps_longitude):
            items.append("填写打卡 GPS 坐标")
        if not (self.config.mumu.mumu_exe_path or "").strip():
            items.append("填写 MuMu 安装目录，或确认自动查找可用")
        if not (self.config.mumu.adb_path or "").strip():
            items.append("ADB 路径可留空自动查找；若连接失败请手动选择 adb.exe")
        if self.config.notification.enabled and not (self.config.notification.webhook or "").strip():
            items.append("通知已开启但未填写 Server酱 Key")
        return items

    def _should_show_first_run_setup_prompt(self) -> bool:
        """首次启动且关键配置缺失时提醒用户配置。"""
        app_state = self.config.app_state
        if getattr(app_state, "setup_prompt_shown", False):
            return False
        return bool(self._first_run_setup_items())

    def _maybe_show_first_run_setup_prompt(self):
        if not self._should_show_first_run_setup_prompt():
            return

        self.config.app_state.setup_prompt_shown = True
        self.config.app_state.gps_prompt_shown = True
        try:
            self.config_manager.save()
        except Exception as e:
            logger.warning("保存首次配置提示状态失败: %s", e)

        items = self._first_run_setup_items()
        detail = "\n".join(f"- {item}" for item in items)

        box = QMessageBox(self)
        box.setWindowTitle("首次使用配置提醒")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("首次使用前，请先检查关键设置。")
        box.setInformativeText(f"{detail}\n\n未填写 GPS 坐标时，程序不会设置模拟器虚拟定位，可能导致打卡定位失败。")
        open_settings_btn = box.addButton("打开设置", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()

        if box.clickedButton() == open_settings_btn:
            self.tab_widget.setCurrentWidget(self.settings_widget)

    def _clear_worker_ref(self, attr_name: str, worker):
        """仅在属性仍指向该 worker 时清空引用，避免残留失效 QThread。"""
        if getattr(self, attr_name, None) is worker:
            setattr(self, attr_name, None)

    def _reset_worker_slot(self, attr_name: str, signal_name: str = "done"):
        """安全清理旧 worker，兼容底层 QObject 已被 deleteLater 的情况。"""
        old_worker = getattr(self, attr_name, None)
        if old_worker is None:
            return

        try:
            if old_worker.isRunning():
                old_worker.requestInterruption()
                old_worker.wait(2000)
        except RuntimeError:
            setattr(self, attr_name, None)
            return

        signal = getattr(old_worker, signal_name, None)
        if signal is not None:
            try:
                signal.disconnect()
            except (TypeError, RuntimeError):
                pass

        setattr(self, attr_name, None)
    
    def _on_connect_clicked(self):
        """连接按钮点击"""
        if self._ui_state in (UiState.CONNECTING, UiState.STARTING, UiState.STOPPING, UiState.CHECKING):
            return
        if self.is_running:
            QMessageBox.information(self, "提示", "请先停止自动打卡再断开连接")
            return
        if self.is_connected:
            self._disconnect()
        else:
            self._connect()
    
    def _connect(self, show_error: bool = True):
        """连接设备（后台线程，避免 UI 冻结）"""
        self._reset_worker_slot('_conn_worker')

        self._set_ui_state(UiState.CONNECTING)
        self._log("正在连接设备...")
        self._log(f"目标: {self.config.mumu.host}:{self.config.mumu.port}")

        class _ConnWorker(QThread):
            done = pyqtSignal(bool, str, object)  # success, message, automator_or_error

            def __init__(self, host, port, mumu_exe_path, adb_path, pkg):
                super().__init__()
                self._host = host
                self._port = port
                self._mumu_exe_path = mumu_exe_path
                self._adb_path = adb_path
                self._pkg = pkg

            def run(self):
                port_candidates = []
                for p in (self._port, 7555, 5555):
                    if p and p not in port_candidates:
                        port_candidates.append(p)

                resolved_adb_path = self._adb_path
                if not resolved_adb_path:
                    from src.utils.adb_helper import MuMuHelper
                    found = MuMuHelper().find_mumu_adb(self._mumu_exe_path)
                    resolved_adb_path = str(found) if found else ""

                last_error = ""
                for port in port_candidates:
                    try:
                        auto = UIAutomator2Impl(host=self._host, port=port, adb_path=resolved_adb_path or None)
                        auto.connect()
                        info = auto.device.device_info
                        model = f"{info.get('brand', '?')} {info.get('model', '?')}"
                        try:
                            auto.open_app(self._pkg)
                        except Exception:
                            pass
                        self.done.emit(True, model, auto)
                        return
                    except Exception as e:
                        last_error = str(e)
                        logger.warning(f"端口 {port} 连接失败: {last_error}")
                        continue

                from src.utils.adb_helper import ADBHelper, MuMuHelper
                adb_path = resolved_adb_path or "adb"
                adb = ADBHelper(adb_path)
                mumu = MuMuHelper(adb)
                try:
                    launched = mumu.launch_mumu(self._mumu_exe_path, wait_seconds=60,
                                                app_package=self._pkg,
                                                host=self._host, port=self._port,
                                                candidate_ports=port_candidates)
                except Exception as e:
                    logger.error(f"启动 MuMu 异常: {e}", exc_info=True)
                    msg = f"启动 MuMu 失败: {e}"
                    if last_error:
                        msg += f"\n上次尝试错误: {last_error}"
                    self.done.emit(False, msg, None)
                    return
                if not launched:
                    msg = "MuMu 启动失败或超时，请手动启动模拟器"
                    if last_error:
                        msg += f"\n上次尝试错误: {last_error}"
                    self.done.emit(False, msg, None)
                    return
                for port in port_candidates:
                    try:
                        ok, detail = adb.connect(self._host, port)
                        if not ok:
                            raise RuntimeError(detail)
                        auto = UIAutomator2Impl(host=self._host, port=port, adb_path=adb_path)
                        auto.connect()
                        info = auto.device.device_info
                        model = f"{info.get('brand', '?')} {info.get('model', '?')}"
                        try:
                            auto.open_app(self._pkg)
                        except Exception:
                            pass
                        self.done.emit(True, model, auto)
                        return
                    except Exception as e:
                        last_error = str(e)
                        logger.error(f"端口 {port} 启动后连接失败: {last_error}")
                        continue
                msg = f"连接失败：已尝试端口 {port_candidates}，ADB={adb_path}"
                if last_error:
                    msg += f"\n最后错误: {last_error}"
                else:
                    msg += "\n最后错误: 未知错误（请查看日志）"
                logger.error(msg)
                self.done.emit(False, msg, None)

        self._conn_worker = _ConnWorker(
            self.config.mumu.host,
            self.config.mumu.port,
            self.config.mumu.mumu_exe_path,
            self.config.mumu.adb_path,
            self.config.app.package_name,
        )
        conn_worker = self._conn_worker
        conn_worker.finished.connect(conn_worker.deleteLater)
        conn_worker.finished.connect(lambda worker=conn_worker: self._clear_worker_ref('_conn_worker', worker))

        def _on_conn_done(success, message, automator):
            if success:
                was_recover = self._pending_recover
                self.automator = automator
                self.is_connected = True
                self._log(f"设备: {message}")
                self._log("设备连接成功")
                self._log("交建通已在后台启动")
                # 保存自动连接状态
                self.config.app_state.auto_connect = True
                self.config_manager.save()
                self._set_ui_state(UiState.CONNECTED)
                if was_recover:
                    self._pending_recover = False
                    self._start()
                # 连接成功后，若开启了自动启动则自动启动（自动恢复触发时不重复启动）
                if (not was_recover) and self.config.app_state.auto_start:
                    self._start()
            else:
                self._log(f"连接失败: {message}")
                self._set_ui_state(UiState.DISCONNECTED)
                if show_error:
                    self._show_error(
                        "连接失败",
                        message,
                        "请检查：\n1. MuMu 模拟器是否运行\n2. ADB 端口是否正确\n3. ADB 路径是否正确（可留空自动查找）\n4. 是否已开启 ADB 调试"
                    )
                if self._recovery_in_progress:
                    self._mark_recovery_failed(f"连接失败: {message}")

        conn_worker.done.connect(_on_conn_done)
        conn_worker.start()
    
    def _disconnect(self, persist_auto_state: bool = True):
        """断开连接"""
        if self.orchestrator and self.is_running:
            QMessageBox.information(self, "提示", "请先停止自动打卡再断开连接")
            return
        
        if self.automator:
            self.automator.disconnect()
        
        # 断开连接时清掉 orchestrator，重连后重建（automator 实例变了）
        self.orchestrator = None
        self.dashboard_widget.set_orchestrator(None)
        
        self.is_connected = False
        self.is_running = False
        self._set_ui_state(UiState.DISCONNECTED)

        self._log("已断开连接")
        if persist_auto_state:
            # 手动断开时关闭自动连接/启动；自动恢复流程不改用户偏好
            self.config.app_state.auto_connect = False
            self.config.app_state.auto_start = False
            self._desired_running = False
            self._pending_recover = False
            self._recovery_in_progress = False
            self.config_manager.save()
    
    def _on_start_clicked(self):
        """启动/停止按钮点击"""
        if self._ui_state in (UiState.CONNECTING, UiState.STARTING, UiState.STOPPING, UiState.CHECKING):
            return
        if self.is_running:
            self._stop()
        else:
            self._start()
    
    def _start(self):
        """启动自动打卡"""
        if not self.is_connected:
            QMessageBox.warning(self, "提示", "请先连接设备")
            return

        self._reset_worker_slot('_start_worker')
        
        self._set_ui_state(UiState.STARTING)

        class _StartWorker(QThread):
            done = pyqtSignal(bool, str, object)

            def __init__(self, automator, cfg, cfg_mgr):
                super().__init__()
                self._automator = automator
                self._cfg = cfg
                self._cfg_mgr = cfg_mgr

            def run(self):
                try:
                    holiday_checker = HolidayChecker(
                        skip_weekend=self._cfg.holiday.skip_weekend,
                        skip_holiday=self._cfg.holiday.skip_holiday,
                        extra_workdays=self._cfg.holiday.extra_workdays,
                        extra_holidays=self._cfg.holiday.extra_holidays
                    )
                    orchestrator = CheckinOrchestrator(
                        automator=self._automator,
                        holiday_checker=holiday_checker,
                        config_manager=self._cfg_mgr
                    )
                    if not orchestrator.initialize():
                        self.done.emit(False, "调度器初始化失败", None)
                        return
                    orchestrator.start()
                    self.done.emit(True, "", orchestrator)
                except Exception as e:
                    self.done.emit(False, str(e), None)

        self._start_worker = _StartWorker(self.automator, self.config, self.config_manager)
        start_worker = self._start_worker
        start_worker.finished.connect(start_worker.deleteLater)
        start_worker.finished.connect(lambda worker=start_worker: self._clear_worker_ref('_start_worker', worker))

        def _on_started(ok, message, orchestrator):
            if ok:
                self.orchestrator = orchestrator
                self.is_running = True
                self._desired_running = True
                self.dashboard_widget.set_orchestrator(self.orchestrator)
                self._log("自动打卡已启动")
                self.config.app_state.auto_start = True
                self.config_manager.save()
                self._set_ui_state(UiState.RUNNING)
                if self._recovery_in_progress:
                    self._mark_recovery_succeeded()
            else:
                self.is_running = False
                self.orchestrator = None
                self.dashboard_widget.set_orchestrator(None)
                self._set_ui_state(UiState.CONNECTED)
                self._log(f"启动失败: {message}")
                if self._recovery_in_progress:
                    self._mark_recovery_failed(f"重启调度失败: {message}")
                else:
                    self._show_error("启动失败", message)

        start_worker.done.connect(_on_started)
        start_worker.start()
    
    def _stop(self, persist_auto_state: bool = True):
        """停止自动打卡"""
        if not self.orchestrator:
            self.is_running = False
            if persist_auto_state:
                self._desired_running = False
            self._set_ui_state(UiState.CONNECTED if self.is_connected else UiState.DISCONNECTED)
            return

        self._reset_worker_slot('_stop_worker')

        self._set_ui_state(UiState.STOPPING)

        class _StopWorker(QThread):
            done = pyqtSignal(bool, str)

            def __init__(self, orchestrator):
                super().__init__()
                self._orchestrator = orchestrator

            def run(self):
                try:
                    self._orchestrator.stop()
                    self.done.emit(True, "")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._stop_worker = _StopWorker(self.orchestrator)
        stop_worker = self._stop_worker
        stop_worker.finished.connect(stop_worker.deleteLater)
        stop_worker.finished.connect(lambda worker=stop_worker: self._clear_worker_ref('_stop_worker', worker))

        def _on_stopped(ok, message):
            self.is_running = False
            self.orchestrator = None
            self.dashboard_widget.set_orchestrator(None)
            self._log("自动打卡已停止")
            if persist_auto_state:
                self.config.app_state.auto_start = False
                self._desired_running = False
                self.config_manager.save()
            self._set_ui_state(UiState.CONNECTED if self.is_connected else UiState.DISCONNECTED)
            if not ok:
                if self._recovery_in_progress:
                    self._mark_recovery_failed(f"停止失败: {message}")
                else:
                    self._show_error("停止失败", message)
            if self._pending_recover:
                if self._last_recovery_action == "restart_scheduler" and self.is_connected:
                    self._start()
                else:
                    self._connect(show_error=self._recover_show_error)

        stop_worker.done.connect(_on_stopped)
        stop_worker.start()
    
    def _on_checkin_clicked(self):
        """立即打卡按钮点击"""
        if not self.is_connected:
            QMessageBox.warning(self, "提示", "请先连接设备")
            return
        
        # 弹出选择对话框
        from PyQt6.QtWidgets import QDialog, QComboBox, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle("选择打卡类型")
        layout = QVBoxLayout(dialog)
        
        combo = QComboBox()
        combo.addItems(["上午签到", "上午签退", "下午签到", "下午签退"])
        layout.addWidget(combo)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            action_map = {
                0: CheckinAction.MORNING_SIGNIN,
                1: CheckinAction.MORNING_SIGNOUT,
                2: CheckinAction.AFTERNOON_SIGNIN,
                3: CheckinAction.AFTERNOON_SIGNOUT,
            }
            action = action_map[combo.currentIndex()]
            action_name = combo.currentText()
            self._do_manual_checkin(action, action_name)
    
    def _on_about_clicked(self):
        """关于对话框"""
        QMessageBox.about(
            self,
            f"关于 {APP_NAME} {get_edition_label()}",
            f"<b>{APP_NAME}</b> v{VERSION} · {get_edition_label()}<br>"
            f"构建日期：{BUILD_DATE}<br><br>"
            f"自动完成交建通考勤打卡，支持定时打卡、补签、Server酱通知。<br><br>"
            f"<b>【首次使用】</b><br>"
            f"1. 在「设置」页配置 MuMu 安装目录和 ADB 路径<br>"
            f"2. 填写 Server酱 SendKey 以接收打卡通知<br>"
            f"3. 在「时间配置」页调整打卡时间范围<br>"
            f"4. 点击「连接设备」，程序会自动启动 MuMu 并打开交建通<br>"
            f"5. 连接成功后点击「启动」开始自动打卡<br><br>"
            f"<b>【自动恢复】</b><br>"
            f"在「设置 → 启动行为」勾选「开机自启」「自动连接」「自动启动」，"
            f"重启后程序自动恢复运行。<br><br>"
            f"<b>【打卡前预警】</b><br>"
            f"距打卡不足 10 分钟时自动检查状态，恢复失败则 Server酱 发送预警。<br><br>"
            f"<b>【每日汇总】</b><br>"
            f"下午签退完成后，通过 Server酱 发送当天4次打卡的汇总结果。"
        )

    def _do_manual_checkin(self, action: CheckinAction, action_name: str):
        """执行手动打卡（子线程，避免 UI 卡顿）"""
        if self.is_running:
            QMessageBox.information(self, "提示", "自动打卡运行中，建议先停止后再手动打卡")
            return
        self._set_ui_state(UiState.CHECKING)
        self._log(f"开始手动打卡: {action_name}")

        class _Worker(QThread):
            done = pyqtSignal(bool, str, str)  # success, message, timestamp

            def __init__(self, automator, pkg, action, config):
                super().__init__()
                self._automator = automator
                self._pkg = pkg
                self._action = action
                self._config = config

            def run(self):
                try:
                    from src.core.scheduler import CheckinOrchestrator
                    if hasattr(self._automator, "set_makeup_windows"):
                        self._automator.set_makeup_windows(
                            CheckinOrchestrator.checkin_windows_from_config(self._config)
                        )
                    self._automator.open_app(self._pkg)
                    result = self._automator.do_checkin(self._action)
                    self.done.emit(result.success, result.message, result.timestamp or "")
                except Exception as e:
                    self.done.emit(False, str(e), "")

        self._checkin_worker = _Worker(
            self.automator,
            self.config.app.package_name,
            action,
            self.config
        )
        checkin_worker = self._checkin_worker
        checkin_worker.finished.connect(checkin_worker.deleteLater)
        checkin_worker.finished.connect(lambda worker=checkin_worker: self._clear_worker_ref('_checkin_worker', worker))

        def _on_done(success, message, timestamp):
            status = "成功" if success else "失败"
            self._log(f"[打卡记录] {status} | {action_name} | {timestamp} | {message}")
            from src.core.scheduler import CheckinOrchestrator
            CheckinOrchestrator.record_checkin_result(success, action_name, timestamp, message)
            if success:
                try:
                    from src.utils.notifier import notify_checkin_result
                    notify_checkin_result(self.config, action_name, True, message, timestamp)
                except Exception as notify_err:
                    self._log(f"通知发送失败: {notify_err}")
            else:
                self._show_error("打卡失败", message)

            self._set_ui_state(UiState.CONNECTED)

        checkin_worker.done.connect(_on_done)
        checkin_worker.start()
    
    def _on_save_clicked(self):
        """保存配置"""
        try:
            # 收集配置
            self.time_config_widget.save_to_config()
            self.settings_widget.save_to_config()
            
            # 保存
            self.config_manager.save()
            
            self._log("配置已保存")
            QMessageBox.information(self, "保存成功", "配置已保存")
            
        except Exception as e:
            self._log(f"保存失败: {e}")
            QMessageBox.critical(self, "保存失败", str(e))
    
    def _on_config_changed(self):
        """配置变更后重新调度今日任务"""
        if self.is_running and self.orchestrator:
            self._log("配置已变更，重新生成今日打卡时间...")
            self.orchestrator.reschedule_today()
            self.dashboard_widget.set_orchestrator(self.orchestrator)
    
    def _on_log_message(self, message: str, level: str):
        """Qt 信号槽：在主线程更新 GUI 日志"""
        self.log_viewer_widget.add_log(message, level)

    def _on_log_from_manager(self, message: str, level: str):
        """LogManager 回调（可能来自任意线程），通过 Qt 信号转发到主线程"""
        self.log_signal.emit(message, level)

    def _log(self, message: str, level: str = "INFO"):
        """手动添加日志（仅用于 GUI 自身的操作提示）"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_message = f"[{timestamp}] {message}"
        self.log_signal.emit(log_message, level)

    def _emit_recovery_event(self, event: str, reason: str = "", result: str = "", elapsed_ms: int = 0, source: str = "", extra: Optional[dict] = None):
        """记录结构化恢复事件日志（JSON）"""
        payload = {
            "event": event,
            "reason": reason,
            "source": source,
            "result": result,
            "elapsed_ms": elapsed_ms,
            "fail_count": self._recovery_fail_count,
            "pending_recover": self._pending_recover,
            "in_progress": self._recovery_in_progress,
            "desired_running": self._desired_running,
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "ui_state": self._ui_state.value if isinstance(self._ui_state, UiState) else str(self._ui_state),
            "next_retry_at": self._recovery_next_retry_at.strftime("%Y-%m-%d %H:%M:%S") if self._recovery_next_retry_at else None,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if extra:
            payload.update(extra)
        logger.info("[恢复事件] %s", json.dumps(payload, ensure_ascii=False))

    def get_guard_status_snapshot(self) -> dict:
        """供仪表盘读取守护状态快照"""
        return {
            "keep_alive_enabled": self._is_keep_alive_enabled(),
            "desired_running": self._desired_running,
            "recovery_in_progress": self._recovery_in_progress,
            "recovery_fail_count": self._recovery_fail_count,
            "recovery_next_retry_at": self._recovery_next_retry_at.strftime("%H:%M:%S") if self._recovery_next_retry_at else None,
            "recovery_paused_until": self._recovery_paused_until.strftime("%H:%M:%S") if self._recovery_paused_until else None,
            "last_recovery_time": self._last_recovery_time.strftime("%Y-%m-%d %H:%M:%S") if self._last_recovery_time else "-",
            "last_recovery_result": self._last_recovery_result,
            "last_recovery_error": self._last_recovery_error,
            "last_recovery_reason": self._last_recovery_reason,
            "last_recovery_action": self._last_recovery_action,
            "is_connected": self.is_connected,
            "is_running": self.is_running,
        }

    def _get_recovery_policy(self):
        app_state = self.config.app_state
        base_backoff = max(0, int(getattr(app_state, "recovery_base_backoff_seconds", 5)))
        max_backoff = max(0, int(getattr(app_state, "recovery_max_backoff_seconds", 300)))
        if max_backoff and base_backoff > max_backoff:
            base_backoff = max_backoff
        max_failures = max(0, int(getattr(app_state, "recovery_max_failures", 20)))
        pause_minutes = max(1, int(getattr(app_state, "recovery_pause_minutes_after_max_failures", 30)))
        quiet_enabled = bool(getattr(app_state, "recovery_quiet_hours_enabled", False))
        quiet_start = int(getattr(app_state, "recovery_quiet_start_hour", 0))
        quiet_end = int(getattr(app_state, "recovery_quiet_end_hour", 0))
        return {
            "base_backoff": base_backoff,
            "max_backoff": max_backoff,
            "max_failures": max_failures,
            "pause_minutes": pause_minutes,
            "quiet_enabled": quiet_enabled,
            "quiet_start": quiet_start,
            "quiet_end": quiet_end,
        }

    def _in_recovery_quiet_hours(self) -> bool:
        p = self._get_recovery_policy()
        if not p["quiet_enabled"]:
            return False
        start = p["quiet_start"]
        end = p["quiet_end"]
        if start == end:
            return False
        now_h = datetime.now().hour
        if start < end:
            return start <= now_h < end
        return now_h >= start or now_h < end
    
    def _is_keep_alive_enabled(self) -> bool:
        return bool(getattr(self.config.app_state, "keep_alive_enabled", True))

    def _scheduler_running(self) -> bool:
        if not self.orchestrator:
            return False
        try:
            return bool(self.orchestrator.scheduler._running)
        except Exception:
            return False

    def _mark_recovery_succeeded(self):
        now = datetime.now()
        elapsed_ms = 0
        if self._recovery_started_at:
            elapsed_ms = int((now - self._recovery_started_at).total_seconds() * 1000)
        self._recovery_in_progress = False
        self._pending_recover = False
        self._recovery_fail_count = 0
        self._recovery_next_retry_at = None
        self._recovery_paused_until = None
        self._recovery_started_at = None
        self._last_recovery_time = now
        self._last_recovery_result = "成功"
        self._last_recovery_error = ""
        self._log("自动恢复成功：已恢复到运行状态")
        self._emit_recovery_event(
            event="recover_finish",
            reason=self._last_recovery_reason,
            result="success",
            elapsed_ms=elapsed_ms,
        )

    def _mark_recovery_failed(self, reason: str):
        now = datetime.now()
        elapsed_ms = 0
        if self._recovery_started_at:
            elapsed_ms = int((now - self._recovery_started_at).total_seconds() * 1000)
        self._recovery_in_progress = False
        self._pending_recover = False
        self._recovery_fail_count += 1
        p = self._get_recovery_policy()
        base = p["base_backoff"]
        max_backoff = p["max_backoff"]
        backoff_seconds = base * (2 ** max(0, self._recovery_fail_count - 1))
        if max_backoff > 0:
            backoff_seconds = min(max_backoff, backoff_seconds)
        max_failures = p["max_failures"]
        if max_failures > 0 and self._recovery_fail_count >= max_failures:
            self._recovery_paused_until = now + timedelta(minutes=p["pause_minutes"])
            self._recovery_next_retry_at = self._recovery_paused_until
            self._last_recovery_error = f"{reason}（已达失败阈值，暂停恢复）"
            self._log(
                f"自动恢复失败({self._recovery_fail_count}次): {reason}；"
                f"已达阈值，暂停至 {self._recovery_paused_until.strftime('%H:%M:%S')}"
            )
            self._emit_recovery_event(
                event="recover_finish",
                reason=self._last_recovery_reason or reason,
                result="failed_paused",
                elapsed_ms=elapsed_ms,
                extra={
                    "error": reason,
                    "max_failures": max_failures,
                    "paused_until": self._recovery_paused_until.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            self._recovery_started_at = None
            self._last_recovery_time = now
            self._last_recovery_result = "失败"
            return
        self._recovery_paused_until = None
        self._recovery_next_retry_at = now + timedelta(seconds=backoff_seconds)
        self._recovery_started_at = None
        self._last_recovery_time = now
        self._last_recovery_result = "失败"
        self._last_recovery_error = reason
        self._log(
            f"自动恢复失败({self._recovery_fail_count}次): {reason}；"
            f"{backoff_seconds}秒后重试"
        )
        self._emit_recovery_event(
            event="recover_finish",
            reason=self._last_recovery_reason or reason,
            result="failed",
            elapsed_ms=elapsed_ms,
            extra={
                "error": reason,
                "backoff_seconds": backoff_seconds,
                "max_failures": max_failures,
            }
        )

    def _resolve_recovery_action(self, reason: str) -> str:
        reason = reason or ""
        if "调度未运行" in reason or "打卡未启动" in reason or "重启调度失败" in reason:
            return "restart_scheduler"
        if "无响应" in reason:
            return "reset_device_session"
        return "reconnect_device"

    def _trigger_recovery(self, reason: str, source: str = "unknown", show_error: bool = False):
        if not self._is_keep_alive_enabled():
            return
        if self._recovery_in_progress:
            return
        if self._ui_state in (UiState.CONNECTING, UiState.STARTING, UiState.STOPPING, UiState.CHECKING):
            return
        if self._recovery_next_retry_at and datetime.now() < self._recovery_next_retry_at:
            return
        if self._recovery_paused_until and datetime.now() < self._recovery_paused_until:
            return
        if self._in_recovery_quiet_hours():
            self._emit_recovery_event(
                event="recover_skip_quiet_hours",
                reason=reason,
                source=source,
                result="skipped"
            )
            return

        self._recovery_in_progress = True
        self._recover_show_error = show_error
        self._pending_recover = True
        self._recovery_started_at = datetime.now()
        self._last_recovery_reason = reason
        self._last_recovery_action = self._resolve_recovery_action(reason)
        self._last_recovery_result = "进行中"
        self._log(f"触发自动恢复[{source}/{self._last_recovery_action}]: {reason}")
        self._emit_recovery_event(
            event="recover_trigger",
            reason=reason,
            source=source,
            result="started",
            extra={"recovery_action": self._last_recovery_action},
        )

        # 统一恢复状态机：停止 -> 重连 -> 重启调度
        # 注意：_stop() 是异步的（QThread），完成后通过 _on_stopped 回调触发 _connect()
        if self._last_recovery_action == "restart_scheduler":
            if self.is_running and self.orchestrator:
                self._stop(persist_auto_state=False)
                return
            if self.is_connected:
                self._start()
                return

        if self.is_running and self.orchestrator:
            self._stop(persist_auto_state=False)
            return

        # 未运行时直接断开并重连
        if self.automator and self._last_recovery_action in ("reconnect_device", "reset_device_session"):
            try:
                self.automator.disconnect()
            except Exception:
                pass
        self.is_connected = False
        self.is_running = False
        self.orchestrator = None
        self.dashboard_widget.set_orchestrator(None)
        self._set_ui_state(UiState.DISCONNECTED)
        self._connect(show_error=show_error)

    def _keep_alive_guard(self):
        """常驻守护：运行期持续检查连接和调度状态，并在异常时按状态机恢复。"""
        self._pre_checkin_guard()
        if not self._is_keep_alive_enabled():
            return
        if not self._desired_running:
            return
        if self._ui_state in (UiState.CONNECTING, UiState.STARTING, UiState.STOPPING, UiState.CHECKING):
            return

        issues = []
        if not self.is_connected or not self.automator or not self.automator.is_connected():
            issues.append("设备未连接")
        if not self.is_running or not self.orchestrator or not self._scheduler_running():
            issues.append("调度未运行")
        if not issues:
            return

        self._trigger_recovery("、".join(issues), source="keep_alive_guard", show_error=False)

    def _pre_checkin_guard(self):
        """距打卡不足10分钟时兜底检查，失败后触发恢复并发送预警。"""
        if self._ui_state in (UiState.CONNECTING, UiState.STARTING, UiState.STOPPING, UiState.CHECKING):
            return
        if not self.orchestrator:
            return

        now = datetime.now()
        checkin_times = self.orchestrator.get_checkin_times()
        upcoming = [t for t in checkin_times.values() if t and 0 < (t - now).total_seconds() <= 600]
        if not upcoming:
            return

        nearest = min(upcoming, key=lambda t: t - now)
        minutes_left = int((nearest - now).total_seconds() // 60)
        issues = []
        if not self.is_connected or not self.automator or not self.automator.is_connected():
            issues.append("设备未连接")
        if not self.is_running or not self._scheduler_running():
            issues.append("打卡未启动")
        if not issues:
            return

        self._log(f"距打卡仅剩{minutes_left}分钟，检测到{'、'.join(issues)}")
        self._trigger_recovery("、".join(issues), source="pre_checkin_guard", show_error=False)

        def _check_recovery():
            still_issues = []
            if not self.is_connected or not self.automator or not self.automator.is_connected():
                still_issues.append("设备未连接")
            if not self.is_running or not self._scheduler_running():
                still_issues.append("打卡未启动")
            if not still_issues:
                return
            self._log(f"自动恢复失败: {'、'.join(still_issues)}，发送预警通知")
            sendkey = self.config.notification.webhook
            if sendkey:
                problem = "、".join(still_issues)
                msg_title = f"通卡通预警 - {problem}"
                msg_body = f"距 {nearest.strftime('%H:%M')} 打卡仅剩 {minutes_left} 分钟，自动恢复失败，请手动检查。"
                import threading

                def _send():
                    try:
                        from src.utils.notifier import send_serverchan
                        send_serverchan(sendkey, msg_title, msg_body)
                    except Exception as e:
                        self._log(f"预警通知发送失败: {e}")

                threading.Thread(target=_send, daemon=True).start()

        QTimer.singleShot(30 * 1000, _check_recovery)

    def _update_status(self):
        """更新状态"""
        # 更新时间
        self.time_label.setText(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self.time_label.setStyleSheet(
            "color: #111827; font-size: 13px; font-weight: 700;"
            "background: transparent; border: none; padding: 0px;"
        )

        # 连接状态检测：每30秒才做一次 ADB 查询，避免每秒阻塞主线程
        if self.is_connected and self.automator:
            now_ts = datetime.now().timestamp()
            if not hasattr(self, '_last_conn_check_ts'):
                self._last_conn_check_ts = 0
            if now_ts - self._last_conn_check_ts >= 30:
                self._last_conn_check_ts = now_ts
                if not self.automator.is_connected():
                    self._log("设备连接已断开")
                    self.is_connected = False
                    if self._ui_state == UiState.RUNNING:
                        self._set_ui_state(UiState.CONNECTED)
                    if self._desired_running and self._is_keep_alive_enabled():
                        self._trigger_recovery("连接状态轮询发现设备断线", source="status_poll", show_error=False)

    def _apply_ui_state(self):
        """根据当前 _ui_state 刷新按钮/标签状态。"""
        if self._ui_state == UiState.DISCONNECTED:
            self._set_status_panel_tone(self.connection_label, "未连接", "#C42B1C", "#FCEEEE", "#F3CCCC")
            self._set_status_panel_tone(self.running_label, "已停止", "#6B7280", "#F5F7FB", "#E5E7EB")
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("连接设备")
            self.start_btn.setEnabled(False)
            self.start_btn.setText("启动")
            self.start_btn.setObjectName("")
            self.start_btn.style().unpolish(self.start_btn)
            self.start_btn.style().polish(self.start_btn)
            self.checkin_btn.setEnabled(False)
            self.checkin_btn.setText("立即打卡")
            return

        if self._ui_state == UiState.CONNECTING:
            self._set_status_panel_tone(self.connection_label, "连接中", "#B45309", "#FFF7ED", "#F4D8B6")
            self._set_status_panel_tone(self.running_label, "请稍候", "#B45309", "#FFF7ED", "#F4D8B6")
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("连接中...")
            self.start_btn.setEnabled(False)
            self.checkin_btn.setEnabled(False)
            return

        if self._ui_state in (UiState.CONNECTED, UiState.STARTING, UiState.RUNNING, UiState.STOPPING, UiState.CHECKING):
            self._set_status_panel_tone(self.connection_label, "已连接", "#0F7B0F", "#EEF8F0", "#CDE8D3")

        if self._ui_state == UiState.CONNECTED:
            self._set_status_panel_tone(self.running_label, "已停止", "#6B7280", "#F5F7FB", "#E5E7EB")
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("断开连接")
            self.start_btn.setEnabled(True)
            self.start_btn.setText("启动")
            self.start_btn.setObjectName("primary_btn")
            self.start_btn.style().unpolish(self.start_btn)
            self.start_btn.style().polish(self.start_btn)
            self.checkin_btn.setEnabled(True)
            self.checkin_btn.setText("立即打卡")
            return

        if self._ui_state == UiState.STARTING:
            self._set_status_panel_tone(self.running_label, "启动中", "#B45309", "#FFF7ED", "#F4D8B6")
            self.connect_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.start_btn.setText("启动中...")
            self.checkin_btn.setEnabled(False)
            return

        if self._ui_state == UiState.RUNNING:
            self._set_status_panel_tone(self.running_label, "运行中", "#0F6CBD", "#EEF4FB", "#CFE0F6")
            self.connect_btn.setEnabled(False)
            self.start_btn.setEnabled(True)
            self.start_btn.setText("停止")
            self.start_btn.setObjectName("danger_btn")
            self.start_btn.style().unpolish(self.start_btn)
            self.start_btn.style().polish(self.start_btn)
            self.checkin_btn.setEnabled(False)
            self.checkin_btn.setText("立即打卡")
            return

        if self._ui_state == UiState.STOPPING:
            self._set_status_panel_tone(self.running_label, "停止中", "#B45309", "#FFF7ED", "#F4D8B6")
            self.connect_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.start_btn.setText("停止中...")
            self.checkin_btn.setEnabled(False)
            return

        if self._ui_state == UiState.CHECKING:
            self._set_status_panel_tone(self.running_label, "打卡中", "#B45309", "#FFF7ED", "#F4D8B6")
            self.connect_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.checkin_btn.setEnabled(False)
            self.checkin_btn.setText("打卡中...")
            return

    def _set_ui_state(self, new_state: "UiState"):
        """统一设置 UI 状态并刷新界面。"""
        self._ui_state = new_state
        self._apply_ui_state()

    def _show_error(self, title: str, message: str, detail: str = ""):
        msg = message or title
        suggestion = self._suggest_for_error(msg)

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(msg)
        if suggestion:
            box.setInformativeText(suggestion)
        if detail:
            box.setDetailedText(detail)

        open_settings_btn = box.addButton("打开设置", QMessageBox.ButtonRole.ActionRole)
        open_logs_btn = box.addButton("打开logs", QMessageBox.ButtonRole.ActionRole)
        copy_btn = box.addButton("复制详情", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()

        clicked = box.clickedButton()
        if clicked == open_settings_btn:
            try:
                self.tab_widget.setCurrentWidget(self.settings_widget)
            except Exception:
                pass
            return
        if clicked == open_logs_btn:
            try:
                self.tab_widget.setCurrentWidget(self.log_viewer_widget)
                if hasattr(self.log_viewer_widget, "_open_logs_dir"):
                    self.log_viewer_widget._open_logs_dir()
            except Exception:
                pass
            return
        if clicked == copy_btn:
            text = (msg or "") + ("\n\n" + detail if detail else "")
            if suggestion:
                text += "\n\n" + suggestion
            QApplication.clipboard().setText(text)

    def _suggest_for_error(self, msg: str) -> str:
        s = (msg or "").lower()
        if "端口" in msg or "connection" in s or "connect" in s:
            return "建议检查：端口是否正确（常见 5555/7555）、MuMu 是否已启动、是否开启 ADB 调试；必要时在设置页留空 ADB 路径让程序自动查找。"
        if "ssl" in s or "证书" in msg:
            return "网络证书校验失败：优先检查公司代理/抓包软件/证书链；仅在确认环境需要时才在设置里关闭“验证 HTTPS 证书”。"
        if "未找到" in msg and ("按钮" in msg or "考勤" in msg):
            return "请确认交建通已登录且能手动进入“工作台→考勤”；若界面改版可能需要更新定位策略。"
        if "定位" in msg or "超出距离" in msg:
            return "请先在 MuMu 设置虚拟定位到打卡范围内；并确认网络/定位权限正常。"
        return "可先复制详情发给维护者；也可打开 logs 目录查看当日日志与 crash.log 进行排障。"
    
    def _on_tray_activated(self, reason):
        """托盘图标激活"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
    
    def _on_quit(self):
        """退出（强制关闭，确保进程终止）"""
        if getattr(self, '_quitting', False):
            return
        self._quitting = True
        QTimer.singleShot(5000, self._force_exit)
        if self.tray_icon:
            self.tray_icon.hide()
        self._cleanup_resources()
        QApplication.quit()

    @staticmethod
    def _force_exit():
        """强制退出（兜底，防止线程卡住导致进程不退出）"""
        import os
        os._exit(0)

    def closeEvent(self, event):
        """关闭事件 - 最小化到托盘"""
        if getattr(self, '_quitting', False):
            event.accept()
            return
        if self.tray_icon and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            if self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    "通卡通",
                    "程序已最小化到系统托盘，右键托盘图标可退出",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000
                )
        else:
            event.accept()
            self._on_quit()

    def _cleanup_resources(self):
        """清理所有资源（同步安全，不阻塞退出）"""
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        if hasattr(self, '_guard_timer'):
            self._guard_timer.stop()
        if hasattr(self, 'dashboard_widget') and self.dashboard_widget:
            if hasattr(self.dashboard_widget, 'time_timer'):
                self.dashboard_widget.time_timer.stop()
            if hasattr(self.dashboard_widget, 'status_timer'):
                self.dashboard_widget.status_timer.stop()

        if self.orchestrator:
            try:
                self.orchestrator.stop()
            except Exception as e:
                logger.warning(f"停止调度器失败: {e}")
            self.orchestrator = None
            self.is_running = False

        if self.automator:
            try:
                self.automator.disconnect()
            except Exception as e:
                logger.warning(f"断开自动化器失败: {e}")
            self.automator = None
            self.is_connected = False

        for worker_attr in ['_conn_worker', '_start_worker', '_stop_worker', '_checkin_worker']:
            worker = getattr(self, worker_attr, None)
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.wait(2000)

        try:
            from src.core.automator import UIAutomator2Impl
            UIAutomator2Impl.clear_all_cache()
        except Exception as e:
            logger.debug(f"清理自动化器缓存失败: {e}")


def main():
    """主函数"""
    import traceback
    import os

    # 日志已在 main.py 里初始化，这里只确保崩溃日志目录存在
    log_dir = get_runtime_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 崩溃日志文件（捕获所有未处理异常）
    crash_log = log_dir / "crash.log"
    chevron_down_url = _style_asset_url("chevron_down.svg")
    spin_plus_url = _style_asset_url("spin_plus.svg")
    spin_minus_url = _style_asset_url("spin_minus.svg")

    try:
        # 创建应用
        app = QApplication(sys.argv)
        app.setApplicationName("通卡通")
        app.setQuitOnLastWindowClosed(False)

        # Win11 Fluent Design 全局样式
        stylesheet = """
            QWidget#appRoot, QWidget {
                background-color: #F5F7FB;
                color: #111827;
            }
            QMainWindow {
                background-color: #F5F7FB;
            }
            QFrame#surfaceCard, QFrame#actionBar {
                background: #FFFFFF;
                border: 1px solid #E7EBF1;
                border-radius: 16px;
            }
            QLabel#statusInlineTitle {
                color: #6B7280;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#statusInlineValue {
                color: #111827;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#statusInlineSeparator {
                color: #9CA3AF;
                font-size: 12px;
                padding: 0 2px;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
                top: 0px;
            }
            QTabBar::tab {
                background: #EEF2F7;
                border: 1px solid transparent;
                padding: 9px 16px;
                margin-right: 6px;
                border-radius: 10px;
                color: #6B7280;
                font-size: 12px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #0F6CBD;
                border: 1px solid #DCE5F0;
            }
            QTabBar::tab:hover:!selected {
                background: #E7EDF6;
                color: #111827;
            }
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #D7DEE8;
                border-radius: 10px;
                padding: 6px 14px;
                color: #111827;
                min-height: 20px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #F7FAFE;
                border-color: #BFD2EA;
            }
            QPushButton:pressed {
                background-color: #E8F0FB;
            }
            QPushButton:disabled {
                color: #9D9D9D;
                background-color: #F5F6F8;
                border-color: #E5E7EB;
            }
            QPushButton#primary_btn {
                background-color: #0F6CBD;
                color: white;
                border: 1px solid #0F6CBD;
            }
            QPushButton#primary_btn:hover {
                background-color: #0C5EA8;
            }
            QPushButton#primary_btn:pressed {
                background-color: #094B86;
            }
            QPushButton#accent_btn {
                background-color: #EEF8F0;
                color: #0F7B0F;
                border: 1px solid #CDE8D3;
            }
            QPushButton#accent_btn:hover {
                background-color: #E5F4E8;
                border-color: #6BC47A;
            }
            QPushButton#secondary_btn {
                background-color: #FFFFFF;
                color: #0F6CBD;
                border: 1px solid #CFE0F6;
            }
            QPushButton#secondary_btn:hover {
                background-color: #F3F8FD;
            }
            QPushButton#ghost_btn {
                background-color: #F5F7FB;
                color: #4B5563;
                border: 1px solid #E7EBF1;
            }
            QPushButton#danger_btn {
                background-color: #C42B1C;
                color: white;
                border: 1px solid #C42B1C;
            }
            QPushButton#danger_btn:hover {
                background-color: #A31F15;
            }
            QPushButton#danger_btn:pressed {
                background-color: #8A1A12;
            }
            QGroupBox {
                border: 1px solid #E7EBF1;
                border-radius: 16px;
                margin-top: 14px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 2px 10px;
                background: transparent;
                font-weight: 700;
                color: #111827;
                font-size: 12px;
            }
            QLabel {
                background: transparent;
            }
            QLineEdit, QComboBox, QSpinBox, QTimeEdit {
                border: 1px solid #D7DEE8;
                border-radius: 10px;
                padding: 5px 9px;
                background: white;
                selection-background-color: #ACD8F5;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {
                border: 1px solid #0F6CBD;
                background: #FCFDFF;
            }
            QComboBox {
                padding-right: 30px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border: none;
                background: transparent;
                padding-right: 8px;
            }
            QComboBox::down-arrow {
                image: url("__CHEVRON_DOWN_URL__");
                width: 10px;
                height: 10px;
            }
            QComboBox::down-arrow:on {
                top: 0px;
                left: 0px;
            }
            QComboBox QAbstractItemView {
                border: 1px solid #D7DEE8;
                border-radius: 10px;
                background: #FFFFFF;
                padding: 4px;
                selection-background-color: #EEF4FB;
                selection-color: #111827;
                outline: 0;
            }
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
                subcontrol-origin: border;
                width: 18px;
                background: #F8FAFD;
                border-left: 1px solid #E7EBF1;
            }
            QAbstractSpinBox::up-button {
                subcontrol-position: top right;
                border-top-right-radius: 10px;
                border-bottom: 1px solid #E7EBF1;
            }
            QAbstractSpinBox::down-button {
                subcontrol-position: bottom right;
                border-bottom-right-radius: 10px;
            }
            QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
                background: #EEF4FB;
            }
            QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {
                background: #E2ECF8;
            }
            QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {
                width: 8px;
                height: 8px;
            }
            QAbstractSpinBox::up-arrow {
                image: url("__SPIN_PLUS_URL__");
            }
            QAbstractSpinBox::down-arrow {
                image: url("__SPIN_MINUS_URL__");
            }
            QCheckBox {
                spacing: 8px;
                color: #111827;
                background: transparent;
            }
            QCheckBox:checked {
                color: #0F172A;
                font-weight: 600;
            }
            QCheckBox::indicator {
                width: 17px;
                height: 17px;
                border: 2px solid #A8B4C2;
                border-radius: 4px;
                background: #FFFFFF;
            }
            QCheckBox::indicator:checked {
                background: #0F6CBD;
                border: 2px solid #A8B4C2;
            }
            QCheckBox::indicator:unchecked:hover {
                background: #EAF3FE;
                border-color: #0F6CBD;
            }
            QCheckBox::indicator:checked:hover {
                background: #0C5EA8;
                border-color: #A8B4C2;
            }
            QCheckBox::indicator:disabled {
                background: #F8FAFC;
                border-color: #D7DEE8;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #C4C4C4;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #8B8B8B;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QTextEdit, QListWidget {
                border: 1px solid #E7EBF1;
                border-radius: 12px;
                background: white;
            }
            QListWidget {
                padding: 4px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background: #E8F1FB;
                color: #0F172A;
            }
        """
        app.setStyleSheet(
            stylesheet
            .replace("__CHEVRON_DOWN_URL__", chevron_down_url)
            .replace("__SPIN_PLUS_URL__", spin_plus_url)
            .replace("__SPIN_MINUS_URL__", spin_minus_url)
        )

        # 全局异常钩子，写入崩溃日志
        def excepthook(exc_type, exc_value, exc_tb):
            msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            with open(crash_log, "a", encoding="utf-8") as f:
                from datetime import datetime
                f.write(f"\n[{datetime.now()}] UNHANDLED EXCEPTION:\n{msg}\n")
            sys.__excepthook__(exc_type, exc_value, exc_tb)

        sys.excepthook = excepthook

        # 创建主窗口
        window = MainWindow()
        window.show()
        window.raise_()
        window.activateWindow()

        sys.exit(app.exec())

    except Exception:
        msg = traceback.format_exc()
        with open(crash_log, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"\n[{datetime.now()}] STARTUP CRASH:\n{msg}\n")
        raise


if __name__ == "__main__":
    main()
