"""
打卡按钮查找模块 — 多策略降级查找打卡按钮

职责：
- 在考勤页面查找"签到"/"签退"按钮
- 三策略降级：行锚定法 → 全文本扫描 → 兜底方案
- 已打卡状态检测
"""
import logging
import time
import re
from datetime import datetime
from typing import List, Optional, Tuple, Callable
from enum import Enum

logger = logging.getLogger(__name__)

# 从 xml_parser 导入，避免循环引用
# 这些会在运行时通过参数传入或模块级导入完成
from .xml_parser import parse_hierarchy_xml, TIME_PATTERN


# 已打卡时间范围判断：(is_morning, is_signin) -> callable(h, m) -> bool
# 右侧时间文本既是显示也是按钮，以下为正常签到的备选时间范围
ALREADY_CHECKED_IN_RANGES = {
    (True,  True):  lambda h, m: 5 <= h < 8,                                          # 上午签到：05:00-08:00
    (True,  False): lambda h, m: (h == 11 and m >= 30) or (12 <= h < 13) or (h == 13 and m == 0),  # 上午签退：11:30-13:00
    (False, True):  lambda h, m: 12 <= h < 13 or (h == 13 and m <= 30),               # 下午签到：12:00-13:30
    (False, False): lambda h, m: 17 <= h or h < 4,                                     # 下午签退：17:00-次日04:00
}


