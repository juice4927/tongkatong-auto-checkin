"""
打卡验证模块 — 验证打卡结果与处理弹窗

职责：
- 验证打卡是否成功（多策略检测）
- 处理覆盖打卡时的确认对话框
- 通用按钮查找（按文本多策略查找）
- 保存失败诊断信息（截图 + XML dump）
"""
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Callable

from .xml_parser import parse_hierarchy_xml, extract_center_dialog_texts, TIME_PATTERN
from .button_finder import ButtonFinder

logger = logging.getLogger(__name__)


# 默认弹窗按钮位置比例（兜底用，可通过 DialogHandler 构造函数覆盖）
DIALOG_CANCEL_RATIOS = (0.30, 0.594)
DIALOG_CONFIRM_RATIOS = (0.70, 0.594)


class DialogHandler:
    """通用弹窗处理 — 关闭提示/确认弹窗。"""

    def __init__(self, device, cancel_ratios=None, confirm_ratios=None):
        """
        Args:
            device: uiautomator2 设备实例
            cancel_ratios: (x, y) 取消按钮在屏幕上的默认比例位置
            confirm_ratios: (x, y) 确定按钮在屏幕上的默认比例位置
        """
        self._device = device
        self._cancel_ratios = cancel_ratios or DIALOG_CANCEL_RATIOS
        self._confirm_ratios = confirm_ratios or DIALOG_CONFIRM_RATIOS

    def click_button_by_text(self, texts: List[str], screen_size: Optional[tuple] = None) -> bool:
        """
        按文本查找并点击按钮，支持多种回退策略。
        
        Args:
            texts: 按钮文本列表，按优先级排序
            screen_size: (w, h) 屏幕尺寸，可选
            
        Returns:
            是否成功点击
        """
        w, h = screen_size or (1080, 1920)

        # 策略1：查找原生按钮
        for text in texts:
            btn = self._device(text=text, clickable=True)
            if btn.exists:
                logger.info(f"找到原生'{text}'按钮，点击")
                btn.click()
                time.sleep(1)
                return True

        # 策略2：如果查找"取消"，尝试通过"确定"按钮推算位置
        if "取消" in texts:
            for confirm_text in ["确定", "确认", "OK"]:
                confirm_btn = self._device(text=confirm_text, clickable=True)
                if confirm_btn.exists:
                    try:
                        info = confirm_btn.info
                        bounds = info.get('bounds', {})
                        confirm_cx = (bounds.get('left', 0) + bounds.get('right', 0)) // 2
                        confirm_cy = (bounds.get('top', 0) + bounds.get('bottom', 0)) // 2
                        cancel_cx = w - confirm_cx
                        cancel_cy = confirm_cy
                        logger.info(f"根据确定按钮({confirm_cx},{confirm_cy})推算取消按钮: ({cancel_cx},{cancel_cy})")
                        self._device.click(cancel_cx, cancel_cy)
                        time.sleep(1)
                        return True
                    except Exception as e:
                        logger.debug(f"确定镜像法失败: {e}")

        # 策略3：如果查找"确定"，尝试通过"取消"按钮推算位置
        if any(t in ["确定", "确认", "OK"] for t in texts):
            cancel_btn = self._device(text="取消", clickable=True)
            if cancel_btn.exists:
                try:
                    info = cancel_btn.info
                    bounds = info.get('bounds', {})
                    cancel_cx = (bounds.get('left', 0) + bounds.get('right', 0)) // 2
                    cancel_cy = (bounds.get('top', 0) + bounds.get('bottom', 0)) // 2
                    confirm_cx = w - cancel_cx
                    confirm_cy = cancel_cy
                    logger.info(f"根据取消按钮({cancel_cx},{cancel_cy})推算确定按钮: ({confirm_cx},{confirm_cy})")
                    self._device.click(confirm_cx, confirm_cy)
                    time.sleep(1)
                    return True
                except Exception as e:
                    logger.debug(f"取消镜像法失败: {e}")

        # 策略4：根据屏幕比例直接点击
        rx, ry = self._cancel_ratios if "取消" in texts else self._confirm_ratios
        cx, cy = int(w * rx), int(h * ry)
        label = "取消" if "取消" in texts else "确定"
        logger.info(f"尝试点击{label}按钮: ({cx}, {cy}) [比例 {rx:.2f}, {ry:.3f}]")
        self._device.click(cx, cy)
        time.sleep(1)
        return True


