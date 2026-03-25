#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场状态检测模块 - 主动查询 Polymarket API 获取市场真实状态
严格遵循 Polymarket 官方 API 文档: https://docs.polymarket.com/

参数内嵌版本 - 不依赖外部配置文件
线程安全设计 - 所有文件操作通过统一的锁管理
"""

import requests
import time
import json
import threading
import logging
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List, Set
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


# =============================================================================
# 内嵌配置参数 - 不依赖外部 config 文件
# =============================================================================
class Config:
    """内嵌配置参数"""
    # API 端点（官方文档规范）
    GAMMA_API_BASE = "https://gamma-api.polymarket.com"
    CLOB_API_BASE = "https://clob.polymarket.com"
    
    # 缓存配置
    MARKET_STATE_CACHE_TTL = 300  # 5分钟缓存
    
    # 请求超时
    GAMMA_API_TIMEOUT = 10  # Gamma API 查询超时
    CLOB_BOOK_TIMEOUT = 5   # CLOB Book 查询超时
    
    # 重试配置
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY_BASE = 1.0  # 指数退避基础秒数
    
    # 流动性判断阈值
    MIN_LIQUIDITY_BIDS = 1  # 最少买单数量
    MIN_LIQUIDITY_ASKS = 1  # 最少卖单数量
    
    # 文件路径（相对于工作目录）
    EXIT_TOKENS_FILE = "exit_tokens.json"
    COPYTRADE_TOKEN_FILE = "tokens_from_copytrade.json"
    COPYTRADE_STATE_FILE = "copytrade_state.json"
    
    # 线程安全 - 文件操作锁
    # 注意：这个锁需要与主程序的文件锁共享，通过外部注入
    _file_lock: Optional[threading.RLock] = None
    
    @classmethod
    def get_file_lock(cls) -> threading.RLock:
        """获取文件操作锁（懒加载）"""
        if cls._file_lock is None:
            cls._file_lock = threading.RLock()
        return cls._file_lock
    
    @classmethod
    def set_file_lock(cls, lock: threading.RLock):
        """设置外部传入的文件锁（与主程序共享）"""
        cls._file_lock = lock


# =============================================================================
# 市场状态枚举和数据类
# =============================================================================
class MarketStatus(Enum):
    """市场状态枚举 - 严格对应官方 API 返回值"""
    ACTIVE = "active"              # 市场活跃，正常交易
    LOW_LIQUIDITY = "low_liquidity"  # 市场活跃但流动性极低
    CLOSED = "closed"              # 市场已关闭（等待结算）
    RESOLVED = "resolved"          # 市场已结算（有最终结果）
    ARCHIVED = "archived"          # 市场已归档
    NOT_FOUND = "not_found"        # 市场不存在（404）
    API_ERROR = "api_error"        # API 查询失败（网络等问题）
    
    # 衍生状态（内部使用）
    PERMANENTLY_CLOSED = "permanently_closed"  # 永久关闭（resolved/archived/not_found）


@dataclass
class MarketState:
    """市场状态数据类 - 可序列化"""
    status: MarketStatus
    condition_id: str
    token_id: str
    data: Dict[str, Any]
    checked_at: float
    is_tradeable: bool = False
    refillable: bool = False
    http_status: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 JSON 序列化）"""
        return {
            "status": self.status.value,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "data": self.data,
            "checked_at": self.checked_at,
            "is_tradeable": self.is_tradeable,
            "refillable": self.refillable,
            "http_status": self.http_status,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MarketState":
        """从字典创建"""
        return cls(
            status=MarketStatus(d.get("status", "api_error")),
            condition_id=d.get("condition_id", ""),
            token_id=d.get("token_id", ""),
            data=d.get("data", {}),
            checked_at=d.get("checked_at", 0),
            is_tradeable=d.get("is_tradeable", False),
            refillable=d.get("refillable", False),
            http_status=d.get("http_status"),
        )
    
    @property
    def is_permanently_closed(self) -> bool:
        """是否永久关闭（不可回填）"""
        return self.status in {
            MarketStatus.NOT_FOUND,
            MarketStatus.CLOSED,  # 【修复】添加 CLOSED
            MarketStatus.RESOLVED,
            MarketStatus.ARCHIVED,
            MarketStatus.PERMANENTLY_CLOSED,
        }
    
    @property
    def needs_book_probe(self) -> bool:
        """是否需要进一步 Book 探针（低流动性检测）"""
        return self.status == MarketStatus.ACTIVE


# =============================================================================
# 市场状态检测器
# =============================================================================
class MarketStateChecker:
    """
    市场状态检测器
    
    严格遵循 Polymarket 官方 API 文档：
    - Gamma API: 查询市场基本信息（active/closed/resolved/archived）
    - CLOB API: 查询订单簿深度（流动性检测）
    
    线程安全：所有方法都可从任意线程调用
    """
    
    def __init__(self, file_lock: Optional[threading.RLock] = None):
        """
        初始化检测器
        
        Args:
            file_lock: 外部传入的文件锁（与主程序共享）
        """
        # 设置文件锁
        if file_lock:
            Config.set_file_lock(file_lock)
        
        # 缓存和会话
        self._cache: Dict[str, Tuple[MarketState, float]] = {}
        self._cache_lock = threading.RLock()
        
        # 请求会话（连接复用）
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "PolymarketMaker/1.0 (MarketStateChecker)",
        })
        
        # 统计数据
        self._stats = {
            "total_checks": 0,
            "cache_hits": 0,
            "api_errors": 0,
            "markets_closed": 0,
        }
        self._stats_lock = threading.Lock()
    
    def check_market_state(
        self,
        condition_id: str,
        token_id: str,
        use_cache: bool = True,
        force_refresh: bool = False
    ) -> MarketState:
        """
        检测市场状态 - 主入口
        
        流程：
        1. 检查缓存
        2. 查询 Gamma API（市场基本信息）
        3. 如活跃，查询 CLOB Book（流动性检测）
        4. 更新缓存
        
        Args:
            condition_id: 市场条件ID（来自 copytrade 文件）
            token_id: Token ID
            use_cache: 是否使用缓存
            force_refresh: 强制刷新（忽略缓存）
        
        Returns:
            MarketState 对象
        """
        cache_key = f"{condition_id}:{token_id}"
        now = time.time()
        
        with self._stats_lock:
            self._stats["total_checks"] += 1
        
        # 检查缓存
        if use_cache and not force_refresh:
            with self._cache_lock:
                if cache_key in self._cache:
                    cached_state, cached_at = self._cache[cache_key]
                    if now - cached_at < Config.MARKET_STATE_CACHE_TTL:
                        with self._stats_lock:
                            self._stats["cache_hits"] += 1
                        logger.debug(
                            f"[STATE_CHECK] 缓存命中: {token_id[:16]}... "
                            f"status={cached_state.status.value}"
                        )
                        return cached_state
        
        # 查询 Gamma API
        state = self._query_gamma_api_with_retry(condition_id, token_id)
        
        # 如活跃，进一步检查流动性
        if state.needs_book_probe:
            state = self._check_liquidity(state, token_id)
        
        # 更新缓存
        with self._cache_lock:
            self._cache[cache_key] = (state, now)
        
        # 统计
        if state.is_permanently_closed:
            with self._stats_lock:
                self._stats["markets_closed"] += 1
        
        return state
    
    def _query_gamma_api_with_retry(
        self,
        condition_id: str,
        token_id: str
    ) -> MarketState:
        """带重试的 Gamma API 查询"""
        last_error = None
        
        for attempt in range(Config.MAX_RETRY_ATTEMPTS):
            try:
                return self._query_gamma_api(condition_id, token_id)
            except (requests.exceptions.RequestException, Exception) as e:
                last_error = e
                logger.warning(
                    f"[STATE_CHECK] Gamma API 查询失败 (尝试 {attempt+1}/"
                    f"{Config.MAX_RETRY_ATTEMPTS}): {condition_id[:16]}..., error={e}"
                )
                if attempt < Config.MAX_RETRY_ATTEMPTS - 1:
                    time.sleep(Config.RETRY_DELAY_BASE * (2 ** attempt))
        
        # 所有重试失败
        with self._stats_lock:
            self._stats["api_errors"] += 1
        
        now = time.time()
        return MarketState(
            status=MarketStatus.API_ERROR,
            condition_id=condition_id,
            token_id=token_id,
            data={"error": str(last_error), "retries": Config.MAX_RETRY_ATTEMPTS},
            checked_at=now,
            is_tradeable=False,
            refillable=True,  # API 错误时保守处理，允许重试
        )
    
    def _query_gamma_api(
        self,
        condition_id: str,
        token_id: str
    ) -> MarketState:
        """
        查询 Gamma API
        
        官方端点: GET https://gamma-api.polymarket.com/markets/{condition_id}
        """
        url = f"{Config.GAMMA_API_BASE}/markets/{condition_id}"
        now = time.time()
        
        resp = self._session.get(url, timeout=Config.GAMMA_API_TIMEOUT)
        
        # 处理 404 - 市场不存在
        if resp.status_code == 404:
            logger.warning(
                f"[STATE_CHECK] 市场不存在 (404): {condition_id[:16]}..."
            )
            return MarketState(
                status=MarketStatus.NOT_FOUND,
                condition_id=condition_id,
                token_id=token_id,
                data={"gamma_api_status": 404},
                checked_at=now,
                is_tradeable=False,
                refillable=False,
                http_status=404,
            )
        
        resp.raise_for_status()
        data = resp.json()
        
        # 解析市场状态
        return self._parse_gamma_response(data, condition_id, token_id)
    
    def _parse_gamma_response(
        self,
        data: Dict[str, Any],
        condition_id: str,
        token_id: str
    ) -> MarketState:
        """解析 Gamma API 响应"""
        now = time.time()
        
        # 官方字段映射（根据文档）
        is_archived = data.get("archived") or data.get("isArchived") or data.get("is_archived")
        is_resolved = data.get("resolved") or data.get("isResolved") or data.get("is_resolved")
        is_closed = data.get("closed") or data.get("isClosed") or data.get("is_closed")
        is_active = data.get("active") or data.get("isActive") or data.get("is_active")
        
        # 按优先级判断状态
        if is_archived:
            return MarketState(
                status=MarketStatus.ARCHIVED,
                condition_id=condition_id,
                token_id=token_id,
                data=data,
                checked_at=now,
                is_tradeable=False,
                refillable=False,
                http_status=200,
            )
        
        if is_resolved:
            return MarketState(
                status=MarketStatus.RESOLVED,
                condition_id=condition_id,
                token_id=token_id,
                data=data,
                checked_at=now,
                is_tradeable=False,
                refillable=False,
                http_status=200,
            )
        
        if is_closed:
            return MarketState(
                status=MarketStatus.CLOSED,
                condition_id=condition_id,
                token_id=token_id,
                data=data,
                checked_at=now,
                is_tradeable=False,
                refillable=False,
                http_status=200,
            )
        
        if is_active:
            # 市场活跃，需要进一步检查流动性
            return MarketState(
                status=MarketStatus.ACTIVE,
                condition_id=condition_id,
                token_id=token_id,
                data=data,
                checked_at=now,
                is_tradeable=True,
                refillable=True,
                http_status=200,
            )
        
        # 未知状态，保守处理
        logger.warning(
            f"[STATE_CHECK] 未知市场状态: {condition_id[:16]}..., "
            f"data_keys={list(data.keys())}"
        )
        return MarketState(
            status=MarketStatus.API_ERROR,
            condition_id=condition_id,
            token_id=token_id,
            data={"unknown_state": True, **data},
            checked_at=now,
            is_tradeable=False,
            refillable=True,
            http_status=200,
        )
    
    def _check_liquidity(
        self,
        state: MarketState,
        token_id: str
    ) -> MarketState:
        """
        检查市场流动性 - 查询 CLOB Book
        
        官方端点: GET https://clob.polymarket.com/book?token_id={token_id}
        """
        url = f"{Config.CLOB_API_BASE}/book"
        now = time.time()
        
        try:
            resp = self._session.get(
                url,
                params={"token_id": token_id},
                timeout=Config.CLOB_BOOK_TIMEOUT,
            )
            
            # Book 404 - 市场活跃但无订单簿（极低流动性）
            if resp.status_code == 404:
                logger.warning(
                    f"[STATE_CHECK] Book 404 但市场活跃: {token_id[:16]}..., "
                    "标记为低流动性"
                )
                return MarketState(
                    status=MarketStatus.LOW_LIQUIDITY,
                    condition_id=state.condition_id,
                    token_id=token_id,
                    data={**state.data, "book_status": 404, "book_empty": True},
                    checked_at=now,
                    is_tradeable=True,
                    refillable=True,
                    http_status=404,
                )
            
            resp.raise_for_status()
            book_data = resp.json()
            
            # 分析订单簿深度
            bids = book_data.get("bids", [])
            asks = book_data.get("asks", [])
            
            if len(bids) < Config.MIN_LIQUIDITY_BIDS and len(asks) < Config.MIN_LIQUIDITY_ASKS:
                # 空订单簿或流动性极低
                return MarketState(
                    status=MarketStatus.LOW_LIQUIDITY,
                    condition_id=state.condition_id,
                    token_id=token_id,
                    data={
                        **state.data,
                        "book_status": 200,
                        "bids_count": len(bids),
                        "asks_count": len(asks),
                    },
                    checked_at=now,
                    is_tradeable=True,
                    refillable=True,
                    http_status=200,
                )
            
            # 正常流动性
            return MarketState(
                status=MarketStatus.ACTIVE,
                condition_id=state.condition_id,
                token_id=token_id,
                data={
                    **state.data,
                    "book_status": 200,
                    "bids_count": len(bids),
                    "asks_count": len(asks),
                },
                checked_at=now,
                is_tradeable=True,
                refillable=True,
                http_status=200,
            )
            
        except requests.exceptions.RequestException as e:
            logger.warning(
                f"[STATE_CHECK] Book 查询异常: {token_id[:16]}..., error={e}"
            )
            # Book 查询失败时，保持原状态（依赖 Gamma API 结果）
            return state
    
    def invalidate_cache(self, condition_id: str, token_id: str):
        """使缓存失效"""
        cache_key = f"{condition_id}:{token_id}"
        with self._cache_lock:
            self._cache.pop(cache_key, None)
    
    def clear_cache(self):
        """清空缓存"""
        with self._cache_lock:
            self._cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._cache_lock:
            cache_size = len(self._cache)
        with self._stats_lock:
            return {
                **self._stats,
                "cache_size": cache_size,
                "cache_ttl": Config.MARKET_STATE_CACHE_TTL,
            }


