"""
通知模块 - Server酱推送
配置：notification.enabled=true, notification.webhook=your_sendkey
Server酱文档：https://sct.ftqq.com/
"""
import requests
import logging
import time

logger = logging.getLogger(__name__)


def send_serverchan(sendkey: str, title: str, desp: str = "", verify_tls: bool = True) -> bool:
    """
    发送 Server酱通知

    Args:
        sendkey: Server酱的 SendKey（从 https://sct.ftqq.com/ 获取）
        title: 消息标题（最长64字符）
        desp: 消息内容，支持 Markdown（可选）

    Returns:
        是否发送成功
    """
    if not sendkey:
        logger.warning("Server酱 SendKey 未配置")
        return False

    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    max_retries = 2

    for attempt in range(max_retries + 1):
        try:
            if not verify_tls:
                logger.warning("通知配置已关闭 HTTPS 证书校验（verify_tls=false），存在安全风险")

            resp = requests.post(url, data={"title": title, "desp": desp}, timeout=10, verify=verify_tls)

            if resp.status_code >= 500 and attempt < max_retries:
                logger.warning(f"Server酱服务端错误 HTTP {resp.status_code}，5秒后重试")
                time.sleep(5)
                continue

            if resp.status_code != 200:
                body_preview = (resp.text or "")[:300].strip()
                logger.warning(f"Server酱通知发送失败 HTTP {resp.status_code}: {body_preview}")
                return False

            try:
                data = resp.json()
            except Exception as e:
                body_preview = (resp.text or "")[:300].strip()
                logger.warning(f"Server酱响应解析失败（非JSON）: {e}, 响应内容: {body_preview}")
                return False

            if data.get("code") == 0:
                logger.info(f"Server酱通知发送成功: {title}")
                return True
            else:
                logger.warning(f"Server酱通知发送失败: {data}")
                return False

        except requests.exceptions.SSLError as e:
            logger.error(f"Server酱通知 HTTPS 证书校验失败，请检查网络/证书或将 verify_tls 设为 false: {e}")
            return False
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"Server酱通知异常（第{attempt + 1}次），5秒后重试: {e}")
                time.sleep(5)
            else:
                logger.error(f"Server酱通知异常（已重试{max_retries}次）: {e}")
                return False


def notify_checkin_result(config, action_name: str, success: bool, message: str, timestamp: str):
    """
    打卡结果通知

    Args:
        config: Config 对象
        action_name: 打卡类型（如"上午签到"）
        success: 是否成功
        message: 结果消息
        timestamp: 打卡时间
    """
    notify_cfg = getattr(config, 'notification', None)
    if not notify_cfg:
        return
    if not getattr(notify_cfg, 'enabled', False):
        return
    webhook = getattr(notify_cfg, 'webhook', '')
    if not webhook:
        logger.warning("通知已启用但 SendKey 未填写")
        return

    status = "✅ 成功" if success else "❌ 失败"
    title = f"通卡通 {status} - {action_name}"
    desp = f"**时间**：{timestamp}\n\n**结果**：{message}"

    verify_tls = getattr(notify_cfg, 'verify_tls', True)
    send_serverchan(webhook, title, desp, verify_tls=verify_tls)
