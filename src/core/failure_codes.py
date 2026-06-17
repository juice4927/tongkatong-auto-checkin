"""
失败码枚举模块 — 集中管理所有打卡失败类型

避免魔法字符串散落在各处，提供 IDE 可追踪的失败码引用。
"""
from enum import Enum


class FailureCode(str, Enum):
    """打卡失败码枚举 (str 继承确保可直接用于 JSON/配置)"""
    # 设备连接类
    DEVICE_CONNECT_FAILED = "device_connect_failed"
    DEVICE_NOT_CONNECTED = "device_not_connected"
    DEVICE_UNRESPONSIVE = "device_unresponsive"
    DEVICE_CONNECTION_FAILED = "device_connection_failed"

    # 导航/界面类
    NAVIGATION_FAILED = "navigation_failed"
    BUTTON_NOT_FOUND = "button_not_found"
    RESULT_UNCONFIRMED = "result_unconfirmed"

    # GPS 类
    GPS_PRECHECK_FAILED = "gps_precheck_failed"
    GPS_RUNTIME_FAILED = "gps_runtime_failed"

    # 应用/登录类
    LOGIN_TIMEOUT = "login_timeout"
    APP_NOT_FOUND = "app_not_found"
    APP_POPUP_FAILED = "app_popup_failed"

    # 执行类
    EXECUTION_ERROR = "execution_error"
    SCHEDULER_ERROR = "scheduler_error"

    @classmethod
    def is_retryable(cls, code: str) -> bool:
        """根据失败类型决定是否值得自动重试。"""
        non_retryable = {
            cls.GPS_RUNTIME_FAILED,
            cls.GPS_PRECHECK_FAILED,
            cls.LOGIN_TIMEOUT,
            cls.APP_NOT_FOUND,
        }
        try:
            return cls(code) not in non_retryable
        except ValueError:
            # 未知失败码默认允许重试
            return True
