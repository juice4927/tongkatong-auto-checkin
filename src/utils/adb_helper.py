"""
ADB 辅助工具模块
"""
import subprocess
import logging
import time as _time
from pathlib import Path
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0


class ADBHelper:
    """ADB 辅助类"""
    
    def __init__(self, adb_path: str = "adb"):
        """
        初始化
        
        Args:
            adb_path: adb 可执行文件路径，默认使用系统 PATH 中的 adb
        """
        self.adb_path = adb_path
        self._connected_device = None
    
    def set_adb_path(self, path: str):
        """设置 adb 路径"""
        self.adb_path = path
    
    def _run_command(self, args: List[str], timeout: int = 30) -> Tuple[bool, str]:
        """
        执行 adb 命令
        
        Returns:
            (success, output)
        """
        try:
            cmd = [self.adb_path] + args
            logger.debug(f"执行命令: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='ignore',
                creationflags=_SUBPROCESS_FLAGS,
            )
            
            if result.returncode == 0:
                return True, result.stdout.strip()
            else:
                return False, result.stderr.strip()
                
        except subprocess.TimeoutExpired:
            return False, "命令超时"
        except FileNotFoundError:
            return False, f"找不到 adb: {self.adb_path}"
        except Exception as e:
            return False, str(e)
    
    def version(self) -> Optional[str]:
        """获取 adb 版本"""
        success, output = self._run_command(["version"])
        if success:
            # 提取版本号
            lines = output.split('\n')
            for line in lines:
                if 'Android Debug Bridge' in line:
                    return line
        return None
    
    def devices(self) -> List[dict]:
        """
        获取已连接设备列表
        
        Returns:
            [{'serial': 'xxx', 'status': 'device'}, ...]
        """
        success, output = self._run_command(["devices", "-l"])
        if not success:
            return []
        
        devices = []
        lines = output.split('\n')
        for line in lines[1:]:  # 跳过标题行
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    devices.append({
                        'serial': parts[0],
                        'status': parts[1]
                    })
        
        return devices
    
    def connect(self, host: str, port: int) -> Tuple[bool, str]:
        """
        连接到远程设备
        
        Args:
            host: 主机地址
            port: 端口
            
        Returns:
            (success, message)
        """
        address = f"{host}:{port}"
        success, output = self._run_command(["connect", address])
        
        if success:
            if "connected" in output.lower():
                ready, detail = self._verify_device_ready(address)
                if ready:
                    self._connected_device = address
                    logger.info(f"已连接到设备: {address}")
                    return True, output
                return False, detail
            else:
                return False, self._format_connect_failure(address, output)
        
        return False, self._format_connect_failure(address, output)

    def _verify_device_ready(self, address: str) -> Tuple[bool, str]:
        devices = self.devices()
        for device in devices:
            if device.get("serial") != address:
                continue
            status = device.get("status", "")
            if status == "device":
                return True, ""
            return False, self._format_connect_failure(address, f"设备状态: {status}")
        return False, self._format_connect_failure(address, "adb connect 成功，但 adb devices 未列出可用设备")

    @staticmethod
    def _format_connect_failure(address: str, detail: str) -> str:
        detail = (detail or "").strip() or "无详细错误"
        return (
            f"无法连接设备 {address}: {detail}\n"
            "建议：确认 MuMu 已启动并开启 ADB；端口常见为 5555/7555；"
            "若设备状态为 offline，请重启模拟器或执行 adb kill-server 后重试；"
            "ADB 路径可留空让程序自动查找 MuMu 自带 adb。"
        )
    
    def disconnect(self, host: str = None, port: int = None):
        """断开连接"""
        if host and port:
            address = f"{host}:{port}"
        elif self._connected_device:
            address = self._connected_device
        else:
            return
        
        self._run_command(["disconnect", address])
        self._connected_device = None
        logger.info(f"已断开设备: {address}")
    
    def shell(self, command: str, device: str = None) -> Tuple[bool, str]:
        """
        执行 shell 命令

        Args:
            command: shell 命令
            device: 设备序列号 (可选)

        Returns:
            (success, output)
        """
        args = []
        if device:
            args.extend(["-s", device])
        args.extend(["shell", command])

        success, output = self._run_command(args)
        return success, output
    
    def get_device_info(self, device: str = None) -> dict:
        """获取设备信息"""
        info = {}
        
        # 品牌
        success, output = self.shell("getprop ro.product.brand", device)
        if success:
            info['brand'] = output
        
        # 型号
        success, output = self.shell("getprop ro.product.model", device)
        if success:
            info['model'] = output
        
        # Android 版本
        success, output = self.shell("getprop ro.build.version.release", device)
        if success:
            info['android_version'] = output
        
        # SDK 版本
        success, output = self.shell("getprop ro.build.version.sdk", device)
        if success:
            info['sdk_version'] = output
        
        return info
    
    def get_screen_size(self, device: str = None) -> Optional[Tuple[int, int]]:
        """获取屏幕尺寸"""
        success, output = self.shell("wm size", device)
        if success:
            # 格式: Physical size: 1080x1920
            if "Physical size:" in output:
                size_str = output.split(":")[-1].strip()
                width, height = size_str.split("x")
                return int(width), int(height)
        return None
    
    def screenshot(self, save_path: str, device: str = None) -> bool:
        """截图并保存到本地"""
        # 先截图到设备
        success, _ = self.shell("screencap -p /sdcard/screenshot.png", device)
        if not success:
            return False
        
        # 拉取到本地
        args = []
        if device:
            args.extend(["-s", device])
        args.extend(["pull", "/sdcard/screenshot.png", save_path])
        
        success, _ = self._run_command(args)
        return success
    
    def list_packages(self, device: str = None) -> List[str]:
        """列出已安装的包"""
        success, output = self.shell("pm list packages", device)
        if success:
            # 格式: package:com.xxx.xxx
            packages = []
            for line in output.split('\n'):
                if line.startswith('package:'):
                    packages.append(line.replace('package:', '').strip())
            return packages
        return []


