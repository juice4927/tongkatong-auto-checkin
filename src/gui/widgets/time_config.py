"""
时间配置组件
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QCheckBox, QTimeEdit, QPushButton, QGridLayout, QSpinBox,
    QMessageBox, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, QTime, pyqtSignal


class TimeRangeWidget(QWidget):
    """时间范围选择组件"""
    
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label_text = label
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # 启用复选框
        self.checkbox = QCheckBox(label)
        self.checkbox.setMinimumWidth(78)
        self.checkbox.setChecked(True)
        layout.addWidget(self.checkbox)
        
        # 开始时间
        from_label = QLabel("从")
        from_label.setStyleSheet("color: #616161;")
        from_label.setMinimumWidth(18)
        layout.addWidget(from_label)
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm")
        self.start_time.setTime(QTime(8, 30))
        self.start_time.setMinimumWidth(78)
        self.start_time.setMinimumHeight(28)
        layout.addWidget(self.start_time)
        
        # 结束时间
        to_label = QLabel("到")
        to_label.setStyleSheet("color: #616161;")
        to_label.setMinimumWidth(18)
        layout.addWidget(to_label)
        self.end_time = QTimeEdit()
        self.end_time.setDisplayFormat("HH:mm")
        self.end_time.setTime(QTime(9, 0))
        self.end_time.setMinimumWidth(78)
        self.end_time.setMinimumHeight(28)
        layout.addWidget(self.end_time)
        layout.addStretch()
        
        # 连接信号
        self.checkbox.stateChanged.connect(self._on_checkbox_changed)
    
    def _on_checkbox_changed(self, state):
        """启用状态改变"""
        enabled = state == Qt.CheckState.Checked.value
        self.start_time.setEnabled(enabled)
        self.end_time.setEnabled(enabled)
    
    def get_config(self) -> dict:
        """获取配置"""
        return {
            'enabled': self.checkbox.isChecked(),
            'time_range': [
                self.start_time.time().toString("HH:mm"),
                self.end_time.time().toString("HH:mm")
            ],
            'label': self.label_text
        }
    
    def set_config(self, config: dict):
        """设置配置"""
        self.checkbox.setChecked(config.get('enabled', True))
        
        time_range = config.get('time_range', ['08:30', '09:00'])
        if len(time_range) >= 2:
            start = QTime.fromString(time_range[0], "HH:mm")
            end = QTime.fromString(time_range[1], "HH:mm")
            if start.isValid():
                self.start_time.setTime(start)
            if end.isValid():
                self.end_time.setTime(end)


class TimeConfigWidget(QWidget):
    """时间配置组件"""
    
    config_changed = pyqtSignal()
    
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._setup_ui()
        self._load_config()
    
    def _setup_ui(self):
        """设置界面"""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        time_group = QGroupBox("打卡时间配置")
        time_layout = QGridLayout(time_group)
        time_layout.setContentsMargins(10, 12, 10, 10)
        time_layout.setHorizontalSpacing(10)
        time_layout.setVerticalSpacing(6)
        time_layout.setColumnStretch(0, 1)
        time_layout.setColumnStretch(1, 1)
        
        # 说明
        hint_label = QLabel("系统会在每个时间范围内随机选择执行时间")
        hint_label.setStyleSheet("color: #616161; font-size: 11px;")
        hint_label.setWordWrap(True)
        time_layout.addWidget(hint_label, 0, 0, 1, 2)
        
        # 上午签到
        self.morning_signin = TimeRangeWidget("上午签到")
        time_layout.addWidget(self.morning_signin, 1, 0)
        
        # 上午签退
        self.morning_signout = TimeRangeWidget("上午签退")
        self.morning_signout.start_time.setTime(QTime(11, 30))
        self.morning_signout.end_time.setTime(QTime(12, 0))
        time_layout.addWidget(self.morning_signout, 1, 1)
        
        # 下午签到
        self.afternoon_signin = TimeRangeWidget("下午签到")
        self.afternoon_signin.start_time.setTime(QTime(13, 30))
        self.afternoon_signin.end_time.setTime(QTime(14, 0))
        time_layout.addWidget(self.afternoon_signin, 2, 0)
        
        # 下午签退
        self.afternoon_signout = TimeRangeWidget("下午签退")
        self.afternoon_signout.start_time.setTime(QTime(17, 30))
        self.afternoon_signout.end_time.setTime(QTime(18, 0))
        time_layout.addWidget(self.afternoon_signout, 2, 1)
        
        layout.addWidget(time_group)
        
        # 随机延迟配置
        delay_group = QGroupBox("随机延迟")
        delay_layout = QHBoxLayout(delay_group)
        delay_layout.setContentsMargins(10, 12, 10, 10)
        delay_layout.setSpacing(6)
        
        delay_label = QLabel("额外随机延迟:")
        delay_label.setMinimumWidth(94)
        delay_layout.addWidget(delay_label)
        
        self.min_delay = QSpinBox()
        self.min_delay.setRange(0, 300)
        self.min_delay.setValue(1)
        self.min_delay.setSuffix(" 秒")
        self.min_delay.setMinimumWidth(88)
        self.min_delay.setMinimumHeight(28)
        delay_layout.addWidget(self.min_delay)
        
        mid_label = QLabel("到")
        mid_label.setMinimumWidth(18)
        delay_layout.addWidget(mid_label)
        
        self.max_delay = QSpinBox()
        self.max_delay.setRange(0, 300)
        self.max_delay.setValue(5)
        self.max_delay.setSuffix(" 秒")
        self.max_delay.setMinimumWidth(88)
        self.max_delay.setMinimumHeight(28)
        delay_layout.addWidget(self.max_delay)
        
        delay_layout.addStretch()
        
        layout.addWidget(delay_group)

        # 有效打卡窗口配置
        makeup_group = QGroupBox("有效打卡窗口")
        makeup_layout = QGridLayout(makeup_group)
        makeup_layout.setSpacing(5)
        makeup_layout.setContentsMargins(10, 12, 10, 10)
        makeup_layout.setColumnStretch(0, 2)   # 打卡点列稍宽
        for c in range(1, 5):
            makeup_layout.setColumnStretch(c, 1)  # 4个数字列等宽

        makeup_hint = QLabel("用于判断页面右侧时间是否正常；启动时若计划时间已过但当前仍在窗口内会自动补打。结束时 > 23 表示次日，如 28:00 = 次日 04:00")
        makeup_hint.setStyleSheet("color: #616161; font-size: 11px;")
        makeup_hint.setWordWrap(True)
        makeup_layout.addWidget(makeup_hint, 0, 0, 1, 5)

        headers = ["打卡点", "开始时", "开始分", "结束时", "结束分"]
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #616161; font-size: 11px;")
            makeup_layout.addWidget(lbl, 1, col)

        self._makeup_spins = {}
        rows = [
            ("morning_signin",   "上午签到"),
            ("morning_signout",  "上午签退"),
            ("afternoon_signin", "下午签到"),
            ("afternoon_signout","下午签退"),
        ]
        for row_idx, (key, label) in enumerate(rows, start=2):
            makeup_layout.addWidget(QLabel(label), row_idx, 0)
            spins = []
            for col_idx, (lo, hi, default) in enumerate(
                [(0,23,4),(0,59,0),(0,48,8),(0,59,0)], start=1
            ):
                sp = QSpinBox()
                sp.setRange(lo, hi)
                sp.setValue(default)
                sp.setMinimumWidth(62)
                sp.setMinimumHeight(24)
                sp.setAlignment(Qt.AlignmentFlag.AlignCenter)
                makeup_layout.addWidget(sp, row_idx, col_idx)
                spins.append(sp)
            self._makeup_spins[key] = spins  # [sh, sm, eh, em]

        layout.addWidget(makeup_group)

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.clicked.connect(self._reset_to_default)
        button_layout.addWidget(self.reset_btn)
        
        button_layout.addStretch()
        
        self.apply_btn = QPushButton("应用配置")
        self.apply_btn.clicked.connect(self._apply_config)
        button_layout.addWidget(self.apply_btn)
        
        layout.addLayout(button_layout)

    def _load_config(self):
        """加载配置"""
        config = self.config_manager.config
        
        # 打卡时间
        checkin = config.checkin
        
        if 'morning_signin' in checkin:
            self.morning_signin.set_config(checkin['morning_signin'])
        if 'morning_signout' in checkin:
            self.morning_signout.set_config(checkin['morning_signout'])
        if 'afternoon_signin' in checkin:
            self.afternoon_signin.set_config(checkin['afternoon_signin'])
        if 'afternoon_signout' in checkin:
            self.afternoon_signout.set_config(checkin['afternoon_signout'])
        
        # 随机延迟
        self.min_delay.setValue(config.random_delay.min_seconds)
        self.max_delay.setValue(config.random_delay.max_seconds)

        # 有效打卡窗口
        mw = config.checkin_window
        defaults = {
            'morning_signin':   mw.morning_signin,
            'morning_signout':  mw.morning_signout,
            'afternoon_signin': mw.afternoon_signin,
            'afternoon_signout':mw.afternoon_signout,
        }
        for key, spins in self._makeup_spins.items():
            vals = defaults.get(key, [4, 0, 8, 0])
            for sp, v in zip(spins, vals):
                sp.setValue(v)
    
    def _reset_to_default(self):
        """恢复默认"""
        default_config = self.config_manager.get_default_config()
        default_checkin = default_config.checkin

        self.morning_signin.set_config(default_checkin.get('morning_signin', self.morning_signin.get_config()))
        self.morning_signout.set_config(default_checkin.get('morning_signout', self.morning_signout.get_config()))
        self.afternoon_signin.set_config(default_checkin.get('afternoon_signin', self.afternoon_signin.get_config()))
        self.afternoon_signout.set_config(default_checkin.get('afternoon_signout', self.afternoon_signout.get_config()))

        self.min_delay.setValue(default_config.random_delay.min_seconds)
        self.max_delay.setValue(default_config.random_delay.max_seconds)

        mw = default_config.checkin_window
        defaults = {
            'morning_signin': mw.morning_signin,
            'morning_signout': mw.morning_signout,
            'afternoon_signin': mw.afternoon_signin,
            'afternoon_signout': mw.afternoon_signout,
        }
        for key, spins in self._makeup_spins.items():
            for sp, v in zip(spins, defaults[key]):
                sp.setValue(v)
    
    def _apply_config(self):
        """应用配置"""
        ok, msg = self._validate_inputs()
        if not ok:
            QMessageBox.warning(self, "配置无效", msg)
            return
        self.save_to_config()
        self.config_manager.save()
        self.config_changed.emit()
        QMessageBox.information(self, "成功", "配置已应用")
    
    def save_to_config(self):
        """保存到配置"""
        config = self.config_manager.config
        
        config.checkin['morning_signin'] = self.morning_signin.get_config()
        config.checkin['morning_signout'] = self.morning_signout.get_config()
        config.checkin['afternoon_signin'] = self.afternoon_signin.get_config()
        config.checkin['afternoon_signout'] = self.afternoon_signout.get_config()
        
        config.random_delay.min_seconds = self.min_delay.value()
        config.random_delay.max_seconds = self.max_delay.value()

        # 有效打卡窗口
        mw = config.checkin_window
        for key, spins in self._makeup_spins.items():
            setattr(mw, key, [sp.value() for sp in spins])
        config.makeup_window = mw

    def _validate_inputs(self) -> tuple[bool, str]:
        def _range_ok(name: str, w: TimeRangeWidget):
            s = w.start_time.time()
            e = w.end_time.time()
            s_min = s.hour() * 60 + s.minute()
            e_min = e.hour() * 60 + e.minute()
            if e_min < s_min:
                return False, f"{name}：结束时间不能早于开始时间"
            return True, ""

        for name, w in [
            ("上午签到", self.morning_signin),
            ("上午签退", self.morning_signout),
            ("下午签到", self.afternoon_signin),
            ("下午签退", self.afternoon_signout),
        ]:
            ok, msg = _range_ok(name, w)
            if not ok:
                return False, msg

        if self.max_delay.value() < self.min_delay.value():
            return False, "随机延迟：最大值不能小于最小值"

        label_map = {
            "morning_signin": "上午签到",
            "morning_signout": "上午签退",
            "afternoon_signin": "下午签到",
            "afternoon_signout": "下午签退",
        }
        for key, spins in self._makeup_spins.items():
            sh, sm, eh, em = [sp.value() for sp in spins]
            start = sh * 60 + sm
            end = eh * 60 + em
            if end < start:
                name = label_map.get(key, key)
                return False, f"有效打卡窗口 {name}：结束时间不能早于开始时间（跨日请把结束时设为 >23，例如 28:00）"
            if end == start:
                name = label_map.get(key, key)
                return False, f"有效打卡窗口 {name}：结束时间不能等于开始时间"

        return True, ""
