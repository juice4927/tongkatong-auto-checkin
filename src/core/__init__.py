# src/core/__init__.py
from .config import ConfigManager
from .automator import UIAutomator2Impl, CheckinAction
from .scheduler import CheckinScheduler
from .holiday import HolidayChecker