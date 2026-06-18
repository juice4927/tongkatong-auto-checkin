"""
UI 导航与恢复模块

职责：
- 从交建通主界面导航到考勤打卡页面
- 导航失败时的多级恢复策略（返回重试 → 重启 APP）
- 打卡完成后返回工作台
- 处理导航过程中常见的弹窗
"""
import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


class CheckinNavigator:
    """
    UI 导航与恢复。

    从交建通主界面进入 工作台 → 考勤打卡界面，
    并在导航失败时自动执行恢复策略。
    """

    LOGIN_ACTIVITY = 'com.tencent.wework.login.controller.LoginQrCodeLoginActivity'
    MAIN_ACTIVITY = 'com.tencent.wework.launch.WwMainActivity'

    def __init__(
        self,
        device,
        package_name: str,
        open_app_cb: Callable[..., bool],
        close_app_cb: Callable[..., bool],
    ):
        """
        Args:
            device: uiautomator2 设备实例
            package_name: 目标 APP 包名
            open_app_cb: 打开 APP 的回调 (package_name) -> bool
            close_app_cb: 关闭 APP 的回调 (package_name) -> bool
        """
        self._device = device
        self._package_name = package_name
        self._open_app = open_app_cb
        self._close_app = close_app_cb

        # 外部可读：最近一次导航恢复的动作标识
        self.last_recovery_action: str = ""

    # ── 内部工具方法 ──────────────────────────────────────────────────

    def dismiss_navigation_dialogs(self):
        """关闭导航过程里常见的提示弹窗。"""
        for dismiss_text in ["我知道了", "知道了", "确定", "关闭", "取消"]:
            btn = self._device(text=dismiss_text, clickable=True)
            if btn.exists:
                logger.info(f"关闭导航弹窗: {dismiss_text}")
                btn.click()
                time.sleep(1)

    def _is_checkin_page_ready(self) -> bool:
        """进入考勤页后，必须看到关键标识才算成功。"""
        return (
            self._device(textContains="今日考勤").exists
            or self._device(textContains="签到").exists
        )

    @staticmethod
    def recovery_label(action: str) -> str:
        mapping = {
            "return_home_retry": "回主界面重试",
            "restart_app_retry": "重开交建通重试",
        }
        return mapping.get(action or "", "")

    # ── 导航到考勤 ────────────────────────────────────────────────────

    def _navigate_workbench_to_checkin(self, max_attempts: int = 3) -> bool:
        """从交建通主界面进入 工作台 -> 考勤。"""
        if self._is_checkin_page_ready():
            logger.info("已在考勤界面")
            return True

        if self._device(text="工作台").exists:
            logger.info("点击工作台...")
            self._device(text="工作台").click()
            time.sleep(2)

        for attempt in range(max_attempts):
            self.dismiss_navigation_dialogs()
            attendance_btn = self._device(textContains="考勤")
            if attendance_btn.exists:
                logger.info("点击考勤...")
                attendance_btn.click()
                time.sleep(3)

                for _ in range(5):
                    if self._is_checkin_page_ready():
                        return True
                    time.sleep(1)

                logger.warning("点击考勤后未检测到关键标识，准备返回后重试")
                self._device.press("back")
                time.sleep(1)
            elif attempt < max_attempts - 1:
                logger.info(f"未找到考勤入口，等待后重试({attempt + 1}/{max_attempts})")
                time.sleep(2)

        return False

    def _return_to_app_main_page_for_retry(self, max_back: int = 6) -> bool:
        """导航失败后，尽量回到交建通主界面，为二次进入考勤做准备。"""
        try:
            current = self._device.app_current()
            if current.get('package') != self._package_name:
                logger.info("导航恢复：交建通不在前台，重新打开应用")
                self._open_app(self._package_name)
                time.sleep(2)

            self.dismiss_navigation_dialogs()

            for i in range(max_back):
                current = self._device.app_current()
                if current.get('package') != self._package_name:
                    logger.info("导航恢复：前台已离开交建通，重新拉回应用")
                    self._open_app(self._package_name)
                    time.sleep(2)
                    self.dismiss_navigation_dialogs()
                    continue

                activity = current.get('activity', '')
                if self.MAIN_ACTIVITY in activity or self._device(text="工作台").exists:
                    logger.info("导航恢复：已回到交建通主界面")
                    return True

                logger.info(f"导航恢复：按返回键回主界面 ({i + 1}/{max_back})，当前: {activity}")
                self._device.press("back")
                time.sleep(1)
                self.dismiss_navigation_dialogs()

            logger.warning("导航恢复：多次返回后仍未回到主界面，尝试重新打开交建通")
            self._open_app(self._package_name)
            time.sleep(2)
            self.dismiss_navigation_dialogs()
            current = self._device.app_current()
            return self.MAIN_ACTIVITY in current.get('activity', '') or self._device(text="工作台").exists
        except Exception as e:
            logger.warning(f"导航恢复失败: {e}", exc_info=True)
            return False

    def _restart_app_for_navigation_retry(self) -> bool:
        """导航恢复的最后兜底：强制重开交建通，再从主界面重走一次。"""
        try:
            logger.warning("导航恢复：准备强制重开交建通后再次进入考勤")
            try:
                self._close_app(self._package_name)
            except Exception as close_error:
                logger.warning(f"导航恢复：关闭交建通失败，继续尝试重开: {close_error}")
            time.sleep(1)
            self._open_app(self._package_name)
            time.sleep(2)
            self.dismiss_navigation_dialogs()
            current = self._device.app_current()
            ready = self.MAIN_ACTIVITY in current.get('activity', '') or self._device(text="工作台").exists
            if ready:
                logger.info("导航恢复：重开交建通成功，已回到主界面")
            else:
                logger.warning(f"导航恢复：重开交建通后仍未到主界面，当前: {current}")
            return ready
        except Exception as e:
            logger.warning(f"导航恢复：重开交建通失败: {e}", exc_info=True)
            return False

    def navigate_to_checkin(self) -> bool:
        """
        导航到考勤打卡界面（含重试和弹窗处理）

        Returns:
            是否成功进入打卡界面
        """
        try:
            self.last_recovery_action = ""
            current = self._device.app_current()
            if current.get('package') != self._package_name:
                logger.info("打开交建通应用...")
                self._open_app(self._package_name)
                time.sleep(2)

            self.dismiss_navigation_dialogs()

            if self._navigate_workbench_to_checkin():
                return True

            logger.warning("首次导航到考勤失败，尝试先返回交建通主界面，再重新进入工作台-考勤")
            if self._return_to_app_main_page_for_retry():
                self.last_recovery_action = "return_home_retry"
                self.dismiss_navigation_dialogs()
                if self._navigate_workbench_to_checkin():
                    return True

            logger.warning("回主界面重试仍失败，尝试强制重开交建通后再次进入工作台-考勤")
            if self._restart_app_for_navigation_retry():
                self.last_recovery_action = "restart_app_retry"
                self.dismiss_navigation_dialogs()
                if self._navigate_workbench_to_checkin():
                    return True

            logger.warning("无法导航到考勤界面")
            return False

        except Exception as e:
            logger.error(f"导航失败: {e}", exc_info=True)
            return False

    # ── 打卡完成后返回 ────────────────────────────────────────────────

    def return_to_workbench(self):
        """打卡完成后返回工作台界面（按系统返回键）"""
        try:
            for _ in range(4):
                time.sleep(0.5)
                cur = self._device.app_current()
                if 'WwMainActivity' not in cur.get('activity', ''):
                    break

            for confirm_text in ["确定", "知道了", "我知道了", "关闭", "完成"]:
                btn = self._device(text=confirm_text, clickable=True)
                if btn.exists:
                    logger.info(f"关闭弹窗: {confirm_text}")
                    btn.click()
                    time.sleep(1)
                    break

            max_back = 5
            for i in range(max_back):
                current = self._device.app_current()
                activity = current.get('activity', '')

                if 'WwMainActivity' in activity:
                    logger.info("已返回工作台主界面")
                    if self._device(text="工作台").exists:
                        self._device(text="工作台").click()
                    break

                if current.get('package') != self._package_name:
                    break

                logger.debug(f"按返回键 ({i + 1}/{max_back})，当前: {activity}")
                self._device.press("back")
                time.sleep(1)

        except Exception as e:
            logger.warning(f"返回工作台失败（不影响打卡结果）: {e}", exc_info=True)
