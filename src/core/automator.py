"""
自动化执行模块 - 基于 uiautomator2
"""
import time
import threading
import logging
from typing import Optional, Callable, Dict, List, Tuple

import uiautomator2 as u2

from .xml_parser import (
    parse_hierarchy_xml,
    extract_center_dialog_texts,
    TIME_PATTERN,
)
from .navigator import CheckinNavigator
from .button_finder import ButtonFinder
from .checkin_verifier import CheckinVerifier
from src.core.config import get_runtime_root
from src.utils.adb_helper import ADBHelper
from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class CheckinAction(Enum):
    """打卡动作类型"""
    SIGNIN = "signin"           # 兼容旧代码，运行时用系统时间判断上午/下午
    SIGNOUT = "signout"         # 兼容旧代码
    MORNING_SIGNIN = "morning_signin"
    MORNING_SIGNOUT = "morning_signout"
    AFTERNOON_SIGNIN = "afternoon_signin"
    AFTERNOON_SIGNOUT = "afternoon_signout"


@dataclass
class CheckinResult:
    """打卡结果"""
    success: bool
    action: CheckinAction
    message: str
    timestamp: str
    screenshot_path: Optional[str] = None
    failure_code: str = ""
    recovery_action: str = ""


class DeviceConnectionError(Exception):
    """设备连接错误"""
    def __init__(self, message: str, failure_code: str = "device_connection_failed"):
        super().__init__(message)
        self.failure_code = failure_code


class AppNotFoundError(Exception):
    """应用未找到错误"""
    pass


class LoginTimeoutError(Exception):
    """应用登录超时"""
    pass


class AlreadyCheckedInError(Exception):
    """已经打卡过了"""
    def __init__(self, message: str, in_correct_slot: bool = True, checkin_time: str = None):
        super().__init__(message)
        self.in_correct_slot = in_correct_slot
        self.checkin_time = checkin_time


class GpsLocationError(Exception):
    """GPS 定位失败"""
    pass


class DeviceSession:
    """设备会话封装（含 TTL 管理）"""
    def __init__(self, device, ttl_seconds: int = 300):
        self.device = device
        self.created_at = time.time()
        self.ttl = ttl_seconds

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    def is_alive(self) -> bool:
        if self.is_expired():
            return False
        try:
            self.device.device_info
            return True
        except Exception as e:
            logger.debug(f"设备会话存活检查失败: {e}")
            return False


class AutomatorBase(ABC):
    """自动化执行器基类"""

    @abstractmethod
    def connect(self) -> bool:
        """连接设备"""
        pass

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        pass

    @abstractmethod
    def open_app(self, package_name: str) -> bool:
        """打开应用"""
        pass

    @abstractmethod
    def close_app(self, package_name: str) -> bool:
        """关闭应用"""
        pass

    @abstractmethod
    def do_checkin(self, action: CheckinAction) -> CheckinResult:
        """执行打卡"""
        pass

    @abstractmethod
    def take_screenshot(self, save_path: str = None) -> Optional[str]:
        """截图"""
        pass


