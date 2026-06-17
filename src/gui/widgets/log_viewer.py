"""
日志查看组件
"""
from html import escape
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QComboBox, QLineEdit, QMessageBox, QApplication,
    QMenu, QFrame
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor, QDesktopServices, QTextCharFormat, QColor, QAction
from PyQt6.QtCore import QUrl
from datetime import datetime
from pathlib import Path


class _LogTextEdit(QTextEdit):
    """带右键菜单的日志文本框"""

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        select_all = QAction("全选", self)
        select_all.triggered.connect(self.selectAll)
        menu.addAction(select_all)
        copy_all = QAction("复制全部", self)
        copy_all.triggered.connect(lambda: QApplication.clipboard().setText(self.toPlainText()))
        menu.addAction(copy_all)
        scroll_top = QAction("滚动到顶部", self)
        scroll_top.triggered.connect(lambda: self.verticalScrollBar().setValue(0))
        menu.addAction(scroll_top)
        scroll_bottom = QAction("滚动到底部", self)
        scroll_bottom.triggered.connect(
            lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))
        menu.addAction(scroll_bottom)
        menu.exec(event.globalPos())


class LogViewerWidget(QWidget):
    """日志查看组件"""

    _LEVEL_COLORS = {
        "INFO": "#60CDFF",
        "WARNING": "#FFB900",
        "ERROR": "#FF6B6B",
        "DEBUG": "#8A8A8A",
    }

    def __init__(self, parent=None, base_dir: Path = None):
        super().__init__(parent)
        self._all_logs = []
        self._base_dir = base_dir
        self._setup_ui()
        self._max_lines = 1000
    
    def _setup_ui(self):
        """设置界面"""
        self.setStyleSheet("""
            QLabel#logHeaderTitle {
                color: #111827;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#logHeaderSubtitle {
                color: #6B7280;
                font-size: 11px;
            }
            QLabel#logSummaryChip {
                color: #334155;
                background: #F8FAFD;
                border: 1px solid #E2E8F0;
                border-radius: 9px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#logStatusMeta {
                color: #6B7280;
                font-size: 11px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        header_card = QFrame()
        header_card.setObjectName("surfaceCard")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(8)

        header_top = QHBoxLayout()
        header_top.setSpacing(10)
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title_label = QLabel("运行日志")
        title_label.setObjectName("logHeaderTitle")
        header_text.addWidget(title_label)
        subtitle_label = QLabel("查看最近日志、过滤关键字并导出排查记录")
        subtitle_label.setObjectName("logHeaderSubtitle")
        header_text.addWidget(subtitle_label)
        header_top.addLayout(header_text)
        header_top.addStretch()
        header_layout.addLayout(header_top)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(8)
        self.total_chip = QLabel("总计 0 条")
        self.total_chip.setObjectName("logSummaryChip")
        summary_row.addWidget(self.total_chip)
        self.filter_chip = QLabel("当前过滤：全部")
        self.filter_chip.setObjectName("logSummaryChip")
        summary_row.addWidget(self.filter_chip)
        self.auto_scroll_chip = QLabel("自动滚动：开")
        self.auto_scroll_chip.setObjectName("logSummaryChip")
        summary_row.addWidget(self.auto_scroll_chip)
        summary_row.addStretch()
        header_layout.addLayout(summary_row)
        layout.addWidget(header_card)
        
        # 工具栏
        toolbar_card = QFrame()
        toolbar_card.setObjectName("surfaceCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(14, 10, 14, 10)
        toolbar.setSpacing(8)
        
        # 日志级别过滤
        toolbar.addWidget(QLabel("日志级别:"))
        self.level_combo = QComboBox()
        self.level_combo.setMinimumWidth(96)
        self.level_combo.addItems(["全部", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.level_combo.currentTextChanged.connect(lambda _: self._render_logs())
        toolbar.addWidget(self.level_combo)

        toolbar.addWidget(QLabel("关键字:"))
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("过滤日志（大小写不敏感）")
        self.keyword_edit.textChanged.connect(self._on_keyword_changed)
        toolbar.addWidget(self.keyword_edit)
        
        toolbar.addStretch()
        
        # 自动滚动
        self.auto_scroll_btn = QPushButton("自动滚动")
        self.auto_scroll_btn.setCheckable(True)
        self.auto_scroll_btn.setChecked(True)
        self.auto_scroll_btn.setObjectName("secondary_btn")
        self.auto_scroll_btn.toggled.connect(lambda _: self._refresh_summary())
        toolbar.addWidget(self.auto_scroll_btn)
        
        # 清空日志
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("ghost_btn")
        self.clear_btn.clicked.connect(self.clear_logs)
        toolbar.addWidget(self.clear_btn)

        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("ghost_btn")
        self.copy_btn.clicked.connect(self._copy_logs)
        toolbar.addWidget(self.copy_btn)
        
        # 导出日志
        self.export_btn = QPushButton("导出")
        self.export_btn.setObjectName("secondary_btn")
        self.export_btn.clicked.connect(self._export_logs)
        toolbar.addWidget(self.export_btn)

        self.open_dir_btn = QPushButton("打开logs")
        self.open_dir_btn.setObjectName("ghost_btn")
        self.open_dir_btn.clicked.connect(self._open_logs_dir)
        toolbar.addWidget(self.open_dir_btn)
        
        layout.addWidget(toolbar_card)
        
        self.log_text = _LogTextEdit()
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #0F172A;
                color: #D6DEEB;
                font-family: "Cascadia Code", "Cascadia Mono", Consolas, 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid #1E293B;
                border-radius: 14px;
                padding: 10px;
                selection-background-color: #274061;
            }
        """)
        layout.addWidget(self.log_text)
        
        # 状态栏
        status_card = QFrame()
        status_card.setObjectName("surfaceCard")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(14, 10, 14, 10)
        status_layout.setSpacing(10)
        self.status_label = QLabel("就绪")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        self.path_label = QLabel("logs 目录")
        self.path_label.setObjectName("logStatusMeta")
        status_layout.addWidget(self.path_label)
        layout.addWidget(status_card)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._render_logs)
        self._refresh_summary()

    def _build_log_html(self, message: str, level: str, ts: str) -> str:
        """构造单条日志的 HTML"""
        color = self._LEVEL_COLORS.get(level.upper(), "#d4d4d4")
        safe_message = escape(message or "")
        safe_level = escape(level.upper())
        safe_ts = escape(ts or "")
        return (
            f'<span style="color:#8A8A8A;">[{safe_ts}]</span> '
            f'<span style="color:{color};font-weight:600;">[{safe_level}]</span> '
            f'<span style="color:#DCDCDC;">{safe_message}</span>'
        )
    
    def add_log(self, message: str, level: str = "INFO"):
        """
        添加日志

        Args:
            message: 日志消息
            level: 日志级别 (INFO, WARNING, ERROR, DEBUG)
        """
        # 添加到文本框
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        level = level.upper()
        self._all_logs.append((message, level, ts))
        if len(self._all_logs) > self._max_lines:
            self._all_logs = self._all_logs[-self._max_lines:]

        if self._match_filters(message, level):
            self.log_text.append(self._build_log_html(message, level, ts))
        
        # 自动滚动
        if self.auto_scroll_btn.isChecked():
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.log_text.setTextCursor(cursor)
        
        # 更新状态
        self.status_label.setText(f"共 {len(self._all_logs)} 条 | 最后更新: {datetime.now().strftime('%H:%M:%S')}")
        self._refresh_summary()
    
    def add_info(self, message: str):
        """添加 INFO 日志"""
        self.add_log(message, "INFO")
    
    def add_warning(self, message: str):
        """添加 WARNING 日志"""
        self.add_log(message, "WARNING")
    
    def add_error(self, message: str):
        """添加 ERROR 日志"""
        self.add_log(message, "ERROR")
    
    def add_debug(self, message: str):
        """添加 DEBUG 日志"""
        self.add_log(message, "DEBUG")
    
    def clear_logs(self):
        """清空日志"""
        self._all_logs.clear()
        self.log_text.clear()
        self.status_label.setText("日志已清空")
        self._refresh_summary()
    
    def _on_keyword_changed(self, _):
        self._filter_timer.start(200)

    def _match_filters(self, message: str, level: str) -> bool:
        current_level = self.level_combo.currentText()
        if current_level != "全部" and level != current_level:
            return False
        kw = (self.keyword_edit.text() or "").strip().lower()
        if kw and kw not in (message or "").lower():
            return False
        return True

    def _render_logs(self):
        scrollbar = self.log_text.verticalScrollBar()
        previous_value = scrollbar.value()
        was_near_bottom = scrollbar.value() >= max(0, scrollbar.maximum() - 4)

        html_parts = []
        for entry in self._all_logs:
            msg, lvl = entry[0], entry[1]
            ts = entry[2] if len(entry) > 2 else ""
            if not self._match_filters(msg, lvl):
                continue
            html_parts.append(self._build_log_html(msg, lvl, ts))
        
        self.log_text.setHtml("<br>".join(html_parts))
        
        # 限制文档行数防止无限增长
        self._limit_lines()
        
        if self.auto_scroll_btn.isChecked() or was_near_bottom:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.log_text.setTextCursor(cursor)
        else:
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))
        self._refresh_summary(filtered_count=len(html_parts))

    def _copy_logs(self):
        text = self.log_text.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "提示", "当前没有可复制的日志")
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("日志已复制到剪贴板")
        self._refresh_summary()
    
    def _export_logs(self):
        """导出日志（支持 TXT 和 CSV 格式）"""
        from PyQt6.QtWidgets import QFileDialog
        import csv
        
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出日志",
            f"checkin_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "文本文件 (*.txt);;CSV 文件 (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            if file_path.endswith('.csv'):
                with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["时间", "级别", "消息"])
                    for entry in self._all_logs:
                        msg, lvl = entry[0], entry[1]
                        ts = entry[2] if len(entry) > 2 else ""
                        if not self._match_filters(msg, lvl):
                            continue
                        writer.writerow([ts, lvl, msg])
            else:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for entry in self._all_logs:
                        msg, lvl = entry[0], entry[1]
                        if not self._match_filters(msg, lvl):
                            continue
                        f.write(msg + "\n")
            
            self.status_label.setText(f"日志已导出: {file_path}")
            self._refresh_summary()
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出日志时发生错误：{e}")

    def _open_logs_dir(self):
        root = self._base_dir or Path.cwd()
        p = (Path(root) / "logs").resolve()
        if not p.exists():
            QMessageBox.information(self, "提示", f"logs 目录不存在：{p}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _refresh_summary(self, filtered_count: int | None = None):
        if filtered_count is None:
            filtered_count = sum(1 for entry in self._all_logs if self._match_filters(entry[0], entry[1]))
        current_level = self.level_combo.currentText() if hasattr(self, "level_combo") else "全部"
        keyword = (self.keyword_edit.text() or "").strip() if hasattr(self, "keyword_edit") else ""
        if keyword:
            filter_text = f"{current_level} / {keyword}"
        else:
            filter_text = current_level
        self.total_chip.setText(f"总计 {len(self._all_logs)} 条")
        self.filter_chip.setText(f"当前过滤：{filter_text} | 命中 {filtered_count}")
        self.auto_scroll_chip.setText(f"自动滚动：{'开' if self.auto_scroll_btn.isChecked() else '关'}")
        logs_dir = (self._base_dir or Path.cwd()) / "logs"
        self.path_label.setText(f"目录：{logs_dir}")
    
    def _limit_lines(self):
        """限制行数"""
        document = self.log_text.document()
        if document.blockCount() > self._max_lines:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                document.blockCount() - self._max_lines
            )
            cursor.removeSelectedText()
