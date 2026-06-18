"""
配置管理模块
"""
import logging
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_UPDATE_MANIFEST_URL = (
    "https://github.com/juice4927/tongkatong-auto-checkin"
)

@dataclass
class CheckinTime:
    """打卡时间配置"""
    enabled: bool
    time_range: tuple[str, str]
    label: str


class MakeupWindowConfig(BaseModel):
    """有效打卡窗口配置（格式：[起始时, 起始分, 结束时, 结束分]，结束时>23表示次日）"""
    morning_signin:    list[int] = [4,  0,  8,  0]
    morning_signout:   list[int] = [11, 30, 13, 30]
    afternoon_signin:  list[int] = [11, 30, 13, 30]
    afternoon_signout: list[int] = [17, 0,  28, 0]

    @field_validator('morning_signin', 'morning_signout', 'afternoon_signin', 'afternoon_signout')
    @classmethod
    def _validate_window(cls, v: list[int]) -> list[int]:
        if len(v) != 4:
            raise ValueError("有效打卡窗口格式必须为 [起始时, 起始分, 结束时, 结束分]")
        sh, sm, eh, em = v
        if not (0 <= sh <= 48 and 0 <= eh <= 48):
            raise ValueError("有效打卡窗口小时必须在 0..48 范围内")
        if not (0 <= sm <= 59 and 0 <= em <= 59):
            raise ValueError("有效打卡窗口分钟必须在 0..59 范围内")
        return v


class MuMuConfig(BaseModel):
    """MuMu模拟器配置"""
    host: str = "127.0.0.1"
    port: int = 5555
    adb_path: str = ""
    mumu_exe_path: str = ""
    gps_latitude: float = 0.0   # 打卡位置纬度，0表示不设置
    gps_longitude: float = 0.0  # 打卡位置经度，0表示不设置

    @field_validator('port')
    @classmethod
    def _validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("端口必须在 1..65535 范围内")
        return v

    @field_validator('gps_latitude')
    @classmethod
    def _validate_latitude(cls, v: float) -> float:
        if v != 0 and not (-90 <= v <= 90):
            raise ValueError("纬度必须在 -90 到 90 范围内（0 表示不设置）")
        return v

    @field_validator('gps_longitude')
    @classmethod
    def _validate_longitude(cls, v: float) -> float:
        if v != 0 and not (-180 <= v <= 180):
            raise ValueError("经度必须在 -180 到 180 范围内（0 表示不设置）")
        return v


class AppConfig(BaseModel):
    """交建通APP配置"""
    package_name: str = "com.tencent.weworklocal"
    activity: str = ""


class HolidayConfig(BaseModel):
    """节假日配置"""
    skip_weekend: bool = True
    skip_holiday: bool = True
    extra_workdays: list[str] = Field(default_factory=list)  # 额外工作日 (YYYY-MM-DD)
    extra_holidays: list[str] = Field(default_factory=list)  # 额外节假日

    @field_validator("extra_workdays", "extra_holidays", mode="before")
    @classmethod
    def _normalize_extra_dates(cls, value):
        from datetime import datetime

        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("额外日期必须为列表")

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            date_str = str(raw or "").strip()
            if not date_str:
                continue
            try:
                normalized_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"额外日期格式无效: {date_str}，必须为 YYYY-MM-DD") from exc
            if normalized_str not in seen:
                seen.add(normalized_str)
                normalized.append(normalized_str)
        normalized.sort()
        return normalized


class NotificationConfig(BaseModel):
    """通知配置"""
    enabled: bool = False
    webhook: str = ""
    verify_tls: bool = True


class UpdateConfig(BaseModel):
    """软件更新配置"""
    manifest_url: str = DEFAULT_PUBLIC_UPDATE_MANIFEST_URL
    auto_check_on_startup: bool = True


class DialogButtonRatios(BaseModel):
    """弹窗按钮在屏幕上的默认位置比例"""
    cancel_x: float = 0.30
    cancel_y: float = 0.594
    confirm_x: float = 0.70
    confirm_y: float = 0.594


