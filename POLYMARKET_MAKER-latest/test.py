#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Polymarket 仓位查询辅助脚本。"""
from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from Volatility_arbitrage_run import (
    _extract_avg_price_from_entry,
    _extract_position_size_from_entry,
    _fetch_positions_from_data_api,
    _normalize_wallet_address,
    _position_dict_candidates,
    _position_matches_token,
)


def _extract_token_identifier(entry: Dict[str, Any]) -> Optional[str]:
    """尽可能从仓位条目中提取 tokenId/assetId."""

    if not isinstance(entry, dict):
        return None

    keys = (
        "tokenId",
        "token_id",
        "clobTokenId",
        "clob_token_id",
        "assetId",
        "asset_id",
        "asset",
        "id",
    )
    for cand in _position_dict_candidates(entry):
        for key in keys:
            val = cand.get(key)
            if val is None:
                continue
            text = str(val).strip()
            if text:
                return text
    return None


def _extract_market_metadata(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """提取市场标题、结果标签及状态信息，便于解释来源。"""

    market_title: Optional[str] = None
    outcome_label: Optional[str] = None
    status_text: Optional[str] = None

    def _clean(text: Any) -> Optional[str]:
        if text is None:
            return None
        if isinstance(text, str):
            stripped = text.strip()
            return stripped or None
        return str(text)

    for cand in _position_dict_candidates(entry):
        if market_title is None:
            market = cand.get("market") or cand.get("marketInfo") or cand.get("market_data")
            if isinstance(market, dict):
                for key in ("title", "question", "name", "slug"):
                    market_title = _clean(market.get(key))
                    if market_title:
                        break
            market_title = market_title or _clean(cand.get("market"))

        if outcome_label is None:
            for key in ("outcome", "outcomeLabel", "outcome_name", "side", "positionType"):
                outcome_label = _clean(cand.get(key))
                if outcome_label:
                    break
            if outcome_label is None:
                outcome = cand.get("outcomeToken") or cand.get("outcome_token")
                if isinstance(outcome, dict):
                    for key in ("name", "title", "outcome", "label"):
                        outcome_label = _clean(outcome.get(key))
                        if outcome_label:
                            break

        if status_text is None:
            status_sources = [
                cand.get("status"),
                cand.get("marketStatus"),
                cand.get("market_status"),
            ]
            market = cand.get("market") or cand.get("marketInfo")
            if isinstance(market, dict):
                status_sources.extend(
                    [
                        market.get("status"),
                        market.get("state"),
                        market.get("resolution"),
                        market.get("resolved"),
                    ]
                )
            for status in status_sources:
                status_text = _clean(status)
                if status_text:
                    break

        if market_title and outcome_label and status_text:
            break

    return market_title, outcome_label, status_text


def _build_client(address: Optional[str]) -> SimpleNamespace:
    client = SimpleNamespace()
    if address:
        client.funder = address
    return client


def main() -> int:
    parser = argparse.ArgumentParser(description="查询 Polymarket Data API 仓位信息。")
    parser.add_argument(
        "--address",
        help="指定用于查询的地址（建议填 Proxy/Deposit 地址，0x 开头）。不填则使用 client/env 自动解析。",
    )
    parser.add_argument(
        "--token",
        help="可选，按 tokenId 过滤并仅展示匹配仓位。",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="直接输出 JSON 数据。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="除汇总外输出每条仓位的详细 JSON。",
    )
    args = parser.parse_args()

    override_address: Optional[str] = None
    if args.address:
        normalized = _normalize_wallet_address(args.address)
        if not normalized:
            print("提供的地址格式不正确，请输入 0x 开头的以太坊地址。")
            return 2
        override_address = normalized

    client = _build_client(override_address)
    positions, ok, origin = _fetch_positions_from_data_api(client)

    address_source = override_address if override_address else "auto (client/env)"
    if not ok:
        print(f"[FAIL] 查询失败：{origin}")
        print(f"[INFO] 地址来源：{address_source}")
        return 1

    print(f"[OK] 查询成功，来源：{origin}")
    print(f"[INFO] 地址来源：{address_source}")

    data: List[Dict[str, Any]] = [p for p in positions if isinstance(p, dict)]
    if args.token:
        token_filter = str(args.token)
        filtered = [p for p in data if _position_matches_token(p, token_filter)]
    else:
        filtered = data

    print(f"[INFO] 仓位总数：{len(data)}，筛选后：{len(filtered)}")

    if args.raw:
        print(json.dumps(filtered, indent=2, ensure_ascii=False))
        return 0

    summary: Dict[str, Dict[str, Any]] = {}
    for pos in filtered:
        token_id = _extract_token_identifier(pos) or "?"
        size = _extract_position_size_from_entry(pos) or 0.0
        avg_price = _extract_avg_price_from_entry(pos)
        market_title, outcome_label, status_text = _extract_market_metadata(pos)

        entry = summary.setdefault(
            token_id,
            {"size": 0.0, "avg": None, "count": 0, "market": None, "outcome": None, "status": None},
        )
        entry["size"] += size
        entry["count"] += 1
        if avg_price is not None:
            entry["avg"] = avg_price
        if market_title and not entry["market"]:
            entry["market"] = market_title
        if outcome_label and not entry["outcome"]:
            entry["outcome"] = outcome_label
        if status_text and not entry["status"]:
            entry["status"] = status_text

    if summary:
        print("[SUMMARY] 按 token 汇总：")
        for token_id in sorted(summary):
            info = summary[token_id]
            avg_display = info["avg"]
            avg_text = f"{avg_display:.4f}" if avg_display is not None else "N/A"
            extras: List[str] = []
            if info.get("market"):
                extras.append(f"market={info['market']}")
            if info.get("outcome"):
                extras.append(f"outcome={info['outcome']}")
            if info.get("status"):
                extras.append(f"status={info['status']}")
            extra_text = " " + ", ".join(extras) if extras else ""
            print(
                f"  - token={token_id} size={info['size']:.4f} avg_price={avg_text} entries={info['count']}{extra_text}"
            )
    else:
        print("[SUMMARY] 无匹配仓位。")

    if args.verbose:
        print("[DETAIL] 仓位明细：")
        for idx, pos in enumerate(filtered, start=1):
            token_id = _extract_token_identifier(pos) or "?"
            size = _extract_position_size_from_entry(pos) or 0.0
            avg_price = _extract_avg_price_from_entry(pos)
            avg_text = f"{avg_price:.4f}" if avg_price is not None else "N/A"
            market_title, outcome_label, status_text = _extract_market_metadata(pos)
            details = []
            if market_title:
                details.append(f"market={market_title}")
            if outcome_label:
                details.append(f"outcome={outcome_label}")
            if status_text:
                details.append(f"status={status_text}")
            extra = f" ({', '.join(details)})" if details else ""
            print(f"[{idx}] token={token_id} size={size:.4f} avg_price={avg_text}{extra}")
            print(json.dumps(pos, indent=2, ensure_ascii=False))

    if args.token and not filtered:
        print(f"[WARN] 未找到 token={args.token} 的仓位记录。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
