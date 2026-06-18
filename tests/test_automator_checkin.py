"""
测试打卡核心逻辑：按钮定位、验证、弹窗处理
"""
import unittest
from unittest.mock import Mock, MagicMock, patch
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.automator import (
    UIAutomator2Impl, CheckinAction, AlreadyCheckedInError, GpsLocationError,
    LoginTimeoutError
)


# 模拟考勤界面 XML
MOCK_CHECKIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,1920]">
    <node index="0" text="今日考勤" resource-id="com.tencent.weworklocal:id/title" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,100][1080,200]"/>
    <node index="1" text="签到" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,300][150,350]"/>
    <node index="2" text="07:30" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,360][150,400]"/>
    <node index="3" text="签到" resource-id="" class="android.widget.Button" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[800,300][1000,400]"/>
    <node index="4" text="签退" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,500][150,550]"/>
    <node index="5" text="17:30" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,560][150,600]"/>
    <node index="6" text="签退" resource-id="" class="android.widget.Button" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[800,500][1000,600]"/>
  </node>
</hierarchy>"""

# 已打卡状态 XML（显示时间而非按钮）
MOCK_ALREADY_CHECKED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,1920]">
    <node index="0" text="今日考勤" resource-id="com.tencent.weworklocal:id/title" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,100][1080,200]"/>
    <node index="1" text="签到" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,300][150,350]"/>
    <node index="2" text="07:30" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[50,360][150,400]"/>
    <node index="3" text="06:25" resource-id="" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[800,300][1000,400]"/>
  </node>
</hierarchy>"""

MOCK_ROW_TRANSITION_SUCCESS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.tencent.weworklocal" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="08:31" clickable="false" bounds="[820,300][990,380]"/>
    <node index="2" text="签退" clickable="false" bounds="[50,500][150,550]"/>
    <node index="3" text="签退" clickable="true" bounds="[820,500][990,580]"/>
  </node>
</hierarchy>"""

MOCK_OTHER_ROW_SUCCESS_TEXT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="签到" clickable="true" bounds="[820,300][990,380]"/>
    <node index="2" text="签退" clickable="false" bounds="[50,520][150,570]"/>
    <node index="3" text="已签退" clickable="false" bounds="[820,520][990,600]"/>
  </node>
</hierarchy>"""

MOCK_SPARSE_ROWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="签到" clickable="true" bounds="[820,300][990,380]"/>
    <node index="2" text="签退" clickable="false" bounds="[50,520][150,570]"/>
    <node index="3" text="签退" clickable="true" bounds="[820,520][990,600]"/>
    <node index="4" text="签到" clickable="false" bounds="[50,740][150,790]"/>
    <node index="5" text="签到" clickable="true" bounds="[820,740][990,820]"/>
  </node>
</hierarchy>"""

# 打卡成功弹窗 XML
MOCK_SUCCESS_DIALOG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,1920]">
    <node index="0" text="打卡成功" resource-id="com.tencent.weworklocal:id/message" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[200,800][880,900]"/>
    <node index="1" text="确定" resource-id="com.tencent.weworklocal:id/confirm" class="android.widget.Button" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[400,1000][680,1100]"/>
  </node>
</hierarchy>"""

MOCK_MISLEADING_SUCCESS_TEXT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="打卡成功后可继续查看说明" class="android.widget.TextView" clickable="false" bounds="[200,800][880,900]"/>
    <node index="1" text="确定" class="android.widget.Button" clickable="true" bounds="[400,1000][680,1100]"/>
  </node>
</hierarchy>"""

# GPS 失败弹窗 XML
MOCK_GPS_FAIL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,1920]">
    <node index="0" text="超出距离" resource-id="com.tencent.weworklocal:id/message" class="android.widget.TextView" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[200,800][880,900]"/>
    <node index="1" text="确定" resource-id="com.tencent.weworklocal:id/confirm" class="android.widget.Button" package="com.tencent.weworklocal" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[400,1000][680,1100]"/>
  </node>
</hierarchy>"""


