"""
节假日判断模块
使用 chinese_calendar 库（数据准确，含调休），降级到纯周末判断
"""
from datetime import date, datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)

try:
    import chinese_calendar as calendar
    CALENDAR_AVAILABLE = True
    logger.debug(f"chinese_calendar v{calendar.__version__} 已加载")
except ImportError:
    CALENDAR_AVAILABLE = False
    logger.warning("chinese_calendar 未安装，将使用纯周末判断")


class HolidayChecker:
    """节假日检查器"""

    def __init__(self, skip_weekend: bool = True, skip_holiday: bool = True,
                 extra_workdays: list[str] = None, extra_holidays: list[str] = None):
        self.skip_weekend = skip_weekend
        self.skip_holiday = skip_holiday
        self.extra_workdays = set(extra_workdays or [])
        self.extra_holidays = set(extra_holidays or [])

    @staticmethod
    def _date_str(check_date: date) -> str:
        return check_date.strftime('%Y-%m-%d')

    def get_manual_override_label(self, check_date: date = None) -> Optional[str]:
        if check_date is None:
            check_date = date.today()

        date_str = self._date_str(check_date)
        if date_str in self.extra_workdays:
            return "手动工作日"
        if date_str in self.extra_holidays:
            return "手动休息日"
        return None

    def is_workday(self, check_date: date = None) -> bool:
        """
        判断是否为工作日
        优先级：用户手动配置 > chinese_calendar > 纯周末判断
        """
        if check_date is None:
            check_date = date.today()

        date_str = self._date_str(check_date)

        # 用户手动配置优先
        if date_str in self.extra_workdays:
            return True
        if date_str in self.extra_holidays:
            return False

        # chinese_calendar（含法定节假日+调休）
        if self.skip_holiday and CALENDAR_AVAILABLE:
            try:
                return calendar.is_workday(check_date)
            except Exception as e:
                logger.warning(f"chinese_calendar 判断出错: {e}")

        # 降级：纯周末判断
        if self.skip_weekend:
            return check_date.weekday() < 5

        return True

    def is_holiday(self, check_date: date = None) -> bool:
        return not self.is_workday(check_date)

    def get_holiday_name(self, check_date: date = None) -> Optional[str]:
        if check_date is None:
            check_date = date.today()
        manual_label = self.get_manual_override_label(check_date)
        if manual_label:
            return manual_label
        if CALENDAR_AVAILABLE:
            try:
                detail = calendar.get_holiday_detail(check_date)
                if detail and detail[0]:
                    return detail[1] or "节假日"
            except Exception as e:
                logger.debug(f"获取节假日详情失败: {e}")
        return None

    def get_next_workday(self, from_date: date = None) -> date:
        from datetime import timedelta
        if from_date is None:
            from_date = date.today()
        next_date = from_date
        for _ in range(30):
            next_date = next_date + timedelta(days=1)
            if self.is_workday(next_date):
                return next_date
        return next_date


if __name__ == "__main__":
    checker = HolidayChecker()
    today = date.today()
    print(f"今天 ({today}) 是工作日: {checker.is_workday(today)}")
    name = checker.get_holiday_name(today)
    if name:
        print(f"今天是: {name}")
