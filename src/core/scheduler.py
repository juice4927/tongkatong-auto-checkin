"""
定时调度模块 - 基于 APScheduler
"""
import logging
import time as _time_module
import threading
import socket
import subprocess
from datetime import datetime, time, date
from pathlib import Path
from typing import Callable, Optional
from enum import Enum

from .automator import CheckinAction, LoginTimeoutError, AppNotFoundError, DeviceConnectionError
from .failure_codes import FailureCode
from .random_time import generate_checkin_times, format_time_for_display
from src.utils.notifier import notify_checkin_result, send_serverchan

logger = logging.getLogger(__name__)


# 尝试导入 APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    from apscheduler.jobstores.base import JobLookupError
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False
    JobLookupError = Exception
    logger.warning("APScheduler 未安装，定时功能不可用")


class JobStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckinScheduler:
    """打卡定时调度器"""
    
    def __init__(self):
        self._scheduler = None
        self._jobs = {}  # job_id -> job_info
        self._callbacks = {}  # event_type -> callback
        self._running = False
    
    def initialize(self) -> bool:
        """初始化调度器"""
        if not SCHEDULER_AVAILABLE:
            logger.error("APScheduler 未安装")
            return False
        
        try:
            self._scheduler = BackgroundScheduler(
                job_defaults={
                    'misfire_grace_time': 300,  # 5分钟容差，防止系统延迟导致任务被跳过
                    'coalesce': True,           # 合并错过的多次触发为一次
                }
            )
            self._scheduler.add_listener(
                self._job_executed_listener,
                EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
            )
            logger.info("调度器初始化完成")
            return True
        except Exception as e:
            logger.error(f"调度器初始化失败: {e}")
            return False
    
    def start(self):
        """启动调度器"""
        if self._scheduler and not self._running:
            self._scheduler.start()
            self._running = True
            logger.info("调度器已启动")
    
    def stop(self):
        """停止调度器"""
        if self._scheduler and self._running:
            self._scheduler.shutdown()
            self._running = False
            logger.info("调度器已停止")
    
    def add_daily_job(self, job_id: str, 
                      target_time: time,
                      callback: Callable,
                      args: tuple = None,
                      kwargs: dict = None) -> bool:
        """
        添加每日定时任务
        
        Args:
            job_id: 任务唯一标识
            target_time: 目标时间
            callback: 回调函数
            args: 位置参数
            kwargs: 关键字参数
            
        Returns:
            是否添加成功
        """
        if not self._scheduler:
            logger.error("调度器未初始化")
            return False
        
        try:
            # 移除已存在的同名任务
            self.remove_job(job_id)
            
            # 添加新任务
            self._scheduler.add_job(
                callback,
                CronTrigger(
                    hour=target_time.hour,
                    minute=target_time.minute,
                    second=target_time.second
                ),
                id=job_id,
                args=args or (),
                kwargs=kwargs or {},
                replace_existing=True
            )
            
            self._jobs[job_id] = {
                'time': target_time,
                'callback': callback,
                'status': JobStatus.PENDING
            }
            
            logger.info(f"添加定时任务: {job_id} -> {target_time.strftime('%H:%M:%S')}")
            return True
            
        except Exception as e:
            logger.error(f"添加任务失败: {e}")
            return False
    
    def add_once_job(self, job_id: str,
                     run_time: datetime,
                     callback: Callable,
                     args: tuple = None,
                     kwargs: dict = None) -> bool:
        """
        添加一次性任务
        
        Args:
            job_id: 任务唯一标识
            run_time: 运行时间
            callback: 回调函数
            args: 位置参数
            kwargs: 关键字参数
            
        Returns:
            是否添加成功
        """
        if not self._scheduler:
            logger.error("调度器未初始化")
            return False
        
        try:
            self.remove_job(job_id)
            
            self._scheduler.add_job(
                callback,
                'date',
                run_date=run_time,
                id=job_id,
                args=args or (),
                kwargs=kwargs or {}
            )
            
            self._jobs[job_id] = {
                'time': run_time,
                'callback': callback,
                'status': JobStatus.PENDING
            }
            
            logger.info(f"添加一次性任务: {job_id} -> {run_time.strftime('%Y-%m-%d %H:%M:%S')}")
            return True
            
        except Exception as e:
            logger.error(f"添加任务失败: {e}")
            return False
    
    def remove_job(self, job_id: str) -> bool:
        """移除任务。

        这里按幂等方式处理：任务本来就不存在时不记错误日志，同时清理本地缓存，
        避免跨日重调度时因调度器状态与本地缓存短暂不同步而刷出误报。
        """
        if not self._scheduler:
            return False
        
        try:
            scheduler_job = self._scheduler.get_job(job_id)
            cached = job_id in self._jobs

            if scheduler_job is None:
                if cached:
                    del self._jobs[job_id]
                    logger.debug(f"任务 {job_id} 在调度器中不存在，已清理本地缓存")
                return False

            self._scheduler.remove_job(job_id)
            if cached:
                del self._jobs[job_id]
            logger.info(f"移除任务: {job_id}")
            return True
        except JobLookupError:
            if job_id in self._jobs:
                del self._jobs[job_id]
            logger.debug(f"任务 {job_id} 已不存在，跳过移除")
            return False
        except Exception as e:
            logger.error(f"移除任务失败: {e}")
            return False
    
    def get_jobs(self) -> dict:
        """获取所有任务"""
        return self._jobs.copy()

    def reconcile_managed_jobs(self, managed_job_ids: set[str]) -> dict:
        """同步受管任务的本地缓存与 APScheduler 实际任务。"""
        stats = {
            "removed_scheduler_jobs": 0,
            "cleared_cached_jobs": 0,
        }
        if not self._scheduler:
            return stats

        scheduled_ids = {job.id for job in self._scheduler.get_jobs()}
        for job_id in managed_job_ids:
            if job_id in scheduled_ids:
                if self.remove_job(job_id):
                    stats["removed_scheduler_jobs"] += 1
                continue

            if job_id in self._jobs:
                del self._jobs[job_id]
                stats["cleared_cached_jobs"] += 1
                logger.debug(f"受管任务 {job_id} 仅残留在本地缓存，已清理")

        return stats
    
    def get_next_run_time(self, job_id: str) -> Optional[datetime]:
        """获取下次运行时间"""
        if not self._scheduler:
            return None
        
        try:
            job = self._scheduler.get_job(job_id)
            if job:
                return job.next_run_time
            return None
        except Exception as e:
            logger.debug(f"获取任务下次运行时间失败: {e}")
            return None
    
    def on_job_complete(self, callback: Callable):
        """注册任务完成回调"""
        self._callbacks['complete'] = callback
    
    def on_job_error(self, callback: Callable):
        """注册任务错误回调"""
        self._callbacks['error'] = callback
    
    def _job_executed_listener(self, event):
        """任务执行监听器"""
        job_id = event.job_id
        
        if job_id in self._jobs:
            if event.exception:
                self._jobs[job_id]['status'] = JobStatus.FAILED
                logger.error(f"任务执行失败: {job_id}, 错误: {event.exception}")
                
                if 'error' in self._callbacks:
                    self._callbacks['error'](job_id, event.exception)
            else:
                self._jobs[job_id]['status'] = JobStatus.COMPLETED
                logger.info(f"任务执行完成: {job_id}")
                
                if 'complete' in self._callbacks:
                    self._callbacks['complete'](job_id, event.retval)


