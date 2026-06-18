"""仪表盘组件"""

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.holiday import HolidayChecker


class StatusCard(QFrame):
    """Win11 风格状态卡片"""

    def __init__(self, title: str, value: str = "--", meta: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(64)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(1)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.value_label.setWordWrap(True)
        layout.addWidget(self.value_label)

        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("metricMeta")
        self.meta_label.setWordWrap(True)
        self.meta_label.setVisible(bool(meta))
        layout.addWidget(self.meta_label)

        self.set_accent()

    def set_value(self, value: str):
        self.value_label.setText(value)

    def set_meta(self, value: str):
        self.meta_label.setText(value)
        self.meta_label.setVisible(bool(value))

    def set_accent(self, text_color: str = "#111827", bg_color: str = "#FFFFFF", border_color: str = "#E9EEF5"):
        self.value_label.setStyleSheet(f"color: {text_color};")
        self.setStyleSheet(
            f"""
            QFrame#metricCard {{
                background: {bg_color};
                border: 1px solid {border_color};
                border-radius: 12px;
            }}
            QLabel#metricTitle {{
                color: #6B7280;
                font-size: 10px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#metricValue {{
                font-size: 17px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#metricMeta {{
                color: #6B7280;
                font-size: 10px;
                background: transparent;
            }}
            """
        )


class InfoRow(QFrame):
    """信息行"""

    def __init__(self, title: str, value: str = "--", parent=None):
        super().__init__(parent)
        self.setObjectName("infoRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("infoRowTitle")
        self.title_label.setMinimumWidth(62)
        layout.addWidget(self.title_label)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("infoRowValue")
        self.value_label.setWordWrap(True)
        layout.addWidget(self.value_label, 1)

    def set_value(self, value: str, color: Optional[str] = None):
        self.value_label.setText(value)
        style = "color: #111827;" if not color else f"color: {color}; font-weight: 600;"
        self.value_label.setStyleSheet(style)


class SectionCard(QFrame):
    """通用分区卡片"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("sectionCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(5)

        header = QVBoxLayout()
        header.setSpacing(2)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        header.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("sectionSubtitle")
            subtitle_label.setWordWrap(True)
            header.addWidget(subtitle_label)

        layout.addLayout(header)
        divider = QFrame()
        divider.setObjectName("sectionDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(5)
        layout.addLayout(self.body_layout)


class DashboardWidget(QWidget):
    """仪表盘组件"""

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._orchestrator = None
        self._guard_state_provider: Optional[Callable[[], dict]] = None
        self._holiday_checker = None
        self._holiday_config_key = None

        self._setup_ui()

        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self._update_time_display)
        self.time_timer.start(1000)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status_display)
        self.status_timer.start(5000)

    def set_orchestrator(self, orchestrator):
        self._orchestrator = orchestrator
        self._update_display()

    def set_guard_state_provider(self, provider: Callable[[], dict]):
        self._guard_state_provider = provider
        self._update_display()

    def _setup_ui(self):
        self.setStyleSheet(
            """
            QWidget#dashboardPage {
                background: transparent;
            }
            QFrame#heroCard, QFrame#sectionCard {
                background: #FFFFFF;
                border: 1px solid #E7EBF2;
                border-radius: 16px;
            }
            QFrame#sectionDivider {
                background: #EEF2F7;
                border: none;
                border-radius: 1px;
            }
            QLabel#heroEyebrow {
                color: #0F6CBD;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            QLabel#heroDayNumber {
                color: #111827;
                font-size: 34px;
                font-weight: 800;
                min-width: 56px;
            }
            QLabel#heroDate {
                color: #111827;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#heroSubline {
                color: #6B7280;
                font-size: 11px;
            }
            QLabel#heroBadge {
                color: #1F2937;
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#sectionTitle {
                color: #111827;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#sectionSubtitle {
                color: #6B7280;
                font-size: 10px;
            }
            QFrame#infoRow {
                background: transparent;
                border: none;
                border-radius: 0px;
            }
            QLabel#infoRowTitle {
                color: #6B7280;
                font-size: 10px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#infoRowValue {
                color: #111827;
                font-size: 11px;
                background: transparent;
            }
            """
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root_layout.addWidget(scroll)

        page = QWidget()
        page.setObjectName("dashboardPage")
        scroll.setWidget(page)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        hero = QFrame()
        hero.setObjectName("heroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(12, 10, 12, 10)
        hero_layout.setSpacing(12)

        hero_intro_layout = QVBoxLayout()
        hero_intro_layout.setSpacing(4)

        self.hero_eyebrow = QLabel("今日概览")
        self.hero_eyebrow.setObjectName("heroEyebrow")
        hero_intro_layout.addWidget(self.hero_eyebrow)

        hero_date_row = QHBoxLayout()
        hero_date_row.setSpacing(10)

        self.hero_day_number = QLabel("--")
        self.hero_day_number.setObjectName("heroDayNumber")
        hero_date_row.addWidget(self.hero_day_number, 0, Qt.AlignmentFlag.AlignTop)

        hero_date_text_layout = QVBoxLayout()
        hero_date_text_layout.setSpacing(1)

        self.hero_date = QLabel("--")
        self.hero_date.setObjectName("heroDate")
        hero_date_text_layout.addWidget(self.hero_date)

        self.hero_subline = QLabel("等待刷新")
        self.hero_subline.setObjectName("heroSubline")
        self.hero_subline.setWordWrap(True)
        hero_date_text_layout.addWidget(self.hero_subline)

        hero_date_row.addLayout(hero_date_text_layout, 1)
        hero_intro_layout.addLayout(hero_date_row)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)

        self.weekday_badge = QLabel("星期 --")
        self.weekday_badge.setObjectName("heroBadge")
        badge_row.addWidget(self.weekday_badge)

        self.workday_badge = QLabel("待判断")
        self.workday_badge.setObjectName("heroBadge")
        badge_row.addWidget(self.workday_badge)

        self.holiday_badge = QLabel("无节假日")
        self.holiday_badge.setObjectName("heroBadge")
        badge_row.addWidget(self.holiday_badge)
        badge_row.addStretch()

        hero_intro_layout.addLayout(badge_row)
        hero_intro_layout.addStretch()
        hero_layout.addLayout(hero_intro_layout, 4)

        metrics_layout = QGridLayout()
        metrics_layout.setHorizontalSpacing(6)
        metrics_layout.setVerticalSpacing(6)
        metrics_layout.setColumnStretch(0, 1)
        metrics_layout.setColumnStretch(1, 1)
        metrics_layout.setColumnStretch(2, 1)

        self.today_status_card = StatusCard("今日状态", "待判断", "根据节假日与配置自动判断")
        self.next_action_card = StatusCard("下次打卡", "--", "未启动时不会生成实际时间")
        self.guard_summary_card = StatusCard("守护状态", "待机", "显示恢复与守护状态")

        metrics_layout.addWidget(self.today_status_card, 0, 0)
        metrics_layout.addWidget(self.next_action_card, 0, 1)
        metrics_layout.addWidget(self.guard_summary_card, 0, 2)
        hero_layout.addLayout(metrics_layout, 5)
        layout.addWidget(hero)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(6)

        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        status_section = SectionCard("打卡时间窗口", "今日四个打卡点")
        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(5)
        status_grid.setVerticalSpacing(5)
        status_grid.setColumnStretch(0, 1)
        status_grid.setColumnStretch(1, 1)
        self.morning_signin_card = StatusCard("上午签到", "--:--")
        self.morning_signout_card = StatusCard("上午签退", "--:--")
        self.afternoon_signin_card = StatusCard("下午签到", "--:--")
        self.afternoon_signout_card = StatusCard("下午签退", "--:--")
        status_grid.addWidget(self.morning_signin_card, 0, 0)
        status_grid.addWidget(self.morning_signout_card, 0, 1)
        status_grid.addWidget(self.afternoon_signin_card, 1, 0)
        status_grid.addWidget(self.afternoon_signout_card, 1, 1)
        status_section.body_layout.addLayout(status_grid)
        left_col.addWidget(status_section)

        plan_section = SectionCard("今日计划", "执行顺序与最新结果")
        self.plan_label = QLabel("启动后显示...")
        self.plan_label.setWordWrap(True)
        self.plan_label.setStyleSheet("color: #111827; font-size: 11px; line-height: 1.4;")
        plan_section.body_layout.addWidget(self.plan_label)

        self.plan_summary_label = QLabel("")
        self.plan_summary_label.setWordWrap(True)
        self.plan_summary_label.setStyleSheet("color: #0F6CBD; font-size: 11px; font-weight: 600;")
        plan_section.body_layout.addWidget(self.plan_summary_label)

        self.last_label = QLabel("")
        self.last_label.setWordWrap(True)
        self.last_label.setStyleSheet("color: #6B7280; font-size: 10px;")
        plan_section.body_layout.addWidget(self.last_label)
        left_col.addWidget(plan_section)

        detail_section = SectionCard("当前配置", "连接目标与执行规则")
        self.device_row = InfoRow("设备", "--")
        self.package_row = InfoRow("包名", "--")
        self.notify_row = InfoRow("通知", "--")
        self.rule_row = InfoRow("规则", "--")
        detail_section.body_layout.addWidget(self.device_row)
        detail_section.body_layout.addWidget(self.package_row)
        detail_section.body_layout.addWidget(self.notify_row)
        detail_section.body_layout.addWidget(self.rule_row)
        right_col.addWidget(detail_section)

        guard_section = SectionCard("守护恢复", "恢复状态与最近结果")
        self.guard_status_label = QLabel("未知")
        self.guard_status_label.setWordWrap(True)
        self.guard_status_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #111827;")
        guard_section.body_layout.addWidget(self.guard_status_label)

        self.guard_last_recovery_row = InfoRow("最近恢复", "暂无")
        self.guard_action_row = InfoRow("恢复动作", "暂无")
        self.guard_last_error_row = InfoRow("最近错误", "暂无")
        guard_section.body_layout.addWidget(self.guard_last_recovery_row)
        guard_section.body_layout.addWidget(self.guard_action_row)
        guard_section.body_layout.addWidget(self.guard_last_error_row)
        right_col.addWidget(guard_section)

        action_section = SectionCard("快捷操作", "排查与刷新")
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.clicked.connect(self._update_display)
        action_row.addWidget(self.refresh_btn)
        self.test_holiday_btn = QPushButton("测试节假日")
        self.test_holiday_btn.clicked.connect(self._test_holiday)
        action_row.addWidget(self.test_holiday_btn)
        action_section.body_layout.addLayout(action_row)
        right_col.addWidget(action_section)

        content_layout.addLayout(left_col, 3)
        content_layout.addLayout(right_col, 2)
        layout.addLayout(content_layout)

        self._update_display()

    def _get_holiday_checker(self) -> HolidayChecker:
        holiday_config = self.config_manager.config.holiday
        config_key = (
            holiday_config.skip_weekend,
            holiday_config.skip_holiday,
            tuple(holiday_config.extra_workdays),
            tuple(holiday_config.extra_holidays),
        )
        if self._holiday_checker is None or self._holiday_config_key != config_key:
            self._holiday_checker = HolidayChecker(
                skip_weekend=config_key[0],
                skip_holiday=config_key[1],
                extra_workdays=list(config_key[2]),
                extra_holidays=list(config_key[3]),
            )
            self._holiday_config_key = config_key
        return self._holiday_checker

    def _update_time_display(self):
        today = date.today()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        self.hero_day_number.setText(today.strftime("%d"))
        self.hero_date.setText(today.strftime("%Y年%m月"))
        self.hero_subline.setText(f"{weekdays[today.weekday()]} · 保持常驻即可自动执行")
        self.weekday_badge.setText(f"星期 {weekdays[today.weekday()]}")

    def _update_status_display(self):
        today = date.today()
        checker = self._get_holiday_checker()

        is_workday = checker.is_workday(today)
        holiday_name = checker.get_holiday_name(today)
        if is_workday:
            workday_text = "工作日"
            self.today_status_card.set_value("工作日")
            self.today_status_card.set_meta("今天会按规则参与打卡")
            self.today_status_card.set_accent("#0F7B0F", "#EEF8F0", "#CDE8D3")
            self.workday_badge.setStyleSheet("color: #0F7B0F; background: transparent; border: none; padding: 0px; font-size: 12px; font-weight: 600;")
        else:
            workday_text = "休息日"
            self.today_status_card.set_value("休息日")
            self.today_status_card.set_meta("今天默认不执行自动打卡")
            self.today_status_card.set_accent("#C42B1C", "#FCEEEE", "#F3CCCC")
            self.workday_badge.setStyleSheet("color: #C42B1C; background: transparent; border: none; padding: 0px; font-size: 12px; font-weight: 600;")
        self.workday_badge.setText(workday_text)

        if holiday_name:
            self.holiday_badge.setText(holiday_name)
            self.holiday_badge.setStyleSheet("color: #8A4B00; background: transparent; border: none; padding: 0px; font-size: 12px; font-weight: 600;")
            if holiday_name.startswith("手动"):
                self.today_status_card.set_meta(f"今天按{holiday_name}规则处理")
        else:
            self.holiday_badge.setText("无节假日")
            self.holiday_badge.setStyleSheet("color: #4B5563; background: transparent; border: none; padding: 0px; font-size: 12px; font-weight: 600;")

        self._update_checkin_cards()
        self._update_plan(is_workday)
        self._update_guard_status()
        self._update_runtime_snapshot()

    def _update_display(self):
        self._update_time_display()
        self._update_status_display()

    def _update_checkin_cards(self):
        checkin_times = self.config_manager.get_checkin_times()
        cards = {
            "morning_signin": self.morning_signin_card,
            "morning_signout": self.morning_signout_card,
            "afternoon_signin": self.afternoon_signin_card,
            "afternoon_signout": self.afternoon_signout_card,
        }

        for key, card in cards.items():
            if key not in checkin_times:
                continue
            config = checkin_times[key]
            if config.enabled:
                time_range = config.time_range
                card.set_value(f"{time_range[0]} - {time_range[1]}")
                card.set_meta("已启用")
                card.set_accent("#111827", "#FFFFFF", "#E9EEF5")
            else:
                card.set_value("已禁用")
                card.set_meta("不会参与今日计划")
                card.set_accent("#6B7280", "#FFFFFF", "#E9EEF5")

    def _update_plan(self, is_workday: bool):
        def _failure_label(failure_code: str, fallback_success: bool) -> str:
            mapping = {
                "navigation_failed": "导航失败",
                "button_not_found": "定位失败",
                "result_unconfirmed": "确认失败",
                "app_popup_failed": "弹窗失败",
                "gps_runtime_failed": "定位失败",
                "gps_precheck_failed": "GPS失败",
                "login_timeout": "登录失败",
                "app_not_found": "启动失败",
                "device_connect_failed": "连接失败",
                "device_not_connected": "未连设备",
                "device_unresponsive": "设备无响应",
                "scheduler_error": "调度异常",
                "execution_error": "执行异常",
            }
            if failure_code:
                return mapping.get(failure_code, failure_code)
            return "成功" if fallback_success else "失败"

        if not is_workday:
            self.plan_label.setText("今天是休息日，无需打卡。")
            self.plan_summary_label.setText("今日共 0 项待执行")
            self.last_label.setText("")
            self.next_action_card.set_value("无需打卡")
            self.next_action_card.set_meta("工作日才会生成执行时间")
            self.next_action_card.set_accent("#6B7280", "#FFFFFF", "#E9EEF5")
            return

        if self._orchestrator is None:
            self.plan_label.setText("请先连接设备并点击“启动”，系统才会生成今天的实际打卡计划。")
            enabled_count = sum(1 for item in self.config_manager.get_checkin_times().values() if item.enabled)
            self.plan_summary_label.setText(f"当前已启用 {enabled_count}/4 个打卡点")
            self.last_label.setText("")
            self.next_action_card.set_value("等待启动")
            self.next_action_card.set_meta("尚未生成今日执行时间")
            self.next_action_card.set_accent("#0F6CBD", "#FFFFFF", "#D8E6F7")
            return

        times = self._orchestrator.get_checkin_times()
        if not times:
            self.plan_label.setText("今日无计划任务。")
            self.plan_summary_label.setText("当前没有可执行任务")
            self.last_label.setText("")
            self.next_action_card.set_value("无计划")
            self.next_action_card.set_meta("当前未生成打卡任务")
            self.next_action_card.set_accent("#6B7280", "#FFFFFF", "#E9EEF5")
            return

        labels = {
            "morning_signin": "上午签到",
            "morning_signout": "上午签退",
            "afternoon_signin": "下午签到",
            "afternoon_signout": "下午签退",
        }
        now = datetime.now()

        # 收集已完成结果：{ "上午签到": True, "上午签退": False, ... }
        result_map = {}
        try:
            for action_name, success, _msg, _ts in self._orchestrator.get_daily_results():
                result_map[action_name] = success
        except Exception:
            pass

        plan_lines = []
        for key, label in labels.items():
            t = times.get(key)
            if t is None:
                plan_lines.append(f"○  {label}  ·  已禁用")
                continue
            time_str = t.strftime("%H:%M:%S")
            if label in result_map:
                ok = result_map[label]
                status = "✅ 已完成" if ok else "❌ 失败"
            elif t < now:
                status = "已过"
            else:
                status = "待执行"
            plan_lines.append(f"{label}  ·  {time_str}  ·  {status}")

        upcoming = [t for t in times.values() if t and t >= now]
        enabled_count = sum(1 for t in times.values() if t is not None)
        success_stats = None
        if hasattr(self._orchestrator, "get_daily_success_stats"):
            try:
                success_stats = self._orchestrator.get_daily_success_stats()
            except Exception:
                success_stats = None
        stats_note = ""
        if success_stats and success_stats.get("total", 0):
            stats_note = (
                f" · 成功率 {success_stats['success_rate']:.1f}%"
                f"({success_stats['success']}/{success_stats['total']})"
            )
        if upcoming:
            nt = min(upcoming)
            self.plan_summary_label.setText(f"已启用 {enabled_count}/4 项 · 剩余 {len(upcoming)} 项{stats_note}")
            self.next_action_card.set_value(nt.strftime("%H:%M:%S"))
            self.next_action_card.set_meta("距离最近的自动执行时间")
            self.next_action_card.set_accent("#0F6CBD", "#FFFFFF", "#D8E6F7")
        else:
            self.plan_summary_label.setText(f"已启用 {enabled_count}/4 项 · 今日已完成{stats_note}")
            self.next_action_card.set_value("已完成")
            self.next_action_card.set_meta("今天没有剩余自动任务")
            self.next_action_card.set_accent("#6B7280", "#FFFFFF", "#E9EEF5")

        last = None
        last_meta = None
        try:
            last = self._orchestrator.get_last_result()
            if hasattr(self._orchestrator, "get_last_result_meta"):
                last_meta = self._orchestrator.get_last_result_meta()
        except Exception:
            last = None
        if last:
            action_name, success, message, ts = last
            failure_code = last_meta.get("failure_code", "") if last_meta else ""
            icon = _failure_label(failure_code, success)
            recovery_action = self._recovery_action_label(last_meta.get("recovery_action", "")) if last_meta else ""
            recovery_note = f" · 导航恢复 {recovery_action}" if recovery_action and recovery_action != "自动判断" else ""
            self.last_label.setText(f"最近结果：{icon} · {action_name} · {ts}{recovery_note} · {message}")
        else:
            self.last_label.setText("最近结果：暂无")

        self.plan_label.setText("\n".join(plan_lines))

    def _update_runtime_snapshot(self):
        config = self.config_manager.config

        device_text = f"{config.mumu.host}:{config.mumu.port}"
        if getattr(config.mumu, "adb_path", ""):
            device_text += " · 手动ADB"
        else:
            device_text += " · 自动ADB"
        self.device_row.set_value(device_text)

        package_name = config.app.package_name or "未填写"
        self.package_row.set_value(package_name, None if config.app.package_name else "#B45309")

        if config.notification.enabled and config.notification.webhook:
            notify_text = "已启用"
            notify_color = "#0F7B0F"
        elif config.notification.enabled:
            notify_text = "已开启，但未填写 Key"
            notify_color = "#B45309"
        else:
            notify_text = "未启用"
            notify_color = "#6B7280"
        self.notify_row.set_value(notify_text, notify_color)

        rules = []
        if config.holiday.skip_weekend:
            rules.append("跳过周末")
        if config.holiday.skip_holiday:
            rules.append("跳过节假日")
        if not rules:
            rules.append("每天都执行")
        self.rule_row.set_value(" / ".join(rules))

    @staticmethod
    def _recovery_action_label(action: str) -> str:
        mapping = {
            "restart_scheduler": "重启调度",
            "reconnect_device": "重连设备",
            "reset_device_session": "重置会话",
            "return_home_retry": "回主界面重试",
            "restart_app_retry": "重开交建通重试",
        }
        return mapping.get(action or "", "自动判断")

    def _update_guard_status(self):
        if not self._guard_state_provider:
            self.guard_status_label.setText("未连接状态源")
            self.guard_status_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #6B7280;")
            self.guard_last_recovery_row.set_value("暂无")
            self.guard_action_row.set_value("暂无")
            self.guard_last_error_row.set_value("暂无")
            self.guard_summary_card.set_value("待机")
            self.guard_summary_card.set_meta("尚未接入守护状态")
            self.guard_summary_card.set_accent("#6B7280", "#FFFFFF", "#E9EEF5")
            return

        try:
            state = self._guard_state_provider() or {}
        except Exception:
            self.guard_status_label.setText("状态读取失败")
            self.guard_status_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #C42B1C;")
            self.guard_last_recovery_row.set_value("暂无")
            self.guard_action_row.set_value("未知")
            self.guard_last_error_row.set_value("暂无")
            self.guard_summary_card.set_value("异常")
            self.guard_summary_card.set_meta("无法读取守护状态")
            self.guard_summary_card.set_accent("#C42B1C", "#FFFFFF", "#F1D4CF")
            return

        keep_alive_enabled = bool(state.get("keep_alive_enabled", True))
        recovery_in_progress = bool(state.get("recovery_in_progress", False))
        desired_running = bool(state.get("desired_running", False))
        is_connected = bool(state.get("is_connected", False))
        is_running = bool(state.get("is_running", False))
        fail_count = int(state.get("recovery_fail_count", 0) or 0)
        next_retry_at = state.get("recovery_next_retry_at") or "-"
        paused_until = state.get("recovery_paused_until") or "-"
        recovery_action = self._recovery_action_label(state.get("last_recovery_action", ""))

        if not keep_alive_enabled:
            status_text = "未启用"
            status_color = "#6B7280"
            accent = ("#6B7280", "#FFFFFF", "#E9EEF5")
        elif recovery_in_progress:
            status_text = "恢复中"
            status_color = "#B45309"
            accent = ("#B45309", "#FFFFFF", "#F3DEC9")
        elif desired_running and is_connected and is_running:
            status_text = "守护中"
            status_color = "#0F7B0F"
            accent = ("#0F7B0F", "#FFFFFF", "#D7E9DA")
        elif desired_running:
            status_text = "待恢复"
            status_color = "#C42B1C"
            accent = ("#C42B1C", "#FFFFFF", "#F1D4CF")
        else:
            status_text = "待机"
            status_color = "#0F6CBD"
            accent = ("#0F6CBD", "#FFFFFF", "#D8E6F7")

        suffix_parts = [f"连续失败 {fail_count} 次"]
        if keep_alive_enabled:
            suffix_parts.append(f"动作 {recovery_action}")
        if paused_until != "-":
            suffix_parts.append(f"暂停至 {paused_until}")
        elif next_retry_at != "-":
            suffix_parts.append(f"下次重试 {next_retry_at}")
        self.guard_status_label.setText(f"{status_text} · {' · '.join(suffix_parts)}")
        self.guard_status_label.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {status_color};")

        self.guard_summary_card.set_value(status_text)
        self.guard_summary_card.set_meta(" · ".join(suffix_parts))
        self.guard_summary_card.set_accent(*accent)

        last_result = state.get("last_recovery_result", "暂无")
        last_time = state.get("last_recovery_time", "-")
        self.guard_last_recovery_row.set_value(f"{last_result} @ {last_time}")
        self.guard_action_row.set_value(recovery_action)

        last_error = state.get("last_recovery_error") or "暂无"
        self.guard_last_error_row.set_value(last_error)

    def _test_holiday(self):
        checker = HolidayChecker(
            skip_weekend=self.config_manager.config.holiday.skip_weekend,
            skip_holiday=self.config_manager.config.holiday.skip_holiday,
            extra_workdays=self.config_manager.config.holiday.extra_workdays,
            extra_holidays=self.config_manager.config.holiday.extra_holidays,
        )

        today = date.today()
        results = []
        for i in range(7):
            d = today + timedelta(days=i)
            is_workday = checker.is_workday(d)
            holiday_name = checker.get_holiday_name(d)
            status = "工作日" if is_workday else "休息日"
            holiday = f"（{holiday_name}）" if holiday_name else ""
            results.append(f"{d.strftime('%m/%d')} · {status}{holiday}")

        QMessageBox.information(self, "节假日测试", "\n".join(results))
