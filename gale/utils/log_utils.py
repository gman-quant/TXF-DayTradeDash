
import logging
import sys

def setup_logger(name, level=logging.INFO):
    """
    配置統一的 Logger 格式。
    Configures a unified logger format for all modules.
    """
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False # [Fix] Prevent duplicates if Root has handlers
    
    # 避免重複添加 Handler
    if not logger.handlers:
        logger.addHandler(handler)
        
    return logger