class MuMuHelper:
    """MuMu 模拟器专用辅助类"""
    
    # MuMu 模拟器默认端口
    MUMU_PORTS = {
        0: 5555,
        1: 5556,
        2: 5557,
    }
    
    # MuMu 12 安装路径
    MUMU12_DEFAULT_PATHS = [
        Path("C:/Program Files/Netease/MuMuPlayer-12.0"),
        Path("D:/Program Files/Netease/MuMuPlayer-12.0"),
        Path("E:/Program Files/Netease/MuMuPlayer-12.0"),
        Path("F:/Program Files/Netease/MuMuPlayer-12.0"),
        Path("C:/Program Files (x86)/Netease/MuMuPlayer-12.0"),
        Path("D:/MuMuPlayer"),
        Path("C:/MuMuPlayer"),
        Path("E:/MuMuPlayer"),
        Path("F:/MuMuPlayer"),
    ]
    
    def __init__(self, adb: ADBHelper = None):
        self.adb = adb or ADBHelper()

    @staticmethod
    def _mumu_exe_candidates(base_path: Path) -> list[Path]:
        """Return plausible MuMu executable paths for a user-provided path."""
        if base_path.suffix.lower() == ".exe":
            return [base_path]
        if base_path.name.lower() == "nx_main":
            return [
                base_path / "MuMuManager.exe",
                base_path / "MuMuNxMain.exe",
            ]
        return [
            base_path / "nx_main" / "MuMuManager.exe",
            base_path / "nx_main" / "MuMuNxMain.exe",
            base_path / "MuMuPlayer.exe",
            base_path / "MuMuPlayer12.exe",
            base_path / "NemuPlayer.exe",
        ]
    
    def find_mumu_adb(self, custom_path: str = "") -> Optional[Path]:
        """查找 MuMu 自带的 adb"""
        search_paths = []
        if custom_path:
            custom = Path(custom_path)
            if custom.suffix.lower() == ".exe":
                search_paths.append(custom.parent.parent if custom.parent.name.lower() == "nx_main" else custom.parent)
            elif custom.name.lower() == "nx_main":
                search_paths.append(custom.parent)
            search_paths.append(custom)
        search_paths.extend(self.MUMU12_DEFAULT_PATHS)

        seen = set()
        for base_path in search_paths:
            key = str(base_path).casefold()
            if key in seen:
                continue
            seen.add(key)
            for sub in ("nx_device/12.0/shell", "nx_device/shell", "shell", "nx_main"):
                adb_path = base_path / sub / "adb.exe"
                if adb_path.exists():
                    logger.info(f"找到 MuMu adb: {adb_path}")
                    return adb_path
        return None
    
    def connect_mumu(self, instance: int = 0, host: str = "127.0.0.1") -> Tuple[bool, str]:
        """
        连接到 MuMu 模拟器
        
        Args:
            instance: 实例编号 (0, 1, 2...)
            host: 主机地址
            
        Returns:
            (success, message)
        """
        port = self.MUMU_PORTS.get(instance, 5555 + instance)
        return self.adb.connect(host, port)
    
    def check_mumu_running(self) -> bool:
        """检查 MuMu 模拟器是否在运行"""
        devices = self.adb.devices()
        for device in devices:
            if device['status'] == 'device':
                return True
        return False

    def find_mumu_exe(self, custom_path: str = "") -> Optional[Path]:
        """查找 MuMu 主程序 exe（优先 MuMuManager，其次 MuMuNxMain）"""
        search_paths = []
        if custom_path:
            search_paths.append(Path(custom_path))
        search_paths.extend(self.MUMU12_DEFAULT_PATHS)

        for base in search_paths:
            for exe in self._mumu_exe_candidates(base):
                if exe.exists():
                    logger.info(f"找到 MuMu 主程序: {exe}")
                    return exe
        return None

    def launch_mumu(self, custom_path: str = "", wait_seconds: int = 60,
                    app_package: str = "", host: str = "127.0.0.1", port: int = 5555,
                    candidate_ports: Optional[list[int]] = None) -> bool:
        """
        启动 MuMu 模拟器，等待就绪后关闭广告并打开指定 APP。
        优先使用 MuMuManager control launch 启动实例，回退到直接启动主程序。
        
        Args:
            custom_path: MuMu 安装路径或 exe 路径
            wait_seconds: 等待超时秒数
            app_package: 启动后要打开的 APP 包名
            host: 模拟器主机地址
            port: 主 ADB 端口
            candidate_ports: 额外尝试的 ADB 端口列表（如 [7555, 5555]）
        """
        search_paths = [Path(custom_path)] if custom_path else []
        search_paths.extend(self.MUMU12_DEFAULT_PATHS)

        # 优先用 MuMuManager 启动实例（更可靠）
        manager = None
        for base in search_paths:
            for candidate in self._mumu_exe_candidates(base):
                if candidate.name.lower() == "mumumanager.exe" and candidate.exists():
                    manager = candidate
                    break
            if manager:
                break

        if manager:
            logger.info(f"使用 MuMuManager 启动实例: {manager}")
            try:
                subprocess.run([str(manager), "control", "-v", "0", "launch"],
                               cwd=str(manager.parent), timeout=10,
                               creationflags=_SUBPROCESS_FLAGS)
            except Exception as e:
                logger.warning(f"MuMuManager 启动失败: {e}，回退到直接启动")
                manager = None

        if not manager:
            exe = self.find_mumu_exe(custom_path)
            if not exe:
                logger.error("未找到 MuMu 主程序，请在设置中配置安装路径")
                return False
            logger.info(f"直接启动 MuMu: {exe}")
            try:
                subprocess.Popen([str(exe)], cwd=str(exe.parent),
                                 creationflags=_SUBPROCESS_FLAGS)
            except Exception as e:
                logger.error(f"启动 MuMu 失败: {e}")
                return False

        # 等待设备状态变为 device（在多个端口中探测）
        candidates = [port]
        if candidate_ports:
            for p in candidate_ports:
                if p not in candidates:
                    candidates.append(p)
        target_addrs = [f"{host}:{p}" for p in candidates]
        addrs_str = ", ".join(target_addrs)
        logger.info(f"等待 MuMu 启动（最多 {wait_seconds} 秒，探测端口: {addrs_str}）...")
        for i in range(wait_seconds):
            _time.sleep(1)
            devices = self.adb.devices()
            for addr in target_addrs:
                if any(d['serial'] == addr and d['status'] == 'device' for d in devices):
                    logger.info(f"MuMu 已就绪（{i + 1}秒），设备: {addr}")
                    break
            else:
                # 没找到任何就绪设备，继续等待
                if i % 5 == 4:
                    for addr in target_addrs:
                        self.adb._run_command(["connect", addr])
                continue
            break  # 找到就绪设备，跳出外层循环
        else:
            logger.warning(f"MuMu 启动超时，未检测到就绪设备（探测端口: {addrs_str}）")
            return False

        # 关闭广告：返回桌面
        if manager:
            try:
                _time.sleep(2)
                subprocess.run([str(manager), "control", "-v", "0", "tool", "func", "-n", "go_home"],
                               cwd=str(manager.parent), timeout=5,
                               creationflags=_SUBPROCESS_FLAGS)
                logger.info("已关闭广告（返回桌面）")
            except Exception as e:
                logger.warning(f"关闭广告失败: {e}")

        # 启动目标 APP
        if app_package and manager:
            try:
                _time.sleep(1)
                subprocess.run([str(manager), "control", "-v", "0", "app", "launch",
                                "-pkg", app_package],
                               cwd=str(manager.parent), timeout=10,
                               creationflags=_SUBPROCESS_FLAGS)
                logger.info(f"已启动 APP: {app_package}")
            except Exception as e:
                logger.warning(f"启动 APP 失败: {e}")

        return True


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    adb = ADBHelper()
    
    # 检查 adb 版本
    version = adb.version()
    print(f"ADB 版本: {version}")
    
    # 列出设备
    devices = adb.devices()
    print(f"已连接设备: {devices}")
    
    # 检查 MuMu
    mumu = MuMuHelper(adb)
    adb_path = mumu.find_mumu_adb()
    print(f"MuMu adb: {adb_path}")
