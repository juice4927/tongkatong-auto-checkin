"""
XML 层级解析模块 - 从 uiautomator2 dump hierarchy 结果中解析 UI 节点

职责：
- 解析 uiautomator2 的 hierarchy XML（ET 解析 + 正则回退）
- 提取屏幕中部弹窗区域文本
- 提供 UI 节点解析相关常量与工具函数
"""
import re
import logging
import xml.etree.ElementTree as ET
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── 正则常量 ──────────────────────────────────────────────────────────

# 匹配 <node ...> 标签
NODE_PATTERN = re.compile(r'<node\b([^>]*/?>)', re.DOTALL)
# 提取 bounds 属性值
BOUNDS_PATTERN = re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
# 匹配时间格式 HH:MM
TIME_PATTERN = re.compile(r'^[0-2]\d:[0-5]\d$')
# 提取 clickable 属性
CLICKABLE_PATTERN = re.compile(r'clickable="([^"]*)"')
# 去除 XML 非法字符（uiautomator2 偶发）
INVALID_XML_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


# ── 核心解析函数 ──────────────────────────────────────────────────────

def parse_hierarchy_xml(xml_str: str) -> List[dict]:
    """
    用 xml.etree.ElementTree 解析 uiautomator2 的 hierarchy XML，
    返回节点属性字典列表。解析失败时回退到正则。

    Returns:
        节点字典列表，每个字典包含 text / clickable / bounds / _bounds 等键。
        _bounds 为 (x1, y1, x2, y2) 整数元组，无 bounds 时为 None。
    """
    nodes: List[dict] = []

    # 策略 A：ET 解析
    try:
        cleaned = INVALID_XML_CHARS.sub('', xml_str)
        root = ET.fromstring(cleaned)
        for elem in root.iter('node'):
            node = {}
            for attr in ('text', 'resource-id', 'class', 'package',
                         'content-desc', 'checkable', 'checked',
                         'clickable', 'enabled', 'focusable', 'focused',
                         'scrollable', 'long-clickable', 'password',
                         'selected', 'bounds'):
                node[attr] = elem.get(attr, '')
            bounds_str = node.get('bounds', '')
            m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
            if m:
                node['_bounds'] = tuple(int(m.group(i)) for i in range(1, 5))
            else:
                node['_bounds'] = None
            nodes.append(node)
        return nodes
    except Exception as e:
        logger.debug(f"ET 解析 XML 失败，回退正则: {e}")

    # 策略 B：正则回退
    for n in NODE_PATTERN.findall(xml_str):
        node = {}
        for attr_name in ('text', 'clickable', 'bounds', 'class', 'resource-id',
                          'content-desc', 'enabled', 'focused'):
            m = re.search(rf'{attr_name}="([^"]*)"', n)
            node[attr_name] = m.group(1) if m else ''
        m = BOUNDS_PATTERN.search(n)
        if m:
            node['_bounds'] = tuple(int(m.group(i)) for i in range(1, 5))
        else:
            node['_bounds'] = None
        nodes.append(node)
    return nodes


def extract_center_dialog_texts(xml: str, screen_w: int, screen_h: int) -> List[str]:
    """
    提取屏幕中部弹窗区域文本，避免把页面其他区域文案误识别为结果弹窗。

    Args:
        xml: uiautomator2 dump hierarchy 原始 XML
        screen_w: 屏幕宽度像素
        screen_h: 屏幕高度像素

    Returns:
        弹窗区域内的文本列表
    """
    texts: List[str] = []
    for node in parse_hierarchy_xml(xml):
        text = node.get('text', '').strip()
        bounds = node.get('_bounds')
        if not text or not bounds:
            continue
        x1, y1, x2, y2 = bounds
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        if screen_w * 0.15 <= cx <= screen_w * 0.85 and screen_h * 0.2 <= cy <= screen_h * 0.8:
            texts.append(text)
    return texts