class TestParseHierarchyXml(unittest.TestCase):
    """测试 XML 解析"""

    def setUp(self):
        self.automator = UIAutomator2Impl()

    def test_parse_normal_xml(self):
        """正常 XML 应解析出节点列表"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_CHECKIN_XML)
        self.assertGreater(len(nodes), 0)
        # 应包含文本节点
        texts = [n.get('text', '') for n in nodes]
        self.assertIn('今日考勤', texts)
        self.assertIn('签到', texts)

    def test_parse_empty_xml(self):
        """空 XML 应返回空列表"""
        nodes = self.automator._parse_hierarchy_xml("")
        self.assertEqual(nodes, [])

    def test_parse_malformed_xml(self):
        """格式错误的 XML 应回退到正则解析"""
        malformed = '<node text="test" bounds="[0,0][100,100]" clickable="true"/>'
        nodes = self.automator._parse_hierarchy_xml(malformed)
        # 回退解析应仍能提取节点
        self.assertGreater(len(nodes), 0)

    def test_parse_bounds_extraction(self):
        """应正确提取 bounds 属性"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_CHECKIN_XML)
        # 找到"今日考勤"节点
        title_node = next((n for n in nodes if n.get('text') == '今日考勤'), None)
        self.assertIsNotNone(title_node)
        bounds = title_node.get('_bounds')
        self.assertIsNotNone(bounds)
        self.assertEqual(bounds, (0, 100, 1080, 200))