class CheckinOrchestrator:
    """打卡协调器 - 整合调度、节假日判断和自动化执行"""

    def __init__(self, automator, holiday_checker, config_manager):
        self.automator = automator
        self.holiday_checker = holiday_checker
        self.config_manager = config_manager
        self.scheduler = CheckinScheduler()
        self._checkin_times = {}
        self._times_lock = threading.Lock()
        self._scheduled_date = None
        self._daily_results = []   # 当天打卡结果列表 [(action_name, success, message, timestamp)]
        self._results_lock = threading.Lock()
        self._device_lock = threading.Lock()
        self._running_job_ids = set()
        self._running_lock = threading.Lock()
        self._last_result_meta = None
    
    @staticmethod
    def record_checkin_result(success: bool, action_name: str, timestamp: str, message: str, base_dir: Path = None):
        """写入打卡记录到独立日志文件"""
        try:
            root = base_dir or Path.cwd()
            log_dir = Path(root) / "logs"
            log_dir.mkdir(exist_ok=True)
            record_file = log_dir / "checkin_records.log"
            status = "成功" if success else "失败"
            line = f"{timestamp} | {action_name} | {status} | {message}\n"
            with open(record_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.warning(f"写入打卡记录失败: {e}")
    
    @staticmethod
    def _check_network_connectivity(
        probes: list[tuple[str, int]] | None = None,
        timeout: int = 5,
    ) -> bool:
        """检查网络连通性，避免单一探测目标不可达导致误判。"""
        targets = probes or [
            ("223.5.5.5", 53),
            ("114.114.114.114", 53),
            ("1.1.1.1", 53),
            ("8.8.8.8", 53),
        ]
        for host, port in targets:
            try:
                sock = socket.create_connection((host, port), timeout=timeout)
                sock.close()
                return True
            except (socket.timeout, socket.error, OSError):
                continue
        return False
    
    def initialize(self) -> bool:
        """初始化"""
        # 初始化调度器
        if not self.scheduler.initialize():
            return False
        
        # 注册回调
        self.scheduler.on_job_complete(self._on_checkin_complete)
        self.scheduler.on_job_error(self._on_checkin_error)
        
        return True
    
    def start(self):
        """启动"""
        self.scheduler.start()
        self._schedule_today(source="startup")
        self._setup_daily_reschedule()

    def stop(self):
        """停止"""
        self.scheduler.stop()

    def get_checkin_times(self) -> dict:
        """线程安全地获取当天打卡时间"""
        with self._times_lock:
            return self._checkin_times.copy()

    def get_daily_results(self) -> list:
        with self._results_lock:
            return list(self._daily_results)

    def get_last_result(self):
        with self._results_lock:
            return self._daily_results[-1] if self._daily_results else None

    def get_last_result_meta(self):
        with self._results_lock:
            return dict(self._last_result_meta) if self._last_result_meta else None

    @staticmethod
    def _should_retry_failure_code(failure_code: str) -> bool:
        """根据失败类型决定是否值得自动重试。"""
        return FailureCode.is_retryable(failure_code)

    def _set_last_result_meta(
        self,
        action_name: str,
        success: bool,
        message: str,
        timestamp: str,
        failure_code: str = "",
        recovery_action: str = "",
    ):
        with self._results_lock:
            self._last_result_meta = {
                "action_name": action_name,
                "success": success,
                "message": message,
                "timestamp": timestamp,
                "failure_code": failure_code,
                "recovery_action": recovery_action,
            }

    def _record_terminal_result(
        self,
        action_name: str,
        success: bool,
        message: str,
        timestamp: str,
        failure_code: str = "",
        recovery_action: str = "",
    ):
        """统一记录最终结果，保持日志、汇总与通知行为一致。"""
        self.record_checkin_result(
            success,
            action_name,
            timestamp,
            message,
            base_dir=self.config_manager.config_dir.parent
        )
        with self._results_lock:
            self._daily_results.append((action_name, success, message, timestamp))
        self._set_last_result_meta(action_name, success, message, timestamp, failure_code, recovery_action)
        notify_checkin_result(
            self.config_manager.config,
            action_name,
            success,
            message,
            timestamp
        )

    def _setup_daily_reschedule(self):
        """设置每日凌晨重新调度（处理跨日运行）"""
        if not self.scheduler._scheduler:
            return

        self.scheduler._scheduler.add_job(
            self._daily_reschedule,
            CronTrigger(hour=0, minute=1, second=0),
            id='_daily_reschedule',
            replace_existing=True,
        )
        logger.info("已设置每日 00:01 自动重调度")

    def _daily_reschedule(self):
        """每日凌晨重新生成打卡时间并调度"""
        try:
            today = date.today()
            if self._scheduled_date == today:
                return
            logger.info(f"跨日重调度: {today}")
            with self._results_lock:
                self._daily_results.clear()
                self._last_result_meta = None
            self._schedule_today(source="crossday")
        except Exception as e:
            logger.error(f"跨日重调度失败: {e}", exc_info=True)
    
    def reschedule_today(self):
        """公共方法：重新安排今天的打卡任务（用于配置变更后调用）"""
        self._schedule_today(source="manual")
    
    def _schedule_today(self, source: str = "startup"):
        """
        Arrange today's check-in jobs.
        Split into: cleanup → generate times → schedule/makeup.
        """
        action_map = {
            'morning_signin': CheckinAction.MORNING_SIGNIN,
            'morning_signout': CheckinAction.MORNING_SIGNOUT,
            'afternoon_signin': CheckinAction.AFTERNOON_SIGNIN,
            'afternoon_signout': CheckinAction.AFTERNOON_SIGNOUT,
        }

        source_label = {"startup": "启动重排", "crossday": "跨日重排", "manual": "手动重排"}.get(source, source)
        reconcile_stats = self.scheduler.reconcile_managed_jobs(set(action_map))

        if not self._is_workday_today(source_label, reconcile_stats):
            return

        checkin_configs = self._generate_and_store_times()
        stats = self._classify_and_schedule_jobs(action_map, checkin_configs)

        logger.info(
            "%s摘要：清理旧任务 %s 项，清理残留缓存 %s 项，新增定时 %s 项，补签 %s 项，超窗跳过 %s 项，禁用 %s 项。",
            source_label,
            reconcile_stats["removed_scheduler_jobs"],
            reconcile_stats["cleared_cached_jobs"],
            stats["scheduled"],
            stats["makeup_count"],
            stats["skipped"],
            stats["disabled"],
        )

        self._run_makeup_jobs(stats["pending_makeup"])

    def _is_workday_today(self, source_label: str, reconcile_stats: dict) -> bool:
        """Check if today is a workday. If not, log and return False."""
        if not self.holiday_checker.is_workday():
            logger.info(
                "%s摘要：清理旧任务 %s 项，清理残留缓存 %s 项，新增定时 0 项，补签 0 项；今天不是工作日，跳过打卡。",
                source_label,
                reconcile_stats["removed_scheduler_jobs"],
                reconcile_stats["cleared_cached_jobs"],
            )
            self._scheduled_date = date.today()
            with self._times_lock:
                self._checkin_times = {}
            return False
        return True

    def _generate_and_store_times(self) -> dict:
        """Generate random check-in times for today and store them."""
        config = self.config_manager.config
        checkin_configs = self.config_manager.get_checkin_times()
        new_times = generate_checkin_times(
            checkin_configs,
            (config.random_delay.min_seconds, config.random_delay.max_seconds),
        )
        with self._times_lock:
            self._checkin_times = new_times
        self._scheduled_date = date.today()
        return checkin_configs

    def _classify_and_schedule_jobs(self, action_map: dict, checkin_configs: dict) -> dict:
        """
        Classify each check-in job: register timer if time not yet reached,
        queue for makeup if within makeup window, skip if expired.
        """
        now = datetime.now()
        mw = self.config_manager.config.makeup_window
        makeup_windows = {
            'morning_signin':   mw.morning_signin,
            'morning_signout':  mw.morning_signout,
            'afternoon_signin': mw.afternoon_signin,
            'afternoon_signout': mw.afternoon_signout,
        }

        pending_makeup: list = []
        scheduled_count = 0
        skipped_expired_count = 0
        disabled_count = 0

        for job_id, run_time in self._checkin_times.items():
            if run_time is None:
                disabled_count += 1
                continue

            action = action_map.get(job_id)
            if not action:
                continue

            label = checkin_configs.get(job_id).label if job_id in checkin_configs else job_id

            if run_time > now:
                self.scheduler.add_once_job(
                    job_id=job_id, run_time=run_time,
                    callback=self._do_checkin, args=(action, job_id),
                )
                scheduled_count += 1
                continue

            window = makeup_windows.get(job_id)
            if not window:
                continue

            sh, sm, eh, em = window
            now_minutes = now.hour * 60 + now.minute
            start_minutes = sh * 60 + sm
            end_minutes = eh * 60 + em

            if end_minutes > 1440:
                in_window = now_minutes >= start_minutes or now_minutes < (end_minutes - 1440)
            else:
                in_window = start_minutes <= now_minutes < end_minutes

            if in_window:
                logger.info(f"[启动检查] {label} 时间已过，在补签窗口内，将尝试补打")
                pending_makeup.append((action, job_id, label))
            else:
                logger.info(f"[启动检查] {label} 时间已过且超出补签窗口，跳过")
                skipped_expired_count += 1

        return {
            "scheduled": scheduled_count,
            "makeup_count": len(pending_makeup),
            "skipped": skipped_expired_count,
            "disabled": disabled_count,
            "pending_makeup": pending_makeup,
        }

    def _run_makeup_jobs(self, pending_makeup: list):
        """Execute makeup jobs in a background thread (serially, one at a time)."""
        if not pending_makeup:
            return
        makeup_copy = list(pending_makeup)

        def _run_makeup():
            for action, job_id, label in makeup_copy:
                logger.info(f"[补签] 开始尝试: {label}")
                try:
                    self._do_checkin(action, job_id)
                except Exception as e:
                    logger.error(f"[补签] {label} 失败: {e}")
                _time_module.sleep(3)

        threading.Thread(target=_run_makeup, daemon=True).start()


    def _do_checkin(self, action, job_id):
        """执行打卡入口：加锁 + 防重入，然后委托给单次尝试"""
        checkin_configs = self.config_manager.get_checkin_times()
        action_name = checkin_configs.get(job_id).label if job_id in checkin_configs else job_id
        with self._running_lock:
            if job_id in self._running_job_ids:
                logger.warning(f"任务 {job_id} 已在执行中，本次触发跳过")
                return
            self._running_job_ids.add(job_id)
        try:
            with self._device_lock:
                if not self.holiday_checker.is_workday():
                    logger.info(f"任务 {action_name}: 今天不是工作日，跳过打卡")
                    return
                if not self._check_network_connectivity():
                    logger.warning("网络不可用，等待网络恢复...")
                    _time_module.sleep(30)
                    if not self._check_network_connectivity():
                        logger.error("网络仍不可用，跳过本次打卡")
                        return
                    logger.info("网络已恢复，继续打卡")
                self._execute_checkin_attempt(action, job_id, action_name)
        finally:
            with self._running_lock:
                self._running_job_ids.discard(job_id)

    def _setup_gps(self, cfg) -> bool:
        if not cfg.mumu.gps_latitude or not cfg.mumu.gps_longitude:
            return True
        from src.utils.adb_helper import MuMuHelper
        try:
            manager = None
            search_paths = []
            if cfg.mumu.mumu_exe_path:
                search_paths.append(Path(cfg.mumu.mumu_exe_path))
            search_paths.extend(MuMuHelper.MUMU12_DEFAULT_PATHS)

            seen = set()
            for base in search_paths:
                for p in self._mumu_manager_candidates(base):
                    key = str(p).casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    if p.exists():
                        manager = p
                        break
                if manager:
                    break
            if manager:
                _flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                logger.info(f"使用 MuMuManager 设置 GPS: {manager}")
                r = subprocess.run([str(manager), "control", "-v", "0", "tool", "location",
                                    "-lat", str(cfg.mumu.gps_latitude),
                                    "-lon", str(cfg.mumu.gps_longitude)],
                                   timeout=5, capture_output=True, text=True, creationflags=_flags)
                if r.returncode == 0:
                    logger.info(f"GPS已设置: {cfg.mumu.gps_latitude}, {cfg.mumu.gps_longitude}")
                    return True
                logger.warning(f"GPS设置失败(returncode={r.returncode}): {r.stderr.strip()}")
                return False
            logger.warning("未找到 MuMuManager.exe，GPS 未设置")
            return False
        except Exception as e:
            logger.warning(f"GPS设置异常: {e}")
            return False

    @staticmethod
    def _mumu_manager_candidates(path: Path) -> list[Path]:
        """Return plausible MuMuManager paths for a configured install location."""
        if path.name.lower() == "mumumanager.exe":
            return [path]
        if path.name.lower() == "nx_main":
            return [path / "MuMuManager.exe"]
        return [path / "nx_main" / "MuMuManager.exe"]

    def _execute_checkin_attempt(self, action, job_id, action_name):
        """单次打卡尝试（含重试），由 _do_checkin 调用"""
        max_retries = 2
        retry_delay = 30
        for attempt in range(max_retries + 1):
            try:
                if not self.automator.is_connected():
                    self.automator.connect()
                cfg = self.config_manager.config
                gps_set_ok = self._setup_gps(cfg)
                if cfg.mumu.gps_latitude and cfg.mumu.gps_longitude and not gps_set_ok:
                    if attempt < max_retries:
                        logger.warning(f"GPS设置失败，{retry_delay}秒后重试（第{attempt+1}/{max_retries}次）")
                        _time_module.sleep(retry_delay)
                        continue
                    self._record_terminal_result(action_name, False,
                        "GPS虚拟定位设置失败，已跳过本次打卡以避免无效重试",
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        failure_code=FailureCode.GPS_PRECHECK_FAILED)
                    return
                self.automator.open_app(cfg.app.package_name, notify_config=cfg)
                result = self.automator.do_checkin(action)
                gps_hint = ""
                if not result.success and not gps_set_ok and cfg.mumu.gps_latitude:
                    gps_hint = "（GPS未设置，可能超出打卡范围）"
                if not result.success and attempt < max_retries and self._should_retry_failure_code(result.failure_code):
                    logger.warning(f"打卡失败，{retry_delay}秒后重试（第{attempt+1}/{max_retries}次）")
                    _time_module.sleep(retry_delay)
                    continue
                if not result.success and attempt < max_retries and result.failure_code:
                    logger.warning(f"失败类型 {result.failure_code} 不适合自动重试，直接结束本次任务")
                # 只在最终结果（不再重试时）记录并通知，避免重试过程中用户被消息轰炸
                self._record_terminal_result(action_name, result.success,
                    result.message + gps_hint, result.timestamp,
                    failure_code=result.failure_code,
                    recovery_action=getattr(result, "recovery_action", ""))
                if job_id == 'afternoon_signout':
                    self._send_daily_summary()
                return
            except LoginTimeoutError as e:
                self._record_terminal_result(action_name, False, f"登录失败：{e}",
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    failure_code=FailureCode.LOGIN_TIMEOUT)
                return
            except AppNotFoundError as e:
                self._record_terminal_result(action_name, False, f"应用启动失败：{e}",
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    failure_code=FailureCode.APP_NOT_FOUND)
                return
            except DeviceConnectionError as e:
                fc = getattr(e, "failure_code", FailureCode.DEVICE_CONNECTION_FAILED)
                if attempt < max_retries and self._should_retry_failure_code(fc):
                    logger.warning(f"设备错误 {fc}，{retry_delay}秒后重试")
                    _time_module.sleep(retry_delay)
                    continue
                msg = {FailureCode.DEVICE_UNRESPONSIVE: f"设备无响应：{e}",
                       FailureCode.DEVICE_NOT_CONNECTED: f"设备未连接：{e}"}.get(fc, f"设备连接失败：{e}")
                logger.error(f"打卡失败 [{action_name}]：{msg}")
                self._record_terminal_result(action_name, False, msg,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), failure_code=fc)
                return
            except Exception as e:
                logger.error(f"打卡失败 [{action_name}] (第{attempt+1}次): {e}", exc_info=True)
                if attempt < max_retries:
                    _time_module.sleep(retry_delay)
                else:
                    raise


    def _send_daily_summary(self):
        """发送每日打卡汇总通知"""
        config = self.config_manager.config
        if not config.notification.enabled or not config.notification.webhook:
            return

        with self._results_lock:
            results = list(self._daily_results)

        if not results:
            return

        all_ok = all(s for _, s, _, _ in results)
        title = f"{'✅' if all_ok else '⚠️'} 通卡通每日汇总 - {'全部成功' if all_ok else '有打卡失败'}"
        lines = []
        for action_name, success, message, timestamp in results:
            icon = "✅" if success else "❌"
            lines.append(f"{icon} **{action_name}** {timestamp}  \n{message}")
        desp = "\n\n".join(lines)

        ok = send_serverchan(config.notification.webhook, title, desp)
        if ok:
            logger.info("每日汇总通知已发送")
            with self._results_lock:
                self._daily_results = [r for r in self._daily_results if r not in results]
        else:
            logger.error("每日汇总通知发送失败（已重试），请检查 SendKey 或网络")

    def _on_checkin_complete(self, job_id, result):
        """打卡完成回调"""
        logger.info(f"打卡任务完成: {job_id}")
    
    def _on_checkin_error(self, job_id, error):
        """打卡错误回调（所有重试均失败）"""
        logger.error(f"打卡任务失败: {job_id}, 错误: {error}")
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        checkin_configs = self.config_manager.get_checkin_times()
        action_name = checkin_configs.get(job_id).label if job_id in checkin_configs else job_id
        message = f"打卡异常: {error}"
        self._record_terminal_result(action_name, False, message, timestamp, failure_code=FailureCode.SCHEDULER_ERROR)


# 简单测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    scheduler = CheckinScheduler()
    if scheduler.initialize():
        scheduler.start()
        
        # 添加一个测试任务
        scheduler.add_daily_job(
            "test_job",
            time(14, 30, 0),
            lambda: print("任务执行了!")
        )
        
        print("任务列表:", scheduler.get_jobs())
        
        import time
        time.sleep(5)
        
        scheduler.stop()