class NetworkProbeConfig(BaseModel):
    """网络探测目标配置"""
    targets: list[list[int | str]] = Field(
        default_factory=lambda: [
            ["223.5.5.5", 53],
            ["114.114.114.114", 53],
            ["1.1.1.1", 53],
            ["8.8.8.8", 53],
        ],
        description="探测目标列表，每项 [host, port]",
    )
    timeout_seconds: int = 5


class AdvancedConfig(BaseModel):
    """高级配置（可调参数，通常无需修改）"""
    dialog_button_ratios: DialogButtonRatios = Field(default_factory=DialogButtonRatios)
    network_probe: NetworkProbeConfig = Field(default_factory=NetworkProbeConfig)
    session_ttl_seconds: int = 300
    misfire_grace_seconds: int = 300


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = (value or "").split(":")
    if len(parts) != 2:
        raise ValueError("时间格式必须为 HH:MM")
    try:
        h = int(parts[0])
        m = int(parts[1])
    except Exception:
        raise ValueError("时间格式必须为 HH:MM")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("时间必须在 00:00..23:59 范围内")
    return h, m


class CheckinEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    enabled: bool = True
    time_range: list[str] = Field(default_factory=lambda: ["00:00", "00:00"])
    label: str = ""

    @field_validator('time_range')
    @classmethod
    def _validate_time_range(cls, v: list[str]) -> list[str]:
        if len(v) != 2:
            raise ValueError("time_range 必须为 [开始, 结束]，格式 HH:MM")
        sh, sm = _parse_hhmm(v[0])
        eh, em = _parse_hhmm(v[1])
        if (eh, em) < (sh, sm):
            raise ValueError("time_range 结束时间不能早于开始时间")
        return v