class CheckinVerifier:
    """打卡结果验证器。"""

    def __init__(self, device, package_name: str,
                 compute_result_message_fn: Callable[[str], str] = None,
                 save_diagnosis_fn: Callable = None):
        """
        Args:
            device: uiautomator2 设备实例
            package_name: APP 包名
            compute_result_message_fn: 格式化结果消息的回调 (base_message) -> str
            save_diagnosis_fn: 保存诊断信息的回调 (action, reason) -> None
        """
        self._device = device
        self._package_name = package_name
        self._compute_result_message = compute_result_message_fn or (lambda msg: msg)
        self._save_diagnosis = save_diagnosis_fn
        self._dialog_handler = DialogHandler(device)
        self._button_finder = ButtonFinder(device, already_checked_in_error_class=Exception)

    # ── 确认对话框处理 ────────────────────────────────────────────────

    def handle_confirm_dialog(self, timeout: int = 30):
        """
        处理覆盖打卡时的确认对话框。
        仅在确实检测到弹窗时才操作，无弹窗则直接返回。
        """
        logger.debug("检测是否需要确认覆盖...")

        start = time.time()
        xml = ""
        found_dialog = False
        while time.time() - start <= timeout:
            time.sleep(0.5)
            xml = self._device.dump_hierarchy(compressed=True)
            try:
                screen_w, screen_h = self._device.window_size()
            except Exception:
                screen_w, screen_h = (1080, 1920)
            dialog_texts = extract_center_dialog_texts(xml, screen_w, screen_h)

            fail_keywords = ["超出距离", "不在打卡范围", "定位失败", "网络异常", "打卡失败"]
            for kw in fail_keywords:
                if kw in dialog_texts:
                    logger.warning(f"检测到打卡失败弹窗: {kw}")
                    for close_text in ["确定", "知道了", "关闭"]:
                        btn = self._device(text=close_text, clickable=True)
                        if btn.exists:
                            btn.click()
                            break
                    if kw in ("超出距离", "不在打卡范围", "定位失败"):
                        # 延迟导入避免循环引用
                        from .automator import GpsLocationError
                        raise GpsLocationError(f"打卡失败: {kw}")
                    from .automator import AlreadyCheckedInError
                    raise AlreadyCheckedInError(f"打卡失败弹窗: {kw}", in_correct_slot=False)

            success_keywords = ["打卡成功", "签到成功", "签退成功"]
            for kw in success_keywords:
                if kw in dialog_texts:
                    logger.info(f"检测到成功弹窗: {kw}")
                    for close_text in ["确定", "知道了", "关闭"]:
                        btn = self._device(text=close_text, clickable=True)
                        if btn.exists:
                            btn.click()
                            return
                    return

            if "加载中" in xml:
                time.sleep(1)
                continue

            if "重新签" in xml or 'text="提示"' in xml or 'text="提示 "' in xml:
                found_dialog = True
                break

            # 无弹窗特征持续超过 8 秒 → 大概率不在正确页面，提前退出
            if time.time() - start > 8 and not found_dialog:
                logger.debug(f"等待{time.time()-start:.0f}秒未检测到弹窗特征文本，提前退出（最长{timeout}秒）")
                return

        if not found_dialog:
            logger.debug(f"等待{timeout}秒超时未检测到弹窗，跳过")
            return

        cancel_keywords = ["退出登录", "退出", "注销", "删除", "移除"]
        confirm_keywords = ["重新签", "覆盖", "确认", "确定"]

        should_click_cancel = any(kw in xml for kw in cancel_keywords)

        if should_click_cancel:
            logger.info("检测到需要点击'取消'的弹窗")
            self._dialog_handler.click_button_by_text(["取消", "关闭", "返回"])
            return

        if any(kw in xml for kw in confirm_keywords):
            logger.info("检测到需要点击'确定'的弹窗")
            self._dialog_handler.click_button_by_text(["确定", "确认", "OK", "ok"])

    # ── 打卡结果验证 ──────────────────────────────────────────────────

    def default_verify(self, action) -> bool:
        """
        默认验证打卡是否成功。

        策略：
          1. 检测失败弹窗关键词 → 直接返回 False
          2. 检测成功弹窗关键词 → 直接返回 True
          3. 检测目标行从按钮变为时间/完成态 → 返回 True
          4. 检测右侧可点击时间节点接近当前时间 → 返回 True
          5. 以上均未命中 → 截图保存，返回 False

        Args:
            action: CheckinAction 枚举
        """
        try:
            xml = self._device.dump_hierarchy(compressed=True)
            try:
                screen_w, _ = self._device.window_size()
            except Exception:
                screen_w = 1080
            try:
                _, screen_h = self._device.window_size()
            except Exception:
                screen_h = 1920
            dialog_texts = extract_center_dialog_texts(xml, screen_w, screen_h)

            fail_keywords = ["超出距离", "不在打卡范围", "定位失败", "网络异常", "打卡失败",
                             "请先定位", "无法获取", "签到失败", "签退失败"]
            for kw in fail_keywords:
                if kw in dialog_texts:
                    logger.warning(f"检测到失败提示: {kw}")
                    for close_text in ["确定", "知道了", "关闭", "取消"]:
                        btn = self._device(text=close_text, clickable=True)
                        if btn.exists:
                            btn.click()
                            break
                    return False

            success_texts = ["打卡成功", "签到成功", "签退成功"]
            for text in success_texts:
                if text in dialog_texts:
                    return True

            screen_w = self._device.window_size()[0]
            mid_x = screen_w * 0.5
            all_nodes = parse_hierarchy_xml(xml)

            # 检查目标行状态变化（从按钮变为完成态）
            if self._button_finder.verify_target_row_transition(action, all_nodes, mid_x):
                return True

            # 检查右侧可点击时间节点
            is_morning, _, _ = ButtonFinder.resolve_action_slot(action)
            _, right_nodes = self._button_finder.collect_target_row_right_nodes(
                all_nodes, action, mid_x, is_morning,
            )
            if not right_nodes:
                if self._save_diagnosis:
                    self._save_diagnosis(action, "未定位到目标行右侧状态")
                logger.warning("未定位到目标行右侧状态，判定打卡失败")
                return False

            now = datetime.now()
            now_minutes = now.hour * 60 + now.minute

            for t, clickable in right_nodes:
                if not clickable:
                    continue
                if not TIME_PATTERN.match(t):
                    continue
                h, m = map(int, t.split(':'))
                diff = abs((h * 60 + m) - now_minutes)
                if diff > 720:
                    diff = 1440 - diff
                if diff <= 3:
                    logger.info(f"检测到目标行右侧可点击时间 {t}（距当前{diff}分钟），判定打卡成功")
                    return True

            if self._save_diagnosis:
                self._save_diagnosis(action, "未检测到成功标志")
            logger.warning("未检测到成功标志，判定打卡失败")
            return False

        except Exception as e:
            logger.error(f"验证打卡结果出错: {e}")
            return False

