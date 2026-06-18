"""
图像识别模块 - OCR 兜底 + 模板匹配
作为 Accessibility 识别失败时的兜底手段
"""
import logging
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 延迟导入，避免启动时加载重型依赖
_rapidocr = None
_cv2 = None
_np = None
_PIL_Image = None


def _ensure_ocr():
    """延迟导入 OCR 库"""
    global _rapidocr
    if _rapidocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapidocr = RapidOCR()
        except ImportError:
            logger.warning("rapidocr_onnxruntime 未安装，OCR 功能不可用")
            _rapidocr = False  # 标记为不可用
    return _rapidocr if _rapidocr else None


def _ensure_cv2():
    """延迟导入 OpenCV"""
    global _cv2, _np
    if _cv2 is None:
        try:
            import cv2
            import numpy as np
            _cv2 = cv2
            _np = np
        except ImportError:
            logger.warning("opencv-python 未安装，模板匹配功能不可用")
            _cv2 = False
    return _cv2 if _cv2 else None


def _ensure_pil():
    """延迟导入 Pillow"""
    global _PIL_Image
    if _PIL_Image is None:
        try:
            from PIL import Image
            _PIL_Image = Image
        except ImportError:
            logger.warning("Pillow 未安装，图像处理功能不可用")
            _PIL_Image = False
    return _PIL_Image if _PIL_Image else None


@dataclass
class OcrResult:
    """OCR 识别结果"""
    text: str
    confidence: float
    box: Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int], Tuple[int, int]]  # 四点坐标


@dataclass
class TemplateMatchResult:
    """模板匹配结果"""
    location: Tuple[int, int]  # 中心点 (x, y)
    confidence: float
    width: int
    height: int


def ocr_recognize(image, region: Optional[Tuple[int, int, int, int]] = None,
                  keywords: Optional[List[str]] = None) -> List[OcrResult]:
    """
    OCR 文本识别
    
    Args:
        image: PIL Image 或 numpy array
        region: 可选的裁剪区域 (x, y, width, height)
        keywords: 可选的关键字过滤，只返回包含这些关键字的结果
    
    Returns:
        OCR 识别结果列表，按置信度降序排列
    """
    engine = _ensure_ocr()
    if engine is None:
        return []
    
    pil_image = _ensure_pil()
    if pil_image is None:
        return []
    
    try:
        # 确保是 PIL Image
        if not isinstance(image, pil_image.Image):
            image = pil_image.fromarray(image)
        
        # 裁剪区域
        if region:
            image = image.crop(region)
        
        # 转换为 numpy array 供 OCR 使用
        import numpy as np
        img_array = np.array(image)
        
        # 执行 OCR
        result, _ = engine(img_array)
        
        if not result:
            return []
        
        # 解析结果
        ocr_results = []
        for item in result:
            box, text, confidence = item
            # 如果指定了关键字，进行过滤
            if keywords and not any(kw in text for kw in keywords):
                continue
            ocr_results.append(OcrResult(
                text=text,
                confidence=confidence,
                box=box
            ))
        
        # 按置信度降序
        ocr_results.sort(key=lambda r: r.confidence, reverse=True)
        return ocr_results
    
    except Exception as e:
        logger.warning(f"OCR 识别失败: {e}")
        return []


def template_match(image, template_path: str,
                   threshold: float = 0.8,
                   multi_scale: bool = True) -> List[TemplateMatchResult]:
    """
    图像模板匹配
    
    Args:
        image: PIL Image 或 numpy array
        template_path: 模板图片路径
        threshold: 匹配阈值 (0.0-1.0)
        multi_scale: 是否使用多尺度匹配（适配不同分辨率）
    
    Returns:
        匹配结果列表，按置信度降序排列
    """
    cv2 = _ensure_cv2()
    if cv2 is None:
        return []
    
    pil_image = _ensure_pil()
    if pil_image is None:
        return []
    
    try:
        # 转换为灰度图
        if isinstance(image, pil_image.Image):
            import numpy as np
            img_array = np.array(image)
        else:
            img_array = image
        
        # 检查模板是否存在
        template_path = Path(template_path)
        if not template_path.exists():
            logger.warning(f"模板文件不存在: {template_path}")
            return []
        
        template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            logger.warning(f"无法读取模板文件: {template_path}")
            return []
        
        # 转换为灰度图
        if len(img_array.shape) == 3:
            img_gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            img_gray = img_array
        
        results = []
        
        if multi_scale:
            # 多尺度匹配：尝试不同缩放比例
            scales = [0.8, 0.9, 1.0, 1.1, 1.2]
            for scale in scales:
                scaled_template = cv2.resize(template, None, fx=scale, fy=scale,
                                            interpolation=cv2.INTER_AREA)
                h, w = scaled_template.shape
                
                if h > img_gray.shape[0] or w > img_gray.shape[1]:
                    continue
                
                result = cv2.matchTemplate(img_gray, scaled_template, cv2.TM_CCOEFF_NORMED)
                locations = _np.where(result >= threshold)
                
                for pt in zip(*locations[::-1]):
                    confidence = result[pt[1], pt[0]]
                    center_x = pt[0] + w // 2
                    center_y = pt[1] + h // 2
                    results.append(TemplateMatchResult(
                        location=(center_x, center_y),
                        confidence=confidence,
                        width=w,
                        height=h
                    ))
        else:
            # 单尺度匹配
            h, w = template.shape
            result = cv2.matchTemplate(img_gray, template, cv2.TM_CCOEFF_NORMED)
            locations = _np.where(result >= threshold)
            
            for pt in zip(*locations[::-1]):
                confidence = result[pt[1], pt[0]]
                center_x = pt[0] + w // 2
                center_y = pt[1] + h // 2
                results.append(TemplateMatchResult(
                    location=(center_x, center_y),
                    confidence=confidence,
                    width=w,
                    height=h
                ))
        
        # 去重（保留置信度最高的）
        if results:
            results.sort(key=lambda r: r.confidence, reverse=True)
            # 简单去重：保留置信度最高的前 3 个
            results = results[:3]
        
        return results
    
    except Exception as e:
        logger.warning(f"模板匹配失败: {e}")
        return []


def find_text_by_ocr(image, text: str, region: Optional[Tuple[int, int, int, int]] = None,
                     min_confidence: float = 0.6) -> Optional[Tuple[int, int]]:
    """
    通过 OCR 查找指定文本的位置
    
    Args:
        image: PIL Image 或 numpy array
        text: 要查找的文本
        region: 可选的裁剪区域
        min_confidence: 最低置信度
    
    Returns:
        文本中心点坐标 (x, y)，未找到返回 None
    """
    results = ocr_recognize(image, region=region, keywords=[text])
    
    for result in results:
        if text in result.text and result.confidence >= min_confidence:
            # 计算文本框中心点
            box = result.box
            center_x = int(sum(p[0] for p in box) / 4)
            center_y = int(sum(p[1] for p in box) / 4)
            
            # 如果指定了 region，需要加上偏移
            if region:
                center_x += region[0]
                center_y += region[1]
            
            return (center_x, center_y)
    
    return None