# =============================================================================
# 文件清理工具 - 线程安全
# =============================================================================
class MarketClosedCleaner:
    """
    市场关闭清理器 - 从所有 JSON 文件中删除已关闭市场的数据
    
    线程安全设计：所有文件操作通过统一的锁管理
    """
    
    def __init__(self, file_lock: Optional[threading.RLock] = None):
        if file_lock:
            Config.set_file_lock(file_lock)
        self._file_lock = Config.get_file_lock()
    
    def clean_closed_market(
        self,
        token_id: str,
        condition_id: str,
        exit_reason: str,
        copytrade_file: Optional[str] = None,
        copytrade_state_file: Optional[str] = None,
        exit_tokens_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        清理已关闭市场的所有数据
        
        Args:
            token_id: Token ID
            condition_id: Condition ID
            exit_reason: 退出原因
            copytrade_file: copytrade token 文件路径
            copytrade_state_file: copytrade state 文件路径
            exit_tokens_file: exit tokens 文件路径
        
        Returns:
            清理结果统计
        """
        result = {
            "token_id": token_id,
            "condition_id": condition_id,
            "exit_reason": exit_reason,
            "cleaned_files": [],
            "errors": [],
        }
        
        with self._file_lock:
            try:
                # 1. 清理 copytrade token 文件
                if copytrade_file:
                    self._remove_from_copytrade_tokens(copytrade_file, token_id, result)
                
                # 2. 清理 copytrade state 文件
                if copytrade_state_file:
                    self._remove_from_copytrade_state(copytrade_state_file, token_id, result)
                
                # 3. 更新 exit_tokens 文件（标记为不可回填）
                if exit_tokens_file:
                    self._update_exit_tokens(exit_tokens_file, token_id, exit_reason, result)
                
                logger.info(
                    f"[CLEANER] 已清理关闭市场: {token_id[:16]}..., "
                    f"files={result['cleaned_files']}"
                )
                
            except Exception as e:
                logger.error(f"[CLEANER] 清理失败: {token_id[:16]}..., error={e}")
                result["errors"].append(str(e))
        
        return result
    
    def _remove_from_copytrade_tokens(
        self,
        filepath: str,
        token_id: str,
        result: Dict[str, Any]
    ):
        """从 copytrade tokens 文件中删除 token"""
        try:
            path = Path(filepath)
            if not path.exists():
                return
            
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            removed = 0
            
            # 【修复】支持 {"tokens": [...]} 格式（标准 copytrade 格式）
            if isinstance(data, dict) and "tokens" in data:
                original_count = len(data["tokens"])
                data["tokens"] = [
                    item for item in data["tokens"]
                    if self._extract_token_id(item) != token_id
                ]
                removed = original_count - len(data["tokens"])
                if removed > 0:
                    data["updated_at"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    )
            # 支持纯列表格式
            elif isinstance(data, list):
                original_len = len(data)
                new_data = [
                    item for item in data
                    if self._extract_token_id(item) != token_id
                ]
                removed = original_len - len(new_data)
                data = new_data
            # 支持纯字典格式（token_id 为键）
            elif isinstance(data, dict):
                original_len = len(data)
                new_data = {
                    k: v for k, v in data.items()
                    if k != token_id and self._extract_token_id(v) != token_id
                }
                removed = original_len - len(new_data)
                data = new_data
            
            if removed > 0:
                # 写回文件
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                result["cleaned_files"].append(f"{filepath} ({removed} items)")
                logger.info(f"[CLEANER] 从 {filepath} 删除 {removed} 个条目")
                
        except Exception as e:
            logger.error(f"[CLEANER] 清理 {filepath} 失败: {e}")
            result["errors"].append(f"{filepath}: {e}")
    
    def _remove_from_copytrade_state(
        self,
        filepath: str,
        token_id: str,
        result: Dict[str, Any]
    ):
        """从 copytrade state 文件中删除 token"""
        try:
            path = Path(filepath)
            if not path.exists():
                return
            
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            removed = 0
            
            # 【修复】支持 {"targets": {...}} 格式
            if isinstance(data, dict) and "targets" in data and isinstance(data["targets"], dict):
                original_len = len(data["targets"])
                if token_id in data["targets"]:
                    del data["targets"][token_id]
                    removed = 1
            # 支持纯字典格式
            elif isinstance(data, dict):
                original_len = len(data)
                if token_id in data:
                    del data[token_id]
                    removed = 1
            
            if removed > 0:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                result["cleaned_files"].append(f"{filepath} ({removed} items)")
                
        except Exception as e:
            logger.error(f"[CLEANER] 清理 {filepath} 失败: {e}")
            result["errors"].append(f"{filepath}: {e}")
    
    def _update_exit_tokens(
        self,
        filepath: str,
        token_id: str,
        exit_reason: str,
        result: Dict[str, Any]
    ):
        """更新 exit_tokens 文件，标记为永久关闭"""
        try:
            path = Path(filepath)
            
            # 读取现有记录
            records = []
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            
            # 查找并更新记录
            updated = False
            for record in records:
                if record.get("token_id") == token_id:
                    record["exit_reason"] = exit_reason
                    record["refillable"] = False
                    record["market_permanently_closed"] = True
                    record["cleaned_at"] = time.time()
                    updated = True
                    break
            
            if not updated:
                # 添加新记录
                records.append({
                    "token_id": token_id,
                    "exit_reason": exit_reason,
                    "refillable": False,
                    "market_permanently_closed": True,
                    "exit_ts": time.time(),
                    "cleaned_at": time.time(),
                })
            
            with open(path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            
            result["cleaned_files"].append(filepath)
            
        except Exception as e:
            logger.error(f"[CLEANER] 更新 {filepath} 失败: {e}")
            result["errors"].append(f"{filepath}: {e}")
    
    def _extract_token_id(self, item: Any) -> Optional[str]:
        """从条目提取 token_id"""
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return item.get("token_id") or item.get("asset_id")
        return None


# =============================================================================
# 便捷函数和单例
# =============================================================================
_state_checker_instance: Optional[MarketStateChecker] = None
_cleaner_instance: Optional[MarketClosedCleaner] = None
_init_lock = threading.Lock()


def init_market_state_checker(file_lock: Optional[threading.RLock] = None) -> MarketStateChecker:
    """初始化市场状态检测器（线程安全单例）"""
    global _state_checker_instance
    with _init_lock:
        if _state_checker_instance is None:
            _state_checker_instance = MarketStateChecker(file_lock=file_lock)
        return _state_checker_instance


def get_market_state_checker() -> MarketStateChecker:
    """获取市场状态检测器实例"""
    if _state_checker_instance is None:
        raise RuntimeError("MarketStateChecker not initialized. Call init_market_state_checker() first.")
    return _state_checker_instance


def init_cleaner(file_lock: Optional[threading.RLock] = None) -> MarketClosedCleaner:
    """初始化清理器（线程安全单例）"""
    global _cleaner_instance
    with _init_lock:
        if _cleaner_instance is None:
            _cleaner_instance = MarketClosedCleaner(file_lock=file_lock)
        return _cleaner_instance


def get_cleaner() -> MarketClosedCleaner:
    """获取清理器实例"""
    if _cleaner_instance is None:
        raise RuntimeError("MarketClosedCleaner not initialized. Call init_cleaner() first.")
    return _cleaner_instance


# 便捷函数
def check_market_state(
    condition_id: str,
    token_id: str,
    use_cache: bool = True
) -> MarketState:
    """便捷函数：检测市场状态"""
    checker = get_market_state_checker()
    return checker.check_market_state(condition_id, token_id, use_cache=use_cache)


def clean_closed_market(
    token_id: str,
    condition_id: str,
    exit_reason: str,
    **kwargs
) -> Dict[str, Any]:
    """便捷函数：清理已关闭市场"""
    cleaner = get_cleaner()
    return cleaner.clean_closed_market(token_id, condition_id, exit_reason, **kwargs)


def should_refill_token(
    token_id: str,
    condition_id: str,
    exit_reason: str,
    previous_state: Optional[Dict] = None
) -> Tuple[bool, str]:
    """
    判断 Token 是否应该回填
    
    Returns:
        (should_refill, reason)
    """
    # 永久关闭的原因，直接拒绝
    permanent_reasons = {
        "MARKET_CLOSED", "MARKET_RESOLVED", "MARKET_ARCHIVED",
        "MARKET_NOT_FOUND", "MARKET_CLOSED_REVISED", "BOOK_PROBE_FAILED"
    }
    
    if exit_reason in permanent_reasons:
        return False, f"permanent_exit_reason: {exit_reason}"
    
    # 需要重新验证的原因
    if exit_reason in ("NO_DATA_TIMEOUT", "WS_TIMEOUT", "API_ERROR"):
        # 强制刷新市场状态
        state = check_market_state(condition_id, token_id, use_cache=False)
        
        if state.is_permanently_closed:
            return False, f"market_permanently_closed: {state.status.value}"
        
        if state.status == MarketStatus.LOW_LIQUIDITY:
            return True, f"low_liquidity_allowed: {state.data.get('bids_count', 0)}b/{state.data.get('asks_count', 0)}a"
        
        return True, f"market_active: {state.status.value}"
    
    # 其他临时原因，默认允许
    return True, f"temporary_exit_allowed: {exit_reason}"