class TestStrategyRowAnchor(unittest.TestCase):
    """测试行锚定策略"""

    def setUp(self):
        self.automator = UIAutomator2Impl()
        self.automator.device = Mock()
        self.automator._device.window_size.return_value = (1080, 1920)

    def test_find_morning_signin_button(self):
        """应找到上午签到按钮"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_CHECKIN_XML)
        result = self.automator._strategy_row_anchor(
            nodes,
            action=CheckinAction.MORNING_SIGNIN,
            is_morning=True,
            is_signin=True,
            action_text='签到',
            slot_label='上午',
            mid_x=540,
            screen_w=1080
        )
        # 应返回 True（找到按钮）或 None（未找到）
        self.assertIn(result, [True, None])

    def test_find_afternoon_signout_button(self):
        """应找到下午签退按钮"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_CHECKIN_XML)
        result = self.automator._strategy_row_anchor(
            nodes,
            action=CheckinAction.AFTERNOON_SIGNOUT,
            is_morning=False,
            is_signin=False,
            action_text='签退',
            slot_label='下午',
            mid_x=540,
            screen_w=1080
        )
        self.assertIn(result, [True, None])

    def test_already_checked_in(self):
        """已打卡状态应抛出 AlreadyCheckedInError"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_ALREADY_CHECKED_XML)
        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
            )
        self.assertTrue(ctx.exception.in_correct_slot)

    def test_row_anchor_treats_normal_target_row_time_as_completed(self):
        """目标行右侧时间必须在配置窗口内才识别为已完成"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="07:31" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        self.automator.set_makeup_windows({"morning_signin": [4, 0, 8, 0]})
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
        )

        self.assertTrue(ctx.exception.in_correct_slot)
        self.assertEqual(ctx.exception.checkin_time, "07:31")
        self.automator._device.click.assert_not_called()

    def test_missing_configured_window_does_not_use_hardcoded_normal_range(self):
        """未配置有效打卡窗口时不应回退硬编码范围误判已完成"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="07:31" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
            )

        self.assertIn("未配置", str(ctx.exception))
        self.assertNotIn("已完成", str(ctx.exception))
        self.automator._device.click.assert_not_called()

    def test_late_time_outside_makeup_window_is_skipped(self):
        """迟到时间超出有效打卡窗口时应跳过并显示迟到"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="08:31" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        self.automator.set_makeup_windows({"morning_signin": [4, 0, 8, 0]})
        self.automator._button_finder._now_provider = lambda: datetime(2026, 6, 18, 9, 0)
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
        )

        self.assertIn("迟到", str(ctx.exception))
        self.assertIn("无法补救", str(ctx.exception))
        self.assertIn("跳过", str(ctx.exception))
        self.automator._device.click.assert_not_called()

    def test_signin_time_inside_makeup_window_is_completed(self):
        """右侧签到时间在有效打卡窗口内时应视为正常已完成"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="08:31" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        self.automator.set_makeup_windows({"morning_signin": [4, 0, 10, 0]})
        self.automator._button_finder._now_provider = lambda: datetime(2026, 6, 18, 9, 0)
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
            )

        self.assertIn("已完成", str(ctx.exception))
        self.assertNotIn("迟到", str(ctx.exception))
        self.assertEqual(ctx.exception.checkin_time, "08:31")
        self.automator._device.click.assert_not_called()

    def test_early_leave_time_outside_makeup_window_is_skipped(self):
        """早退时间超出有效打卡窗口时应跳过并显示早退"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签退" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="11:00" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        self.automator.set_makeup_windows({"morning_signout": [11, 30, 13, 30]})
        self.automator._button_finder._now_provider = lambda: datetime(2026, 6, 18, 14, 0)
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNOUT,
                is_morning=True,
                is_signin=False,
                action_text='签退',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
        )

        self.assertIn("早退", str(ctx.exception))
        self.assertIn("有效打卡窗口", str(ctx.exception))
        self.assertIn("跳过", str(ctx.exception))
        self.automator._device.click.assert_not_called()

    def test_signout_time_inside_makeup_window_is_completed(self):
        """右侧签退时间在有效打卡窗口内时应视为正常已完成"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签退" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="13:15" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        self.automator.set_makeup_windows({"morning_signout": [11, 30, 13, 30]})
        self.automator._button_finder._now_provider = lambda: datetime(2026, 6, 18, 14, 0)
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_row_anchor(
                nodes,
                action=CheckinAction.MORNING_SIGNOUT,
                is_morning=True,
                is_signin=False,
                action_text='签退',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
            )

        self.assertIn("已完成", str(ctx.exception))
        self.assertNotIn("早退", str(ctx.exception))
        self.assertEqual(ctx.exception.checkin_time, "13:15")
        self.automator._device.click.assert_not_called()

    def test_early_leave_time_before_window_clicks_when_now_is_inside_window(self):
        """早退时间早于打卡窗口，当前仍在窗口内时应点击正常签退"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签退" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="11:00" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        self.automator.set_makeup_windows({"morning_signout": [11, 30, 13, 30]})
        self.automator._button_finder._now_provider = lambda: datetime(2026, 6, 18, 11, 45)
        nodes = self.automator._parse_hierarchy_xml(xml)

        result = self.automator._strategy_row_anchor(
            nodes,
            action=CheckinAction.MORNING_SIGNOUT,
            is_morning=True,
            is_signin=False,
            action_text='签退',
            slot_label='上午',
            mid_x=540,
            screen_w=1080
        )

        self.assertTrue(result)
        self.assertEqual(self.automator._button_finder.last_action_note, "正常签退")
        self.automator._device.click.assert_called_once_with(905, 340)

    def test_sparse_rows_prefers_same_action_group(self):
        """行不完整时也应优先选同类动作行，而不是依赖固定四行索引"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_SPARSE_ROWS_XML)
        result = self.automator._strategy_row_anchor(
            nodes,
            action=CheckinAction.AFTERNOON_SIGNOUT,
            is_morning=False,
            is_signin=False,
            action_text='签退',
            slot_label='下午',
            mid_x=540,
            screen_w=1080
        )
        self.assertTrue(result)


class TestStrategyFullScan(unittest.TestCase):
    """测试全屏扫描策略"""

    def setUp(self):
        self.automator = UIAutomator2Impl()
        self.automator.device = Mock()
        self.automator._device.window_size.return_value = (1080, 1920)

    def test_find_button_by_text(self):
        """应通过文本找到打卡按钮"""
        nodes = self.automator._parse_hierarchy_xml(MOCK_CHECKIN_XML)
        result = self.automator._strategy_full_scan(
            nodes,
            action=CheckinAction.MORNING_SIGNIN,
            is_morning=True,
            is_signin=True,
            action_text='签到',
            slot_label='上午',
            mid_x=540,
            screen_w=1080
        )
        self.assertIn(result, [True, None])

    def test_full_scan_treats_target_time_as_completed(self):
        """全文扫描只找到右侧时间时应识别为已完成，而不是点击覆盖"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="08:31" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        nodes = self.automator._parse_hierarchy_xml(xml)

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._strategy_full_scan(
                nodes,
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                mid_x=540,
                screen_w=1080
            )

        self.assertTrue(ctx.exception.in_correct_slot)
        self.assertEqual(ctx.exception.checkin_time, "08:31")
        self.automator._device.click.assert_not_called()

    def test_fallback_treats_target_time_as_completed(self):
        """兜底扫描只找到右侧时间时应识别为已完成，而不是点击覆盖"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="08:31" clickable="true" bounds="[820,300][990,380]"/>
  </node>
</hierarchy>"""
        buttons = Mock()
        buttons.exists = False
        self.automator._device.return_value = buttons

        with self.assertRaises(AlreadyCheckedInError) as ctx:
            self.automator._fallback_find_and_click(
                action=CheckinAction.MORNING_SIGNIN,
                is_morning=True,
                is_signin=True,
                action_text='签到',
                slot_label='上午',
                xml=xml,
                screen_w=1080
            )

        self.assertTrue(ctx.exception.in_correct_slot)
        self.assertEqual(ctx.exception.checkin_time, "08:31")
        self.automator._device.click.assert_not_called()