class UIAutomator2Impl(AutomatorBase):
    """uiautomator2 实现"""

    # 全局会话缓存（类级别，跨实例复用）
    _session_cache: Dict[str, DeviceSession] = {}
    _session_cache_lock = threading.Lock()

    # 已打卡时间范围判断：(is_morning, is_signin) -> lambda
    # 定义位于 button_finder.py 模块级常量 ALREADY_CHECKED_IN_RANGES
    # 此处保留类属性引用以兼容 test_automator_core.py 的测试
    from .button_finder import ALREADY_CHECKED_IN_RANGES as _ALREADY_CHECKED_IN_RANGES

    # XML 解析正则（兜底用）——从 xml_parser 模块导入
    # _NODE_PATTERN / _BOUNDS_PATTERN / _TIME_PATTERN / _CLICKABLE_PATTERN / _INVALID_XML_CHARS
    # 现在位于 src.core.xml_parser 中，此处保留别名以兼容外部测试引用
    _TIME_PATTERN = TIME_PATTERN

    # 图像识别缓存
    _image_recognition_available = None

    _DEFAULT_PACKAGE = "com.tencent.weworklocal"

    def __init__(self, host: str = "127.0.0.1", port: int = 5555, adb_path: str = None,
                 session_ttl: int = 300, package_name: str = ""):
        self.host = host
        self.port = port
        self.adb_path = adb_path
        self.session_ttl = session_ttl
        self._package_name = package_name or self._DEFAULT_PACKAGE
        self._device = None
        self._connected = False
        self._device_addr = f"{host}:{port}"
        self._last_connection_failure_code = ""
        self._last_navigation_recovery_action = ""

        # 创建子模块实例（device 后续在 connect() 中设置或委托时同步）
        self._navigator = CheckinNavigator(
            device=None,
            package_name=self._package_name,
            open_app_cb=self.open_app,
            close_app_cb=self.close_app,
        )
        self._button_finder = ButtonFinder(
            device=None,
            already_checked_in_error_class=AlreadyCheckedInError,
        )
        self._verifier = CheckinVerifier(
            device=None,
            package_name=self._package_name,
            compute_result_message_fn=self._compose_result_message,
            save_diagnosis_fn=self._save_failure_diagnosis,
        )

    def _sync_submodules(self):
        """同步当前 device 到所有子模块。"""
        for mod in (self._navigator, self._button_finder, self._verifier):
            mod._device = self._device
        if hasattr(self._verifier, '_dialog_handler'):
            self._verifier._dialog_handler._device = self._device
        if hasattr(self._verifier, '_button_finder'):
            self._verifier._button_finder._device = self._device

    @property
    def device(self):
        """获取设备实例"""
        return self._device

    @device.setter
    def device(self, value):
        """设置设备实例（自动同步到子模块）"""
        self._device = value
        self._sync_submodules()

    def _get_cached_session(self) -> Optional[DeviceSession]:
        """获取缓存的设备会话（如果未过期且存活）"""
        with self._session_cache_lock:
            session = self._session_cache.get(self._device_addr)
            if session and session.is_alive():
                logger.debug("复用缓存的设备会话: %s", self._device_addr)
                return session
            # 清除过期/无效缓存
            if session:
                logger.debug("清除过期的设备会话缓存: %s", self._device_addr)
                self._session_cache.pop(self._device_addr, None)
            return None

    def _clear_cache(self):
        """清除当前设备的会话缓存"""
        with self._session_cache_lock:
            self._session_cache.pop(self._device_addr, None)
        logger.debug("已清除设备会话缓存: %s", self._device_addr)

    @classmethod
    def clear_all_cache(cls):
        """清除所有设备会话缓存（应用退出时调用）"""
        with cls._session_cache_lock:
            count = len(cls._session_cache)
            cls._session_cache.clear()
        if count > 0:
            logger.info(f"已清除 {count} 个设备会话缓存")

    @staticmethod
    def _parse_hierarchy_xml(xml_str: str) -> List[dict]:
        """委托给 xml_parser.parse_hierarchy_xml（保留旧方法名以兼容外部引用）"""
        return parse_hierarchy_xml(xml_str)

    def _wait_for_ui_ready(self, timeout: float = 5.0, min_nodes: int = 10) -> bool:
        """
        轮询等待 UI 渲染完成：
        - 无"加载中"文本
        - 节点数 > min_nodes
        - 连续两次 dump 节点数变化 < 5%
        """
        start = time.time()
        prev_count = 0
        stable_count = 0
        while time.time() - start < timeout:
            try:
                xml = self._device.dump_hierarchy(compressed=True)
                if "加载中" in xml:
                    time.sleep(0.3)
                    continue
                count = xml.count('<node')
                if count < min_nodes:
                    time.sleep(0.3)
                    continue
                if prev_count > 0:
                    diff_ratio = abs(count - prev_count) / max(prev_count, 1)
                    if diff_ratio < 0.05:
                        stable_count += 1
                        if stable_count >= 2:
                            return True
                    else:
                        stable_count = 0
                prev_count = count
            except Exception as e:
                logger.debug(f"等待UI稳定时dump失败: {e}")
            time.sleep(0.3)
        return prev_count >= min_nodes

    def _save_failure_diagnosis(self, action: 'CheckinAction', reason: str):
        """失败时保存截图和 XML dump 供排查"""
        try:
            from datetime import datetime
            from pathlib import Path

            diag_dir = get_runtime_root() / "logs" / "diagnosis"
            diag_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            action_name = action.value

            screenshot_path = diag_dir / f"fail_{action_name}_{ts}.png"
            try:
                self._device.screenshot(str(screenshot_path))
                logger.info(f"诊断截图已保存: {screenshot_path}")
            except Exception as e:
                logger.warning(f"诊断截图保存失败: {e}")

            xml_path = diag_dir / f"fail_{action_name}_{ts}.xml"
            summary_lines = [
                f"timestamp={ts}",
                f"action={action_name}",
                f"reason={reason}",
            ]
            try:
                xml = self._device.dump_hierarchy(compressed=False)
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(xml)
                logger.info(f"诊断 XML 已保存: {xml_path}")

                nodes = parse_hierarchy_xml(xml)
                clickable_nodes = [n for n in nodes if n.get('clickable') == 'true' and n.get('text', '').strip()]
                if clickable_nodes:
                    texts = [(n['text'], n.get('class', '')) for n in clickable_nodes[:30]]
                    logger.info(f"当前界面可点击节点: {texts}")
                    summary_lines.append("clickable_nodes=" + repr(texts))

                try:
                    screen_w, _ = self._device.window_size()
                except Exception:
                    screen_w = 1080
                is_morning, _, _ = self._button_finder.resolve_action_slot(action)
                right_texts, right_nodes = self._button_finder.collect_target_row_right_nodes(
                    nodes, action, screen_w * 0.5, is_morning
                )
                summary_lines.append("target_row_right_texts=" + repr(right_texts))
                summary_lines.append("target_row_right_nodes=" + repr(right_nodes))
            except Exception as e:
                logger.warning(f"诊断 XML 保存失败: {e}")
                summary_lines.append(f"xml_error={e}")

            summary_path = diag_dir / f"fail_{action_name}_{ts}_summary.txt"
            try:
                summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
                logger.info(f"诊断摘要已保存: {summary_path}")
            except Exception as e:
                logger.warning(f"诊断摘要保存失败: {e}")

            for old in diag_dir.glob("fail_*"):
                if old.stat().st_mtime < time.time() - 3 * 86400:
                    try:
                        old.unlink()
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"保存诊断信息失败: {e}", exc_info=True)

    def connect(self) -> bool:
        """连接到MuMu模拟器（优先复用缓存）"""
        # 1. 尝试复用缓存
        cached = self._get_cached_session()
        if cached:
            self.device = cached.device
            self._connected = True
            return True

        # 2. 缓存无效，重新连接
        device_addr = self._device_addr
        adb = self.adb_path or "adb"
        logger.info(f"正在连接设备: {device_addr} (ADB={adb})")

        # 2a. 确保 ADB server 在运行（先 kill 掉可能残留的僵尸 server）
        self._adb_restart(adb)

        # 2b. adb connect 连接设备（多次重试）
        ok, detail = self._adb_connect_with_retry(adb)
        if not ok:
            # 连接失败 — 看看 adb devices 里有什么，给出诊断信息
            diag = self._adb_diagnose(adb)
            raise DeviceConnectionError(
                f"adb connect {device_addr} 失败\n原因: {detail}\n{diag}",
                failure_code="adb_connect_failed",
            )

        # 2c. 验证设备在 adb devices 中状态为 "device"
        if not self._adb_verify_device_ready(adb, device_addr):
            raise DeviceConnectionError(
                f"设备 {device_addr} 在 adb devices 中状态异常（非 'device'）",
                failure_code="device_not_ready",
            )

        # 2d. 用 u2 建立 uiautomator2 会话
        device = self._u2_connect_with_fallback(adb, device_addr)

        # 3. 验证连接
        try:
            device_info = device.device_info
            logger.info(f"设备连接成功: {device_info.get('brand', 'Unknown')} {device_info.get('model', 'Unknown')}")
        except Exception as e:
            raise DeviceConnectionError(
                f"设备已通过 adb 连接，但 uiautomator2 会话验证失败: {e}",
                failure_code="u2_session_failed",
            )

        # 4. 缓存会话
        with self._session_cache_lock:
            self._session_cache[self._device_addr] = DeviceSession(device, self.session_ttl)
        self.device = device
        self._connected = True
        self._last_connection_failure_code = ""
        return True

    # ── ADB / u2 连接辅助方法 ──────────────────────────────────────────

    def _adb_restart(self, adb: str) -> None:
        """重启 ADB server，清理可能残留的僵尸进程"""
        helper = ADBHelper(adb)
        logger.info("重启 ADB server...")
        try:
            helper._run_command(["kill-server"], timeout=5)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            ok, out = helper._run_command(["start-server"], timeout=10)
            if ok:
                logger.info("ADB server 已启动")
            else:
                logger.warning(f"ADB server 启动警告: {out}")
        except Exception as e:
            logger.warning(f"ADB server 启动异常（可能已在运行）: {e}")
        time.sleep(1)

    def _adb_connect_with_retry(self, adb: str) -> Tuple[bool, str]:
        """连接设备，失败则重启 ADB server 后重试一次"""
        device_addr = self._device_addr
        helper = ADBHelper(adb)

        for attempt in range(1, 4):
            ok, detail = helper.connect(self.host, self.port)
            if ok:
                logger.info(f"adb connect 成功 (第{attempt}次尝试)")
                return True, detail
            logger.warning(f"adb connect 第{attempt}次失败: {detail}")
            if attempt < 3:
                time.sleep(2)
        return False, detail

    def _adb_verify_device_ready(self, adb: str, device_addr: str) -> bool:
        """确认设备在 adb devices 中状态为 'device'"""
        helper = ADBHelper(adb)
        devices = helper.devices()
        for d in devices:
            if d.get("serial") == device_addr and d.get("status") == "device":
                return True
        return False

    def _adb_diagnose(self, adb: str) -> str:
        """收集 ADB 诊断信息"""
        try:
            helper = ADBHelper(adb)
            ver = helper.version() or "unknown"
            devices = helper.devices()
            dev_str = ", ".join(f"{d['serial']}({d['status']})" for d in devices) or "无设备"
            return f"ADB 版本: {ver}\nadb devices: {dev_str}"
        except Exception as e:
            return f"ADB 诊断失败: {e}"

    def _u2_connect_with_fallback(self, adb: str, device_addr: str):
        """
        用 u2 建立连接，失败时尝试多种策略：
        - 策略1: u2.connect_usb(serial) —— 让 u2 自己处理
        - 策略2: u2.connect(addr) —— 字符串地址
        - 策略3: 从 adbutils 拿设备再传给 u2
        """
        last_err = ""

        # 策略1: 直接用字符串地址（最简单，和初始版一样）
        try:
            logger.info(f"u2.connect('{device_addr}') 尝试中...")
            d = u2.connect(device_addr)
            d.info  # 立即触发一次通信，验证会话有效
            logger.info("u2.connect(字符串) 成功")
            return d
        except Exception as e:
            last_err = str(e)
            logger.warning(f"策略1 u2.connect(字符串) 失败: {last_err}")

        # 策略2: u2.connect_usb（可能需要用 serial 而非 addr）
        try:
            logger.info("u2.connect_usb() 尝试中...")
            d = u2.connect_usb(device_addr)
            d.info
            logger.info("u2.connect_usb() 成功")
            return d
        except Exception as e:
            logger.warning(f"策略2 u2.connect_usb() 失败: {e}")

        # 策略3: adbutils 路径（兜底）
        try:
            logger.info("adbutils 路径尝试中...")
            import adbutils
            client = adbutils.AdbClient()
            adb_device = client.device(device_addr)
            d = u2.connect(adb_device)
            d.info
            logger.info("adbutils 路径成功")
            return d
        except Exception as e:
            logger.warning(f"策略3 adbutils 路径失败: {e}")

        raise DeviceConnectionError(
            f"u2 连接失败（3种策略均失败）\n策略1: {last_err}\n设备地址: {device_addr}",
            failure_code="u2_all_failed",
        )

    def disconnect(self):
        """断开连接（不清除缓存，让 TTL 管理）"""
        self._connected = False
        self.device = None
        self._last_connection_failure_code = "device_not_connected"
        logger.info("设备实例已断开连接（缓存保留）")

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self._connected or not self._device:
            self._last_connection_failure_code = "device_not_connected"
            return False

        try:
            self._device.device_info
            self._last_connection_failure_code = ""
            return True
        except Exception as e:
            logger.debug(f"检查连接状态失败: {e}")
            self._connected = False
            self._last_connection_failure_code = "device_unresponsive"
            return False

    def open_app(self, package_name: str, notify_config=None) -> bool:
        """打开应用，并自动处理登录页"""
        if not self.is_connected():
            code = self._last_connection_failure_code or "device_not_connected"
            message = "设备无响应" if code == "device_unresponsive" else "设备未连接"
            raise DeviceConnectionError(message, failure_code=code)

        LOGIN_ACTIVITY = 'com.tencent.wework.login.controller.LoginQrCodeLoginActivity'

        try:
            logger.info(f"正在打开应用: {package_name}")
            self._device.app_start(package_name)

            # 等待 APP 进入前台（最多10秒）
            for _ in range(10):
                time.sleep(1)
                current = self._device.app_current()
                if current.get('package') == package_name:
                    break
            else:
                logger.warning(f"应用可能未正确启动，当前包: {self._device.app_current()}")
                return False

            logger.info("应用启动成功")

            # 再等最多5秒，让登录页完全渲染
            for _ in range(5):
                time.sleep(1)
                cur = self._device.app_current()
                activity = cur.get('activity', '')
                if LOGIN_ACTIVITY in activity or 'WwMainActivity' in activity:
                    break

            # 检测并处理登录页
            self._handle_login_if_needed(package_name, notify_config)
            return True

        except LoginTimeoutError:
            raise
        except Exception as e:
            logger.error(f"打开应用失败: {e}")
            raise AppNotFoundError(f"无法打开应用 {package_name}: {e}")

    LOGIN_ACTIVITY = 'com.tencent.wework.login.controller.LoginQrCodeLoginActivity'
    MAIN_ACTIVITY = 'com.tencent.wework.launch.WwMainActivity'

    def _is_logged_in(self) -> bool:
        """检查是否已登录：优先 UI 元素，备用 Activity 名"""
        try:
            if self._device(text="工作台").exists:
                return True
            if self._device(text="消息").exists:
                return True
        except Exception as e:
            logger.debug(f"UI元素检查登录状态失败: {e}")
        cur = self._device.app_current()
        return self.MAIN_ACTIVITY in cur.get('activity', '')

    def _is_on_login_page(self) -> bool:
        """检查是否在登录页：优先 UI 元素，备用 Activity 名"""
        try:
            if self._device(text="企业微信").exists and self._device(text="扫码登录").exists:
                return True
            if self._device(text="通过手机登录").exists:
                return True
        except Exception as e:
            logger.debug(f"UI元素检查登录页失败: {e}")
        cur = self._device.app_current()
        return self.LOGIN_ACTIVITY in cur.get('activity', '')

    def _send_login_notify(self, title, msg, notify_config=None):
        """发送登录相关通知"""
        if not notify_config:
            return
        try:
            from src.utils.notifier import send_serverchan
            if notify_config.notification.enabled and notify_config.notification.webhook:
                send_serverchan(notify_config.notification.webhook, title, msg)
        except Exception as e:
            logger.warning(f"登录通知发送失败: {e}")

    def _wait_for_login(self, wait_seconds) -> bool:
        """等待登录完成，返回是否成功"""
        logger.info(f"等待登录完成（最多{wait_seconds}秒）...")
        for elapsed in range(wait_seconds):
            time.sleep(1)
            if (elapsed + 1) % 30 == 0:
                mins = (elapsed + 1) // 60
                logger.info(f"等待登录中... 已等待 {mins} 分钟")
            if self._is_logged_in():
                logger.info("登录成功，已进入主界面")
                return True
        return False

    def _handle_login_if_needed(self, package_name: str, notify_config=None):
        """
        检测登录页并处理：
        第一次：等待1分钟，超时则重启APP再试
        第二次：等待5分钟，超时则发通知

        登录检测优先用 UI 元素（"工作台"），Activity 名作为备用，
        避免 APP 更新后 Activity 名变化导致误判。
        """
        if not self._is_on_login_page():
            return

        # 第一次尝试：等待1分钟
        logger.info("检测到登录页，分析登录类型...")
        phone_login_btn = self._device(text='通过手机登录')
        if phone_login_btn.exists:
            logger.info("检测到记忆登录，自动点击「通过手机登录」")
            phone_login_btn.click()
        else:
            logger.info("检测到扫码登录页")

        success = self._wait_for_login(60)
        if success:
            return

        # 第一次超时，重启APP再试
        logger.warning("第一次登录等待超时，重启应用进行第二次尝试...")
        try:
            self._device.app_stop(package_name)
            time.sleep(2)
            self._device.app_start(package_name)
            for _ in range(10):
                time.sleep(1)
                if self._is_logged_in() or self._is_on_login_page():
                    break
        except Exception as e:
            logger.warning(f"重启应用失败: {e}")

        if self._is_logged_in():
            logger.info("重启后已自动登录")
            return

        # 第二次尝试：等待5分钟，先发通知
        phone_login_btn = self._device(text='通过手机登录')
        if phone_login_btn.exists:
            self._send_login_notify("通卡通 - 需要手机确认登录", "请在手机上确认登录交建通（第二次尝试）", notify_config)
        else:
            self._send_login_notify("通卡通 - 需要扫码登录", "请用手机扫码登录交建通（主界面右上角「+」→ 扫一扫）", notify_config)

        success = self._wait_for_login(300)
        if success:
            return

        logger.error("两次登录均超时，登录失败")
        self._send_login_notify("❌ 通卡通 - 登录失败", "两次登录尝试均超时，请手动登录交建通后重启通卡通。", notify_config)
        raise LoginTimeoutError("交建通登录超时，请先在手机上完成登录确认后再重试")

    def close_app(self, package_name: str) -> bool:
        """关闭应用"""
        if not self.is_connected():
            return False

        try:
            self._device.app_stop(package_name)
            logger.info(f"已关闭应用: {package_name}")
            return True
        except Exception as e:
            logger.error(f"关闭应用失败: {e}")
            return False

    def do_checkin(self, action: CheckinAction,
                   find_button: Callable = None,
                   verify_success: Callable = None) -> CheckinResult:
        """
        执行打卡

        Args:
            action: 打卡动作 (签到/签退)
            find_button: 自定义查找按钮函数
            verify_success: 自定义验证成功函数

        Returns:
            CheckinResult 打卡结果
        """
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            if not self.is_connected():
                code = self._last_connection_failure_code or "device_not_connected"
                message = "设备无响应" if code == "device_unresponsive" else "设备未连接"
                raise DeviceConnectionError(message, failure_code=code)

            _signin_actions = (CheckinAction.SIGNIN, CheckinAction.MORNING_SIGNIN, CheckinAction.AFTERNOON_SIGNIN)
            action_name = "签到" if action in _signin_actions else "签退"
            logger.info(f"开始执行{action_name}...")

            # 1. 导航到考勤界面
            if not self.navigate_to_checkin():
                return CheckinResult(
                    success=False,
                    action=action,
                    message=self._compose_result_message("导航失败：无法进入考勤界面"),
                    timestamp=timestamp,
                    failure_code="navigation_failed",
                    recovery_action=self._last_navigation_recovery_action,
                )

            self._wait_for_ui_ready(timeout=5.0)

            # 2. 查找并点击打卡按钮（含重试）
            max_find_retries = 3
            retry_delays = [0.5, 1.0, 2.0]
            success = False

            for attempt in range(max_find_retries):
                if find_button:
                    success = find_button(self._device, action)
                else:
                    success = self._default_find_and_click(action)

                if success:
                    break

                if attempt < max_find_retries - 1:
                    delay = retry_delays[attempt]
                    logger.info(f"未找到按钮，{delay}秒后重试（第{attempt + 1}/{max_find_retries}次）")
                    time.sleep(delay)
                    self._wait_for_ui_ready(timeout=2.0)

            if not success:
                self._save_failure_diagnosis(action, "未找到打卡按钮")

            if success:
                # 3. 处理覆盖确认对话框（重新签到/签退时出现）
                self._handle_confirm_dialog(timeout=30)

                # 4. 验证打卡结果（轮询，最多3秒）
                for _ in range(6):
                    time.sleep(0.5)
                    if "加载中" not in self._device.dump_hierarchy(compressed=True):
                        break

                if verify_success:
                    verified = verify_success(self._device)
                else:
                    verified = self._default_verify(action)

                if verified:
                    logger.info(f"{action_name}成功")
                    # 打卡完成后返回工作台
                    self._return_to_workbench()
                    return CheckinResult(
                        success=True,
                        action=action,
                        message=self._compose_result_message(f"{action_name}成功"),
                        timestamp=timestamp,
                        recovery_action=self._last_navigation_recovery_action,
                    )
                else:
                    logger.warning(f"{action_name}结果未确认")
                    return CheckinResult(
                        success=False,
                        action=action,
                        message=self._compose_result_message(f"结果确认失败：{action_name}结果未确认，请手动检查"),
                        timestamp=timestamp,
                        failure_code="result_unconfirmed",
                        recovery_action=self._last_navigation_recovery_action,
                    )
            else:
                return CheckinResult(
                    success=False,
                    action=action,
                    message=self._compose_result_message(f"按钮定位失败：未找到{action_name}按钮"),
                    timestamp=timestamp,
                    failure_code="button_not_found",
                    recovery_action=self._last_navigation_recovery_action,
                )

        except AlreadyCheckedInError as e:
            if e.in_correct_slot:
                logger.info(f"{e}，跳过本次打卡")
                self._return_to_workbench()
                return CheckinResult(
                    success=True,
                    action=action,
                    message=self._compose_result_message(str(e)),
                    timestamp=timestamp,
                    recovery_action=self._last_navigation_recovery_action,
                )
            else:
                logger.warning(f"打卡失败弹窗: {e}")
                self._return_to_workbench()
                return CheckinResult(
                    success=False,
                    action=action,
                    message=self._compose_result_message(str(e)),
                    timestamp=timestamp,
                    failure_code="app_popup_failed",
                    recovery_action=self._last_navigation_recovery_action,
                )
        except GpsLocationError as e:
            logger.warning(f"GPS定位失败: {e}")
            self._return_to_workbench()
            return CheckinResult(
                success=False,
                action=action,
                message=self._compose_result_message(f"GPS定位失败: {str(e)}"),
                timestamp=timestamp,
                failure_code="gps_runtime_failed",
                recovery_action=self._last_navigation_recovery_action,
            )
        except DeviceConnectionError as e:
            return CheckinResult(
                success=False,
                action=action,
                message=self._compose_result_message(f"设备异常：{str(e)}"),
                timestamp=timestamp,
                failure_code=getattr(e, "failure_code", "device_connection_failed"),
                recovery_action=self._last_navigation_recovery_action,
            )
        except Exception as e:
            logger.error(f"打卡过程出错: {e}")
            return CheckinResult(
                success=False,
                action=action,
                message=self._compose_result_message(f"执行异常：{str(e)}"),
                timestamp=timestamp,
                failure_code="execution_error",
                recovery_action=self._last_navigation_recovery_action,
            )

    def _default_find_and_click(self, action: CheckinAction) -> bool:
        """
        查找并点击打卡按钮（委托给 ButtonFinder）。
        """
        is_morning, is_signin, action_text = self._resolve_action_slot(action)
        slot_label = "上午" if is_morning else "下午"
        return self._button_finder.default_find_and_click(
            action, action_text, is_morning, is_signin, slot_label
        )

    def set_makeup_windows(self, windows: dict) -> None:
        """设置有效打卡窗口配置，供右侧时间按钮判定是否正常或可重新签退。"""
        self._button_finder.set_makeup_windows(windows)

    def _strategy_row_anchor(self, all_nodes: List[dict], action, is_morning, is_signin,
                             action_text, slot_label, mid_x, screen_w) -> Optional[bool]:
        """委托给 ButtonFinder（保留方法名以兼容测试）"""
        return self._button_finder._strategy_row_anchor(
            all_nodes, is_morning, is_signin, action_text, slot_label, mid_x, screen_w
        )

    def _strategy_full_scan(self, all_nodes: List[dict], action, is_morning, is_signin,
                            action_text, slot_label, mid_x, screen_w) -> Optional[bool]:
        """委托给 ButtonFinder"""
        return self._button_finder._strategy_full_scan(
            all_nodes, is_morning, is_signin, action_text, slot_label, mid_x, screen_w
        )

    def _fallback_find_and_click(self, action, is_morning, is_signin, action_text, slot_label, xml, screen_w):
        """委托给 ButtonFinder（保留方法名以兼容测试）"""
        return self._button_finder._fallback_find_and_click(
            action, is_morning, is_signin, action_text, slot_label, xml, screen_w
        )

    def _handle_confirm_dialog(self, timeout: int = 30):
        """处理覆盖打卡时的确认对话框（委托给 CheckinVerifier）"""
        self._verifier.handle_confirm_dialog(timeout)

    def _click_button_by_text(self, texts: List[str]) -> bool:
        """按文本查找并点击按钮（委托给 DialogHandler）"""
        return self._verifier._dialog_handler.click_button_by_text(texts)

    def _resolve_action_slot(self, action: CheckinAction) -> Tuple[bool, bool, str]:
        """委托给 ButtonFinder（保留方法名以兼容测试）"""
        return ButtonFinder.resolve_action_slot(action)

    def _default_verify(self, action: CheckinAction) -> bool:
        """默认验证打卡是否成功（委托给 CheckinVerifier）"""
        return self._verifier.default_verify(action)

    def _return_to_workbench(self):
        """打卡完成后返回工作台界面（委托给导航器）"""
        self._navigator.return_to_workbench()

    def _dismiss_navigation_dialogs(self):
        """关闭导航过程里常见的提示弹窗（委托给导航器）。"""
        if self._device is not None:
            self._navigator.dismiss_navigation_dialogs()

    @staticmethod
    def _navigation_recovery_label(action: str) -> str:
        mapping = {
            "return_home_retry": "回主界面重试",
            "restart_app_retry": "重开交建通重试",
        }
        return mapping.get(action or "", "")

    def _compose_result_message(self, base_message: str) -> str:
        """把本次真实用到的导航恢复动作透出到结果文案里。"""
        label = self._navigation_recovery_label(self._last_navigation_recovery_action)
        if not label:
            return base_message
        return f"{base_message}（导航恢复：{label}）"

    def _navigate_workbench_to_checkin(self, max_attempts: int = 3) -> bool:
        """委托给导航器。"""
        return self._navigator._navigate_workbench_to_checkin(max_attempts)

    def _return_to_app_main_page_for_retry(self, max_back: int = 6) -> bool:
        """委托给导航器。"""
        return self._navigator._return_to_app_main_page_for_retry(max_back)

    def _restart_app_for_navigation_retry(self) -> bool:
        """委托给导航器。"""
        return self._navigator._restart_app_for_navigation_retry()

    def navigate_to_checkin(self) -> bool:
        """导航到考勤打卡界面（委托给导航器）"""
        ok = self._navigator.navigate_to_checkin()
        self._last_navigation_recovery_action = self._navigator.last_recovery_action
        if not ok:
            # 导航失败的诊断信息由本类处理（传入正确的 CheckinAction 枚举）
            self._save_failure_diagnosis(CheckinAction.SIGNIN, "无法导航到考勤界面")
        return ok

    def take_screenshot(self, save_path: str = None) -> Optional[str]:
        """截图（自动清理超过 3 天的旧截图）"""
        if not self.is_connected():
            return None

        try:
            from datetime import datetime

            if save_path is None:
                from pathlib import Path
                screenshot_dir = Path(__file__).parent.parent.parent / "screenshots"
                screenshot_dir.mkdir(exist_ok=True)
                save_path = screenshot_dir / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

                # 清理 3 天前的截图
                try:
                    cutoff = datetime.now().timestamp() - 3 * 86400
                    for old in screenshot_dir.glob("screenshot_*.png"):
                        if old.stat().st_mtime < cutoff:
                            old.unlink()
                except Exception as e:
                    logger.debug(f"清理旧截图失败: {e}")

            self._device.screenshot(str(save_path))
            logger.info(f"截图已保存: {save_path}")
            return str(save_path)

        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def dump_hierarchy(self, save_path: str = None) -> str:
        """
        导出UI层级结构 (用于调试和定位元素)
        """
        if not self.is_connected():
            code = self._last_connection_failure_code or "device_not_connected"
            message = "设备无响应" if code == "device_unresponsive" else "设备未连接"
            raise DeviceConnectionError(message, failure_code=code)

        try:
            xml = self._device.dump_hierarchy()

            if save_path:
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(xml)
                logger.info(f"UI层级已保存: {save_path}")

            return xml

        except Exception as e:
            logger.error(f"导出UI层级失败: {e}")
            return ""

    def _is_image_recognition_available(self) -> bool:
        """检查图像识别是否可用"""
        if self._image_recognition_available is not None:
            return self._image_recognition_available
        try:
            from .image_recognition import _ensure_ocr, _ensure_cv2
            ocr_ok = _ensure_ocr() is not None
            cv2_ok = _ensure_cv2() is not None
            self._image_recognition_available = ocr_ok or cv2_ok
            return self._image_recognition_available
        except Exception as e:
            logger.debug(f"检查图像识别可用性失败: {e}")
            self._image_recognition_available = False
            return False

    def _find_by_ocr(self, text: str, timeout: int = 5) -> Optional[Tuple[int, int]]:
        """通过 OCR 查找文本位置"""
        if not self._is_image_recognition_available():
            return None
        try:
            from .image_recognition import find_text_by_ocr
            import time as _time
            start = _time.time()
            while _time.time() - start < timeout:
                screenshot = self._device.screenshot()
                result = find_text_by_ocr(screenshot, text)
                if result:
                    logger.info(f"OCR 找到 '{text}': {result}")
                    return result
                _time.sleep(0.5)
            logger.debug(f"OCR 未找到 '{text}'")
            return None
        except Exception as e:
            logger.warning(f"OCR 查找失败: {e}")
            return None

    def _find_by_template(self, template_path: str, threshold: float = 0.8) -> Optional[Tuple[int, int]]:
        """通过模板匹配查找位置"""
        if not self._is_image_recognition_available():
            return None
        try:
            from .image_recognition import template_match
            screenshot = self._device.screenshot()
            results = template_match(screenshot, template_path, threshold=threshold)
            if results:
                best = results[0]
                logger.info(f"模板匹配找到: {template_path}, 置信度={best.confidence:.2f}, 位置={best.location}")
                return best.location
            logger.debug(f"模板匹配未找到: {template_path}")
            return None
        except Exception as e:
            logger.warning(f"模板匹配失败: {e}")
            return None

    def hybrid_find_text(self, text: str, timeout: int = 5) -> Optional[Tuple[int, int]]:
        """
        混合识别策略：按优先级降级查找文本位置
        
        优先级：
        1. Accessibility text= 精确匹配
        2. Accessibility textContains 模糊匹配
        3. OCR 文本识别（兜底）
        4. 图像模板匹配（最终兜底）
        
        Args:
            text: 要查找的文本
            timeout: 超时时间（秒）
        
        Returns:
            文本中心点坐标 (x, y)，未找到返回 None
        """
        # 策略 1: Accessibility 精确匹配
        try:
            btn = self._device(text=text, clickable=True)
            if btn.exists:
                info = btn.info
                bounds = info.get('bounds', {})
                x = (bounds.get('left', 0) + bounds.get('right', 0)) // 2
                y = (bounds.get('top', 0) + bounds.get('bottom', 0)) // 2
                logger.debug(f"Accessibility 精确匹配找到 '{text}': ({x}, {y})")
                return (x, y)
        except Exception as e:
            logger.debug(f"Accessibility 精确匹配失败: {e}")

        # 策略 2: Accessibility 模糊匹配
        try:
            btn = self._device(textContains=text, clickable=True)
            if btn.exists:
                info = btn.info
                bounds = info.get('bounds', {})
                x = (bounds.get('left', 0) + bounds.get('right', 0)) // 2
                y = (bounds.get('top', 0) + bounds.get('bottom', 0)) // 2
                logger.debug(f"Accessibility 模糊匹配找到 '{text}': ({x}, {y})")
                return (x, y)
        except Exception as e:
            logger.debug(f"Accessibility 模糊匹配失败: {e}")

        # 策略 3: OCR 兜底
        ocr_result = self._find_by_ocr(text, timeout=timeout)
        if ocr_result:
            return ocr_result

        # 策略 4: 模板匹配（如果有对应模板）
        from pathlib import Path
        template_dir = Path(__file__).parent.parent.parent / "templates"
        template_path = template_dir / f"{text}.png"
        if template_path.exists():
            template_result = self._find_by_template(str(template_path))
            if template_result:
                return template_result

        logger.debug(f"混合识别未找到 '{text}'")
        return None

    def find_checkin_button(self, action: CheckinAction, timeout: int = 5) -> Optional[Tuple[int, int]]:
        """
        查找打卡按钮（右侧时间文本，既是显示也是按钮）
        
        根据时间段判断是上午签到、上午签退、下午签到还是下午签退：
        - 上午签到：05:00-08:00
        - 上午签退：11:30-13:00
        - 下午签到：12:00-13:30
        - 下午签退：17:00-次日04:00
        
        Args:
            action: 打卡动作类型
            timeout: 超时时间（秒）
        
        Returns:
            按钮中心点坐标 (x, y)，未找到返回 None
        """
        from datetime import datetime
        now = datetime.now()
        h, m = now.hour, now.minute
        
        # 判断当前属于哪个时间段
        is_morning_signin = 5 <= h < 8
        is_morning_signout = 11 <= h < 13 or (h == 13 and m == 0)
        is_afternoon_signin = 12 <= h < 13 or (h == 13 and m <= 30)
        is_afternoon_signout = 17 <= h or h < 4
        
        # 根据 action 和时间段确定目标行索引
        is_signin = action in (CheckinAction.MORNING_SIGNIN, CheckinAction.SIGNIN)
        
        if is_morning_signin:
            target_idx = 0  # 上午签到
        elif is_morning_signout:
            target_idx = 1  # 上午签退
        elif is_afternoon_signin:
            target_idx = 2  # 下午签到
        elif is_afternoon_signout:
            target_idx = 3  # 下午签退
        else:
            # 不在任何时间段内，使用 action 判断
            target_idx = 0 if is_signin else 1
        
        try:
            xml = self._device.dump_hierarchy(compressed=False)
            screen_width = self._device.info.get('displayWidth', 1080)
            right_threshold = screen_width * 0.5

            all_nodes = parse_hierarchy_xml(xml)
            left_labels = []
            for n in all_nodes:
                t = n.get('text', '').strip()
                clickable = n.get('clickable', 'false')
                b = n.get('_bounds')
                if t in ("签到", "签退") and clickable == "false" and b:
                    x1, y1, x2, y2 = b
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    if cx < right_threshold:
                        left_labels.append((cy, cx, t))

            left_labels.sort(key=lambda x: x[0])

            if target_idx < len(left_labels):
                target_y = left_labels[target_idx][0]
                y_tolerance = 30
                for n in all_nodes:
                    if n.get('clickable', '') != 'true':
                        continue
                    b = n.get('_bounds')
                    if not b:
                        continue
                    x1, y1, x2, y2 = b
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    if cx > right_threshold and abs(cy - target_y) < y_tolerance:
                        text = n.get('text', '')
                        logger.info(f"找到打卡按钮: '{text}' at ({cx}, {cy})")
                        return (cx, cy)
        except Exception as e:
            logger.debug(f"行锚定法查找打卡按钮失败: {e}")
        
        # 策略 2: OCR 兜底 - 查找右侧时间文本
        if self._is_image_recognition_available():
            try:
                from .image_recognition import ocr_recognize
                screenshot = self._device.screenshot()
                screen_width = self._device.info.get('displayWidth', 1080)
                
                # 只识别右半部分
                region = (screen_width // 2, 0, screen_width // 2, screenshot.height)
                ocr_results = ocr_recognize(screenshot, region=region)
                
                # 过滤时间格式文本
                time_results = []
                for r in ocr_results:
                    if TIME_PATTERN.match(r.text) and r.confidence > 0.8:
                        # 计算中心点（需要加上 region 偏移）
                        box = r.box
                        cx = int(sum(p[0] for p in box) / 4) + region[0]
                        cy = int(sum(p[1] for p in box) / 4)
                        time_results.append((cy, cx, r.text, r.confidence))
                
                # 按 Y 坐标排序
                time_results.sort(key=lambda x: x[0])
                
                if target_idx < len(time_results):
                    cy, cx, text, conf = time_results[target_idx]
                    logger.info(f"OCR 找到打卡时间: '{text}' at ({cx}, {cy}), 置信度={conf:.2f}")
                    return (cx, cy)
            except Exception as e:
                logger.debug(f"OCR 查找打卡按钮失败: {e}")
        
        logger.debug(f"未找到打卡按钮，目标行索引={target_idx}")
        return None


# 简单的测试用模拟器 (用于开发调试)
class MockAutomator(AutomatorBase):
    """模拟自动化执行器 (用于测试)"""

    def __init__(self):
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        logger.info("[Mock] 设备已连接")
        return True

    def disconnect(self):
        self._connected = False
        logger.info("[Mock] 设备已断开")

    def is_connected(self) -> bool:
        return self._connected

    def open_app(self, package_name: str, notify_config=None) -> bool:
        logger.info(f"[Mock] 打开应用: {package_name}")
        return True

    def close_app(self, package_name: str) -> bool:
        logger.info(f"[Mock] 关闭应用: {package_name}")
        return True

    def do_checkin(self, action: CheckinAction) -> CheckinResult:
        from datetime import datetime
        _signin_actions = (CheckinAction.SIGNIN, CheckinAction.MORNING_SIGNIN, CheckinAction.AFTERNOON_SIGNIN)
        action_name = "签到" if action in _signin_actions else "签退"
        logger.info(f"[Mock] 执行{action_name}")

        return CheckinResult(
            success=True,
            action=action,
            message=f"[Mock] {action_name}成功",
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

    def take_screenshot(self, save_path: str = None) -> Optional[str]:
        logger.info("[Mock] 截图")
        return save_path
