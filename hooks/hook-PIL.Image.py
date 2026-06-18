# 通卡通只需要基础截图/模板图片格式，覆盖 PyInstaller 默认的 Pillow 全量插件收集，
# 避免把几十种冷门图片格式全部打进包里。
hiddenimports = [
    "PIL.BmpImagePlugin",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
]