class ButtonFinder:
    """
    打卡按钮查找器。

    在考勤页面中，通过多策略降级查找"签到"/"签退"按钮。
    策略 A: 行锚定法（ET 解析）— 找左侧不可点击标签确定行 Y 范围
    策略 B: 全文本扫描 — 直接扫描全部可点击按钮
    策略 C: 兜底方案 — 放宽条件 + u2 API + 坐标推算
    """

    def __init__(self, device, already_checked_in_error_class=None):
        """
        Args:
            device: uiautomator2 设备实例
            already_checked_in_error_class: 已打卡异常类（用于抛出已打卡状态）
        """
        self._device = device
        self._already_checked_in_error = already_checked_in_error_class or Exception
        self._makeup_windows = {}
        self._now_provider = datetime.now
        self.last_action_note = ""

    # ── 公共入口 ──────────────────────────────────────────────────────

    def default_find_and_click(self, action, action_text: str, is_morning: bool,
                               is_signin: bool, slot_label: str) -> bool:
        """
        查找并点击打卡按钮（多策略降级）。

        Args:
            action: CheckinAction 枚举值
            action_text: "签到" 或 "签退"
            is_morning: 是否为上午时段
            is_signin: 是否为签到动作
            slot_label: "上午" 或 "下午"

        Returns:
            True 表示已点击按钮，False 表示未找到
        """
        from datetime import datetime, time as _time

        try:
            screen_w, screen_h = self._device.window_size()
            mid_x = screen_w * 0.5

            xml = self._device.dump_hierarchy(compressed=False)
            all_nodes = parse_hierarchy_xml(xml)

            if not all_nodes:
                logger.warning("XML 解析结果为空，等待 1 秒后重试")
                time.sleep(1)
                xml = self._device.dump_hierarchy(compressed=False)
                all_nodes = parse_hierarchy_xml(xml)

            # 策略 A
            result = self._strategy_row_anchor(
                all_nodes, is_morning, is_signin,
                action_text, slot_label, mid_x, screen_w
            )
            if result is not None:
                return result

            # 策略 B
            result = self._strategy_full_scan(
                all_nodes, is_morning, is_signin,
                action_text, slot_label, mid_x, screen_w
            )
            if result is not None:
                return result

            # 策略 C
            logger.warning("[全策略] 行锚定和全文扫描均未找到按钮，回退兜底方案")
            return self._fallback_find_and_click(
                action, is_morning, is_signin, action_text, slot_label, xml, screen_w
            )

        except self._already_checked_in_error:
            raise
        except Exception as e:
            logger.error(f"查找按钮出错: {e}")
            return False

    def set_makeup_windows(self, windows: dict) -> None:
        """设置有效打卡窗口，格式与配置文件一致：{action_key: [sh, sm, eh, em]}。"""
        self._makeup_windows = dict(windows or {})

    # ── 策略 A: 行锚定法 ─────────────────────────────────────────────

    def _strategy_row_anchor(self, all_nodes, is_morning, is_signin,
                             action_text, slot_label, mid_x, screen_w) -> Optional[bool]:
        """找左侧不可点击的"签到"/"签退"标签确定行 Y 范围，再在目标行右侧找可点击节点。"""
        row_labels = []
        for n in all_nodes:
            t = n.get('text', '').strip()
            if t not in ('签到', '签退'):
                continue
            if n.get('clickable', '') != 'false':
                continue
            b = n.get('_bounds')
            if not b:
                continue
            x1, y1, x2, y2 = b
            cx = (x1 + x2) // 2
            if cx >= mid_x:
                continue
            cy = (y1 + y2) // 2
            row_labels.append((t, y1, y2, cy))

        row_labels.sort(key=lambda r: r[3])
        logger.debug(f"[行锚定] 左侧行标签({len(row_labels)}行): {[(t, cy) for t, _, _, cy in row_labels]}")

        same_action_rows = [row for row in row_labels if row[0] == action_text]
        if same_action_rows:
            target_row = same_action_rows[0] if is_morning else same_action_rows[-1]
        elif row_labels:
            target_row = row_labels[0] if is_morning else row_labels[-1]
        else:
            logger.warning("[行锚定] 未找到可用的左侧行标签")
            return None

        _, row_y1, row_y2, row_cy = target_row
        row_height = row_y2 - row_y1
        margin = max(15, row_height * 0.3) if row_height > 0 else 30
        y_lo = row_y1 - margin
        y_hi = row_y2 + margin

        right_clickables = []
        for n in all_nodes:
            if n.get('clickable', '') != 'true':
                continue
            b = n.get('_bounds')
            if not b:
                continue
            x1, y1, x2, y2 = b
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            if cx <= mid_x:
                continue
            if not (y_lo <= cy <= y_hi):
                continue
            t = n.get('text', '').strip()
            right_clickables.append((t, cx, cy))

        logger.debug(f"[行锚定] 目标行[{slot_label}/{action_text}@{row_cy}] 右侧可点击: {right_clickables}")

        if not right_clickables:
            return None

        for t, cx, cy in right_clickables:
            if t == action_text:
                logger.info(f"[行锚定] 点击'{action_text}'按钮（{slot_label}，未打卡）: ({cx},{cy})")
                self._device.click(cx, cy)
                return True

        time_nodes = [(t, cx, cy) for t, cx, cy in right_clickables
                      if TIME_PATTERN.match(t)]
        if time_nodes:
            t_str, cx, cy = time_nodes[0]
            return self._handle_target_time_node(
                t_str, cx, cy, is_morning, is_signin, action_text, slot_label
            )

        return None

    # ── 策略 B: 全文本扫描 ───────────────────────────────────────────

    def _strategy_full_scan(self, all_nodes, is_morning, is_signin,
                            action_text, slot_label, mid_x, screen_w) -> Optional[bool]:
        """直接在整个界面找所有"签到"/"签退"可点击按钮和时间节点，按 Y 排序后根据上午/下午选择对应索引。"""
        signin_buttons = []
        time_nodes_all = []

        for n in all_nodes:
            if n.get('clickable', '') != 'true':
                continue
            b = n.get('_bounds')
            if not b:
                continue
            x1, y1, x2, y2 = b
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            if cx <= mid_x:
                continue
            t = n.get('text', '').strip()
            if t == action_text:
                signin_buttons.append((cy, cx, t))
            elif TIME_PATTERN.match(t):
                time_nodes_all.append((cy, cx, t))

        signin_buttons.sort(key=lambda x: x[0])
        time_nodes_all.sort(key=lambda x: x[0])

        logger.debug(f"[全文扫描] '{action_text}'按钮: {[(t, cy) for cy, _, t in signin_buttons]}")
        logger.debug(f"[全文扫描] 时间节点: {[(t, cy) for cy, _, t in time_nodes_all]}")

        if signin_buttons:
            cy, cx, t = signin_buttons[0 if is_morning else -1]
            logger.info(f"[全文扫描] 点击'{action_text}'按钮（{slot_label}）: ({cx},{cy})")
            self._device.click(cx, cy)
            return True

        if time_nodes_all:
            cy, cx, t_str = time_nodes_all[0 if is_morning else -1]
            return self._handle_target_time_node(
                t_str, cx, cy, is_morning, is_signin, action_text, slot_label
            )

        return None

    # ── 策略 C: 兜底方案 ─────────────────────────────────────────────

    def _fallback_find_and_click(self, action, is_morning, is_signin,
                                 action_text, slot_label, xml, screen_w):
        """放宽条件全屏扫描 + u2 API 查找。"""
        right_threshold = screen_w * 0.6

        buttons = self._device(text=action_text, clickable=True)
        if buttons.exists:
            button_list = []
            for i in range(buttons.count):
                try:
                    info = buttons[i].info
                    bounds = info.get('bounds', {})
                    y_center = (bounds.get('top', 0) + bounds.get('bottom', 0)) // 2
                    x_center = (bounds.get('left', 0) + bounds.get('right', 0)) // 2
                    if x_center > right_threshold:
                        button_list.append((i, y_center))
                except Exception as e:
                    logger.debug(f"获取按钮 {i} 信息失败: {e}")
                    continue
            if button_list:
                button_list.sort(key=lambda x: x[1])
                target_idx = button_list[0][0] if is_morning else button_list[-1][0]
                logger.info(f"[兜底] 点击'{action_text}'按钮（{slot_label}）")
                buttons[target_idx].click()
                return True

        all_nodes = parse_hierarchy_xml(xml)
        right_nodes = []
        for n in all_nodes:
            t = n.get('text', '').strip()
            if not TIME_PATTERN.match(t):
                continue
            b = n.get('_bounds')
            if not b:
                continue
            x1, y1, x2, y2 = b
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            c = n.get('clickable', 'false')
            if cx > right_threshold:
                right_nodes.append((cy, cx, t, c))

        right_nodes.sort(key=lambda x: x[0])
        logger.debug(f"[兜底] 右侧时间节点: {[(t, c) for _, _, t, c in right_nodes]}")

        clickable_right_nodes = [node for node in right_nodes if node[3] == "true"]
        if not clickable_right_nodes:
            logger.warning("[兜底] 未找到可用的时间节点")
            return False

        cy, cx, time_str, _ = clickable_right_nodes[0 if is_morning else -1]
        return self._handle_target_time_node(
            time_str, cx, cy, is_morning, is_signin, action_text, slot_label
        )

    # ── 工具方法 ──────────────────────────────────────────────────────

    @staticmethod
    def _action_key(is_morning: bool, is_signin: bool) -> str:
        if is_morning and is_signin:
            return "morning_signin"
        if is_morning and not is_signin:
            return "morning_signout"
        if not is_morning and is_signin:
            return "afternoon_signin"
        return "afternoon_signout"

    @staticmethod
    def _parse_time_text(time_str: str) -> Optional[tuple[int, int]]:
        if not TIME_PATTERN.match(time_str or ""):
            return None
        h, m = time_str.split(":", 1)
        return int(h), int(m)

    @staticmethod
    def _normal_start_minutes(is_morning: bool, is_signin: bool) -> int:
        starts = {
            (True, True): 5 * 60,
            (True, False): 11 * 60 + 30,
            (False, True): 12 * 60,
            (False, False): 17 * 60,
        }
        return starts[(is_morning, is_signin)]

    @classmethod
    def _abnormal_label(cls, is_morning: bool, is_signin: bool, h: int, m: int) -> str:
        if is_signin:
            return "迟到"
        if h * 60 + m < cls._normal_start_minutes(is_morning, is_signin):
            return "早退"
        return "异常"

    def _is_now_in_makeup_window(self, action_key: str) -> bool:
        window = self._makeup_windows.get(action_key)
        if not window or len(window) != 4:
            return False

        sh, sm, eh, em = window
        now = self._now_provider()
        now_minutes = now.hour * 60 + now.minute
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em

        if end_minutes > 1440:
            return now_minutes >= start_minutes or now_minutes < (end_minutes - 1440)
        return start_minutes <= now_minutes < end_minutes

    def _is_time_in_configured_window(self, action_key: str, h: int, m: int) -> Optional[bool]:
        window = self._makeup_windows.get(action_key)
        if not window or len(window) != 4:
            return None

        sh, sm, eh, em = window
        time_minutes = h * 60 + m
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em

        if end_minutes > 1440:
            return time_minutes >= start_minutes or time_minutes < (end_minutes - 1440)
        return start_minutes <= time_minutes < end_minutes

    def _handle_target_time_node(self, time_str: str, cx: int, cy: int,
                                 is_morning: bool, is_signin: bool,
                                 action_text: str, slot_label: str) -> bool:
        """目标行右侧已显示时间时，根据有效打卡窗口决定已完成、跳过或签退补打。"""
        self.last_action_note = ""
        parsed = self._parse_time_text(time_str)
        if not parsed:
            return False

        h, m = parsed
        action_key = self._action_key(is_morning, is_signin)
        is_normal = self._is_time_in_configured_window(action_key, h, m)
        if is_normal is None:
            raise self._already_checked_in_error(
                f"今日{slot_label}{action_text}显示时间（{time_str}），但未配置有效打卡窗口，跳过",
                in_correct_slot=True,
                checkin_time=time_str,
            )
        if is_normal:
            raise self._already_checked_in_error(
                f"今日{slot_label}{action_text}已完成（{time_str}）",
                in_correct_slot=True,
                checkin_time=time_str,
            )

        abnormal = self._abnormal_label(is_morning, is_signin, h, m)
        if is_signin:
            raise self._already_checked_in_error(
                f"今日{slot_label}{action_text}{abnormal}（{time_str}），迟到无法补救，跳过",
                in_correct_slot=True,
                checkin_time=time_str,
            )

        can_makeup_early_leave = abnormal == "早退" and self._is_now_in_makeup_window(action_key)
        if can_makeup_early_leave:
            self.last_action_note = f"正常{action_text}"
            logger.info(f"[时间节点] 今日{slot_label}{action_text}{abnormal}（{time_str}），仍在补卡窗口内，按正常{action_text}点击: ({cx},{cy})")
            self._device.click(cx, cy)
            return True

        raise self._already_checked_in_error(
            f"今日{slot_label}{action_text}{abnormal}（{time_str}），已超出补卡窗口，跳过补卡",
            in_correct_slot=True,
            checkin_time=time_str,
        )

    @staticmethod
    def resolve_action_slot(action) -> tuple:
        """解析动作所属时段与按钮文本。
        Returns:
            (is_morning, is_signin, action_text)
        """
        from datetime import datetime, time as _time

        signin_actions = ("morning_signin", "afternoon_signin", "signin")
        action_val = action.value if hasattr(action, 'value') else str(action)
        action_text = "签到" if action_val in signin_actions else "签退"
        now = datetime.now().time()

        if action_val in ("morning_signin", "morning_signout"):
            is_morning = True
        elif action_val in ("afternoon_signin", "afternoon_signout"):
            is_morning = False
        else:
            is_morning = now < _time(12, 30)

        return is_morning, action_val in signin_actions, action_text

    def collect_target_row_right_nodes(self, all_nodes, action, mid_x, is_morning):
        """收集目标行右侧文本与可点击状态。"""
        _, _, action_text = self.resolve_action_slot(action)

        row_labels = []
        for n in all_nodes:
            t = n.get('text', '').strip()
            if t not in ('签到', '签退'):
                continue
            if n.get('clickable', '') != 'false':
                continue
            b = n.get('_bounds')
            if not b:
                continue
            x1, y1, x2, y2 = b
            cx = (x1 + x2) // 2
            if cx >= mid_x:
                continue
            cy = (y1 + y2) // 2
            row_labels.append((t, y1, y2, cy))

        row_labels.sort(key=lambda item: item[3])
        same_action_rows = [row for row in row_labels if row[0] == action_text]
        if same_action_rows:
            target_row = same_action_rows[0] if is_morning else same_action_rows[-1]
        elif row_labels:
            target_row = row_labels[0] if is_morning else row_labels[-1]
        else:
            return [], []

        _, row_y1, row_y2, _ = target_row
        row_height = row_y2 - row_y1
        margin = max(15, row_height * 0.3) if row_height > 0 else 30
        y_lo = row_y1 - margin
        y_hi = row_y2 + margin

        right_texts = []
        right_nodes = []
        for n in all_nodes:
            text = n.get('text', '').strip()
            if not text:
                continue
            b = n.get('_bounds')
            if not b:
                continue
            x1, y1, x2, y2 = b
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            if cx <= mid_x or not (y_lo <= cy <= y_hi):
                continue
            clickable = n.get('clickable', '') == 'true'
            right_texts.append(text)
            right_nodes.append((text, clickable))

        return right_texts, right_nodes

    def verify_target_row_transition(self, action, all_nodes, mid_x) -> bool:
        """通过目标行从按钮变为时间/完成态来判定打卡成功。"""
        _, _, action_text = self.resolve_action_slot(action)
        right_texts, _ = self.collect_target_row_right_nodes(all_nodes, action, mid_x, self.resolve_action_slot(action)[0])
        if not right_texts:
            return False

        success_tokens = ("已打卡", "已签到", "已签退", "打卡完成", "更新成功")
        if any(token in text for token in success_tokens for text in right_texts):
            logger.info(f"检测到目标行完成态文案 {right_texts}，判定打卡成功")
            return True

        time_texts = [text for text in right_texts if TIME_PATTERN.match(text)]
        if time_texts and action_text not in right_texts:
            logger.info(f"检测到目标行时间 {time_texts} 替代按钮，判定打卡成功")
            return True

        return False
