"""
随机时间生成模块
"""
import random
from datetime import datetime, time, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def parse_time(time_str: str) -> time:
    """解析时间字符串 (HH:MM)"""
    parts = time_str.split(':')
    return time(int(parts[0]), int(parts[1]))


def time_to_minutes(t: time) -> int:
    """时间转换为分钟数"""
    return t.hour * 60 + t.minute


def minutes_to_time(minutes: int) -> time:
    """分钟数转换为时间"""
    return time(minutes // 60, minutes % 60)


def generate_random_time(
    start_time: str,
    end_time: str,
    random_seconds: tuple[int, int] = (0, 0)
) -> datetime:
    """
    在指定时间范围内生成随机时间
    
    Args:
        start_time: 开始时间 (HH:MM)
        end_time: 结束时间 (HH:MM)
        random_seconds: 额外随机秒数范围 (min, max)
        
    Returns:
        今天的随机时间 datetime
    """
    start = parse_time(start_time)
    end = parse_time(end_time)
    
    start_minutes = time_to_minutes(start)
    end_minutes = time_to_minutes(end)
    
    if end_minutes < start_minutes:
        raise ValueError(f"结束时间 {end_time} 不能早于开始时间 {start_time}")
    
    # 随机选择分钟
    random_minutes = random.randint(start_minutes, end_minutes)
    
    # 随机秒数
    extra_seconds = random.randint(random_seconds[0], random_seconds[1])
    
    # 构建时间
    base_time = datetime.today().replace(
        hour=random_minutes // 60,
        minute=random_minutes % 60,
        second=0,
        microsecond=0
    )
    
    result_time = base_time + timedelta(seconds=extra_seconds)
    
    logger.debug(f"生成随机时间: {start_time}-{end_time} -> {result_time.strftime('%H:%M:%S')}")
    
    return result_time


def generate_checkin_times(
    time_configs: dict,
    random_seconds: tuple[int, int] = (1, 5)
) -> dict[str, Optional[datetime]]:
    """
    生成所有打卡的随机时间
    
    Args:
        time_configs: 打卡时间配置字典
        random_seconds: 随机秒数范围
        
    Returns:
        每个打卡点的随机时间字典
    """
    result = {}
    
    for key, config in time_configs.items():
        if not config.enabled:
            result[key] = None
            continue
        
        time_range = config.time_range
        if len(time_range) != 2:
            logger.warning(f"配置 {key} 的时间范围格式错误: {time_range}")
            result[key] = None
            continue
        
        result[key] = generate_random_time(
            time_range[0],
            time_range[1],
            random_seconds
        )
    
    return result


def format_time_for_display(dt: datetime) -> str:
    """格式化时间用于显示"""
    return dt.strftime('%H:%M:%S')


def format_time_for_log(dt: datetime) -> str:
    """格式化时间用于日志"""
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# 测试
if __name__ == "__main__":
    # 测试随机时间生成
    from dataclasses import dataclass
    
    @dataclass
    class TestConfig:
        enabled: bool = True
        time_range: tuple = ("08:30", "09:00")
    
    configs = {
        "morning": TestConfig(enabled=True, time_range=("08:30", "09:00")),
        "afternoon": TestConfig(enabled=True, time_range=("17:30", "18:00")),
        "disabled": TestConfig(enabled=False, time_range=("00:00", "01:00")),
    }
    
    times = generate_checkin_times(configs)
    for key, t in times.items():
        if t:
            print(f"{key}: {format_time_for_display(t)}")
        else:
            print(f"{key}: 禁用")