class AppStateConfig(BaseModel):
    """应用状态配置（自动恢复）"""
    auto_connect: bool = False  # 启动时自动连接设备
    auto_start: bool = False    # 连接成功后自动启动打卡
    keep_alive_enabled: bool = True  # 启用常驻守护与自动恢复
    recovery_base_backoff_seconds: int = 5
    recovery_max_backoff_seconds: int = 300
    recovery_max_failures: int = 20
    recovery_pause_minutes_after_max_failures: int = 30
    recovery_quiet_hours_enabled: bool = False
    recovery_quiet_start_hour: int = 0
    recovery_quiet_end_hour: int = 0
    setup_prompt_shown: bool = False
    gps_prompt_shown: bool = False

    @field_validator(
        "recovery_base_backoff_seconds",
        "recovery_max_backoff_seconds",
        "recovery_max_failures",
        "recovery_pause_minutes_after_max_failures",
    )
    @classmethod
    def _validate_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("恢复策略参数不能为负数")
        return v

    @field_validator("recovery_quiet_start_hour", "recovery_quiet_end_hour")
    @classmethod
    def _validate_hour(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("静默时段小时必须在 0..23")
        return v

    @model_validator(mode='after')
    def _validate_recovery_policy(self):
        if self.recovery_max_backoff_seconds and self.recovery_base_backoff_seconds > self.recovery_max_backoff_seconds:
            raise ValueError("recovery_base_backoff_seconds 不能大于 recovery_max_backoff_seconds")
        if self.recovery_quiet_hours_enabled and self.recovery_quiet_start_hour == self.recovery_quiet_end_hour:
            raise ValueError("启用静默时段时，开始小时和结束小时不能相同")
        return self


class RandomDelayConfig(BaseModel):
    """随机延迟配置"""
    min_seconds: int = 1
    max_seconds: int = 5

    @model_validator(mode='after')
    def _validate_delay(self):
        if self.min_seconds < 0 or self.max_seconds < 0:
            raise ValueError("随机延迟秒数不能为负数")
        if self.max_seconds < self.min_seconds:
            raise ValueError("随机延迟 max_seconds 不能小于 min_seconds")
        return self


class Config(BaseModel):
    """主配置"""
    mumu: MuMuConfig = Field(default_factory=MuMuConfig)
    checkin: dict = Field(default_factory=dict)
    app: AppConfig = Field(default_factory=AppConfig)
    holiday: HolidayConfig = Field(default_factory=HolidayConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    random_delay: RandomDelayConfig = Field(default_factory=RandomDelayConfig)
    app_state: AppStateConfig = Field(default_factory=AppStateConfig)
    checkin_window: MakeupWindowConfig = Field(default_factory=MakeupWindowConfig)
    makeup_window: MakeupWindowConfig = Field(default_factory=MakeupWindowConfig)
    advanced: AdvancedConfig = Field(default_factory=AdvancedConfig)
    
    model_config = ConfigDict(extra="allow")


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_dir: Path = None):
        if config_dir is None:
            # 获取正确的配置目录路径
            import sys
            if getattr(sys, 'frozen', False):
                # 打包后，使用 exe 所在目录
                self.config_dir = Path(sys.executable).parent / "config"
            else:
                # 开发环境
                self.config_dir = Path(__file__).parent.parent.parent / "config"
        else:
            self.config_dir = config_dir
        
        self.config_file = self.config_dir / "user_config.json"
        self._config: Optional[Config] = None
    
    @property
    def config(self) -> Config:
        """获取配置"""
        if self._config is None:
            self.load()
        return self._config
    
    # 内置默认配置（exe 独立运行时无需外部 default.json）
    _BUILTIN_DEFAULT = {
        "mumu": {"host": "127.0.0.1", "port": 5555, "adb_path": ""},
        "checkin": {
            "morning_signin":    {"enabled": True, "time_range": ["07:20", "07:55"], "label": "上午签到"},
            "morning_signout":   {"enabled": True, "time_range": ["11:35", "12:00"], "label": "上午签退"},
            "afternoon_signin":  {"enabled": True, "time_range": ["13:10", "13:25"], "label": "下午签到"},
            "afternoon_signout": {"enabled": True, "time_range": ["17:10", "17:30"], "label": "下午签退"},
        },
        "checkin_window": {
            "morning_signin": [4, 0, 8, 0],
            "morning_signout": [11, 30, 13, 30],
            "afternoon_signin": [11, 30, 13, 30],
            "afternoon_signout": [17, 0, 28, 0],
        },
        "app": {"package_name": "com.tencent.weworklocal", "activity": ""},
        "holiday": {"skip_weekend": True, "skip_holiday": True, "extra_workdays": [], "extra_holidays": []},
        "notification": {"enabled": False, "webhook": "", "verify_tls": True},
        "update": {"manifest_url": DEFAULT_PUBLIC_UPDATE_MANIFEST_URL, "auto_check_on_startup": True},
        "random_delay": {"min_seconds": 1, "max_seconds": 5},
        "app_state": {
            "auto_connect": False,
            "auto_start": False,
            "keep_alive_enabled": True,
            "recovery_base_backoff_seconds": 5,
            "recovery_max_backoff_seconds": 300,
            "recovery_max_failures": 20,
            "recovery_pause_minutes_after_max_failures": 30,
            "recovery_quiet_hours_enabled": False,
            "recovery_quiet_start_hour": 0,
            "recovery_quiet_end_hour": 0,
            "setup_prompt_shown": False,
            "gps_prompt_shown": False,
        },
    }

    def _load_default_data(self) -> dict:
        """加载默认配置数据（不包含用户覆盖）"""
        default_data = deepcopy(self._BUILTIN_DEFAULT)
        default_file = self.config_dir / "default.json"
        if default_file.exists():
            with open(default_file, 'r', encoding='utf-8') as f:
                default_data = self._deep_merge(default_data, json.load(f))
        return default_data

    def _normalize_checkin(self, config: Config):
        """规范化打卡配置，确保异常值回退到默认值"""
        try:
            checkin_default = self._BUILTIN_DEFAULT.get("checkin", {})
            checkin_raw = dict(config.checkin or {})
            checkin_validated = {}
            for key, value in checkin_raw.items():
                try:
                    entry = CheckinEntry(**(value or {}))
                    checkin_validated[key] = entry.model_dump()
                except Exception as e:
                    fallback = checkin_default.get(key)
                    if fallback:
                        checkin_validated[key] = deepcopy(fallback)
                    logger.warning(f"checkin 配置无效，已降级为默认值: {key} ({e})")
            config.checkin = checkin_validated
        except Exception as e:
            logger.warning(f"checkin 配置校验失败（已跳过）: {e}")

    def _normalize_checkin_window(self, config: Config, raw_data: dict):
        """同步新旧有效打卡窗口字段：checkin_window 优先，makeup_window 兼容旧配置。"""
        explicit_checkin_window = "checkin_window" in raw_data
        explicit_makeup_window = "makeup_window" in raw_data
        if explicit_checkin_window:
            source = config.checkin_window.model_dump()
            source.update(raw_data.get("checkin_window") or {})
        elif explicit_makeup_window:
            source = config.checkin_window.model_dump()
            source.update(raw_data.get("makeup_window") or {})
        else:
            source = config.checkin_window.model_dump()
        try:
            window = MakeupWindowConfig(**source)
        except Exception as e:
            logger.warning(f"有效打卡窗口配置无效，已使用默认值: {e}")
            window = MakeupWindowConfig()
        config.checkin_window = window
        config.makeup_window = window

    def get_default_config(self) -> Config:
        """获取项目默认配置（内置默认 + default.json，不含用户配置）"""
        raw = self._load_default_data()
        config = Config(**raw)
        self._normalize_checkin(config)
        self._normalize_checkin_window(config, raw)
        return config

    def load(self) -> Config:
        """加载配置（自动创建默认配置）"""
        # 内置默认 → default.json 覆盖 → user_config.json 覆盖
        default_data = self._load_default_data()

        user_data = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"用户配置文件损坏: {e}，已备份并回退到默认配置")
                backup = self.config_file.with_suffix('.json.bak')
                try:
                    self.config_file.rename(backup)
                except OSError:
                    pass
                user_data = {}

        merged = self._deep_merge(default_data, user_data)
        self._config = Config(**merged)
        self._normalize_checkin(self._config)
        window_raw = {}
        if "checkin_window" in user_data:
            window_raw["checkin_window"] = user_data["checkin_window"]
        elif "makeup_window" in user_data:
            window_raw["makeup_window"] = user_data["makeup_window"]
        else:
            window_raw = merged
        self._normalize_checkin_window(self._config, window_raw)
        if not (self._config.update.manifest_url or "").strip():
            self._config.update.manifest_url = DEFAULT_PUBLIC_UPDATE_MANIFEST_URL

        # 首次运行：自动保存一份 user_config.json
        if not self.config_file.exists() or not (merged.get("update", {}).get("manifest_url", "") or "").strip():
            self.save()

        return self._config
    
    def save(self, config: Config = None):
        """保存配置"""
        if config:
            self._config = config
        
        if self._config is None:
            return
        
        # 确保目录存在
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # 原子写入：先写入临时文件，再替换
        fd, tmp_path = tempfile.mkstemp(dir=str(self.config_dir), suffix='.tmp', prefix='config_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self._config.model_dump(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_file)
        except Exception:
            # 写入失败时清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    
    def _deep_merge(self, base: dict, override: dict) -> dict:
        """深度合并字典"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def get_checkin_times(self) -> dict[str, CheckinTime]:
        """获取打卡时间配置"""
        result = {}
        for key, value in self.config.checkin.items():
            try:
                entry = CheckinEntry(**value) if isinstance(value, dict) else CheckinEntry(**dict(value))
            except Exception:
                entry = CheckinEntry()
            result[key] = CheckinTime(
                enabled=entry.enabled,
                time_range=tuple(entry.time_range),
                label=entry.label or key
            )
        return result