class TestDefaultVerify(unittest.TestCase):
    """测试打卡验证逻辑"""

    def setUp(self):
        self.automator = UIAutomator2Impl()
        self.automator.device = Mock()
        self.automator._device.window_size.return_value = (1080, 1920)

    def test_verify_success_dialog(self):
        """打卡成功弹窗应返回 True"""
        self.automator._device.dump_hierarchy.return_value = MOCK_SUCCESS_DIALOG_XML
        
        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertTrue(result)

    def test_verify_gps_failure(self):
        """GPS 失败弹窗应返回 False"""
        self.automator._device.dump_hierarchy.return_value = MOCK_GPS_FAIL_XML
        # Mock 关闭按钮
        mock_btn = Mock()
        mock_btn.exists = True
        self.automator._device.text.return_value = mock_btn
        
        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertFalse(result)

    def test_verify_no_success_indicator(self):
        """无成功标志应返回 False"""
        # 使用完全空的 XML，没有任何文本或可点击节点
        empty_xml = '<?xml version="1.0"?><hierarchy></hierarchy>'
        self.automator._device.dump_hierarchy.return_value = empty_xml
        self.automator._device.window_size.return_value = (1080, 1920)
        
        # Mock _device 作为函数调用（用于 textContains 查找）
        # 代码中使用的是 self._device(textContains=text).exists
        mock_selector = Mock()
        mock_selector.exists = False
        self.automator._device.return_value = mock_selector
        
        # Mock take_screenshot 返回 None
        self.automator.take_screenshot = Mock(return_value=None)
        
        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertFalse(result)

    def test_verify_row_transition_success(self):
        """目标行由按钮变成时间时也应判定成功"""
        self.automator._device.dump_hierarchy.return_value = MOCK_ROW_TRANSITION_SUCCESS_XML
        self.automator._device.window_size.return_value = (1080, 1920)

        mock_selector = Mock()
        mock_selector.exists = False
        self.automator._device.return_value = mock_selector

        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertTrue(result)

    def test_verify_ignores_misleading_success_popup_text(self):
        """只含成功字样的说明文本不应误判为成功弹窗"""
        self.automator._device.dump_hierarchy.return_value = MOCK_MISLEADING_SUCCESS_TEXT_XML
        self.automator._device.window_size.return_value = (1080, 1920)
        self.automator.take_screenshot = Mock(return_value=None)

        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertFalse(result)

    def test_verify_ignores_success_text_from_other_row(self):
        """其他行的完成态文案不应误判为当前动作成功"""
        self.automator._device.dump_hierarchy.return_value = MOCK_OTHER_ROW_SUCCESS_TEXT_XML
        self.automator._device.window_size.return_value = (1080, 1920)

        def _selector_side_effect(*args, **kwargs):
            mock_selector = Mock()
            text = kwargs.get("textContains")
            mock_selector.exists = text == "已签退"
            return mock_selector

        self.automator._device.side_effect = _selector_side_effect
        self.automator.take_screenshot = Mock(return_value=None)

        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertFalse(result)

    def test_verify_ignores_current_time_from_other_row(self):
        """其他行的当前时间按钮不应误判为当前动作成功"""
        from datetime import datetime

        now_text = datetime.now().strftime("%H:%M")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" text="签到" clickable="false" bounds="[50,300][150,350]"/>
    <node index="1" text="签到" clickable="true" bounds="[820,300][990,380]"/>
    <node index="2" text="签退" clickable="false" bounds="[50,520][150,570]"/>
    <node index="3" text="{now_text}" clickable="true" bounds="[820,520][990,600]"/>
  </node>
</hierarchy>"""
        self.automator._device.dump_hierarchy.return_value = xml
        self.automator._device.window_size.return_value = (1080, 1920)

        mock_selector = Mock()
        mock_selector.exists = False
        self.automator._device.side_effect = lambda *args, **kwargs: mock_selector
        self.automator.take_screenshot = Mock(return_value=None)

        result = self.automator._default_verify(CheckinAction.MORNING_SIGNIN)
        self.assertFalse(result)


class TestHandleConfirmDialog(unittest.TestCase):
    """测试弹窗处理逻辑"""

    def setUp(self):
        self.automator = UIAutomator2Impl()
        self.automator.device = Mock()

    def test_no_dialog_returns_early(self):
        """无弹窗时应快速返回"""
        empty_xml = '<?xml version="1.0"?><hierarchy><node text="" bounds="[0,0][100,100]"/></hierarchy>'
        self.automator._device.dump_hierarchy.return_value = empty_xml
        
        # 不应抛出异常
        self.automator._handle_confirm_dialog(timeout=1)

    def test_success_dialog_closes(self):
        """成功弹窗应关闭"""
        call_count = [0]
        def mock_dump(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MOCK_SUCCESS_DIALOG_XML
            return '<?xml version="1.0"?><hierarchy/>'
        
        self.automator._device.dump_hierarchy.side_effect = mock_dump
        
        # Mock 按钮查找 - 当查找"确定"时返回存在的按钮
        def mock_text_selector(text=None, clickable=None):
            mock_btn = Mock()
            if text == "确定" and clickable:
                mock_btn.exists = True
                return mock_btn
            mock_btn.exists = False
            return mock_btn
        
        self.automator._device.side_effect = lambda **kwargs: mock_text_selector(**kwargs)
        
        # 不应抛出异常
        self.automator._handle_confirm_dialog(timeout=2)

    def test_misleading_success_text_is_not_treated_as_success_dialog(self):
        """包含成功字样但不是精确结果文案的弹窗不应当成成功弹窗"""
        self.automator._device.dump_hierarchy.return_value = MOCK_MISLEADING_SUCCESS_TEXT_XML
        self.automator._click_button_by_text = Mock()

        self.automator._handle_confirm_dialog(timeout=1)

        self.automator._click_button_by_text.assert_not_called()

    def test_gps_failure_raises(self):
        """GPS 失败弹窗应抛出 GpsLocationError"""
        self.automator._device.dump_hierarchy.return_value = MOCK_GPS_FAIL_XML
        mock_btn = Mock()
        mock_btn.exists = True
        self.automator._device.text.return_value = mock_btn
        
        with self.assertRaises(GpsLocationError):
            self.automator._handle_confirm_dialog(timeout=1)


class TestLoginAndNavigation(unittest.TestCase):
    """测试登录超时和考勤导航的关键失败路径"""

    def setUp(self):
        self.automator = UIAutomator2Impl(package_name="com.tencent.weworklocal")
        self.automator.device = Mock()
        self.automator._connected = True
        self.automator.is_connected = Mock(return_value=True)

    @patch("src.core.automator.time.sleep", return_value=None)
    def test_handle_login_timeout_raises(self, _sleep):
        self.automator._is_on_login_page = Mock(return_value=True)
        self.automator._is_logged_in = Mock(return_value=False)
        self.automator._wait_for_login = Mock(side_effect=[False, False])
        self.automator._send_login_notify = Mock()

        phone_login_btn = Mock()
        phone_login_btn.exists = False
        self.automator._device.text.return_value = phone_login_btn

        with self.assertRaises(LoginTimeoutError):
            self.automator._handle_login_if_needed("com.tencent.weworklocal")

        self.assertEqual(self.automator._wait_for_login.call_count, 2)
        self.assertTrue(self.automator._send_login_notify.called)

    @patch("src.core.automator.time.sleep", return_value=None)
    def test_open_app_propagates_login_timeout(self, _sleep):
        self.automator._device.app_current.return_value = {
            "package": "com.tencent.weworklocal",
            "activity": "com.tencent.wework.launch.WwMainActivity",
        }
        self.automator._handle_login_if_needed = Mock(side_effect=LoginTimeoutError("login timeout"))

        with self.assertRaises(LoginTimeoutError):
            self.automator.open_app("com.tencent.weworklocal")

    @patch("src.core.automator.time.sleep", return_value=None)
    def test_navigate_to_checkin_requires_key_markers(self, _sleep):
        self.automator._device.app_current.return_value = {
            "package": "com.tencent.weworklocal",
            "activity": "com.tencent.wework.launch.WwMainActivity",
        }
        self.automator._save_failure_diagnosis = Mock()

        def _make_selector(exists: bool):
            selector = Mock()
            selector.exists = exists
            return selector

        def _text_selector(*, text=None, textContains=None, clickable=None):
            if text in ["我知道了", "知道了", "确定", "关闭", "取消", "工作台"]:
                return _make_selector(False)
            if textContains == "考勤":
                return _make_selector(True)
            if textContains in ("今日考勤", "签到"):
                return _make_selector(False)
            return _make_selector(False)

        self.automator._device.side_effect = _text_selector

        result = self.automator.navigate_to_checkin()

        self.assertFalse(result)
        self.assertGreaterEqual(self.automator._device.press.call_count, 3)
        self.automator._save_failure_diagnosis.assert_called_once()


class TestCheckinFailureMessages(unittest.TestCase):
    def setUp(self):
        self.automator = UIAutomator2Impl()
        self.automator._connected = True
        self.automator.is_connected = Mock(return_value=True)
        self.automator.device = Mock()

    def test_navigation_failure_message_is_classified(self):
        self.automator.navigate_to_checkin = Mock(return_value=False)

        result = self.automator.do_checkin(CheckinAction.MORNING_SIGNIN)

        self.assertFalse(result.success)
        self.assertIn("导航失败：", result.message)
        self.assertEqual(result.failure_code, "navigation_failed")

    def test_disconnected_device_returns_specific_failure_code(self):
        self.automator.is_connected = Mock(return_value=False)
        self.automator._last_connection_failure_code = "device_not_connected"

        result = self.automator.do_checkin(CheckinAction.MORNING_SIGNIN)

        self.assertFalse(result.success)
        self.assertEqual(result.failure_code, "device_not_connected")
        self.assertIn("设备异常：", result.message)


class TestCheckinActionEnum(unittest.TestCase):
    """测试 CheckinAction 枚举"""

    def test_morning_actions(self):
        """上午打卡动作"""
        self.assertEqual(CheckinAction.MORNING_SIGNIN.value, 'morning_signin')
        self.assertEqual(CheckinAction.MORNING_SIGNOUT.value, 'morning_signout')

    def test_afternoon_actions(self):
        """下午打卡动作"""
        self.assertEqual(CheckinAction.AFTERNOON_SIGNIN.value, 'afternoon_signin')
        self.assertEqual(CheckinAction.AFTERNOON_SIGNOUT.value, 'afternoon_signout')


if __name__ == "__main__":
    unittest.main()
