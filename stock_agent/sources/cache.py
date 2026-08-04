"""以交易日為單位的檔案 cache。

因為 TWSE 端點不吃日期參數、只回最新交易日（見 twse.py），這裡的職責分成兩種：

- 取最新交易日：無法用日期判斷 cache 是否過期，只能用 fetched_at 時間戳 + TTL。
- 取指定交易日：只能從 cache 讀。cache 沒有就是查不到 — 打 API 也只會拿到最新
  交易日的資料，硬打反而會回錯日期的數字，是比報錯更糟的結果。
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import config

from . import twse


class DataUnavailable(Exception):
    """要求的交易日不在本地 cache，且無法從 API 取得。"""


def _day_file(trade_date: str) -> Path:
    # 透過 config 模組取值（而非 import 時綁定常數），讓測試能改導向 cache 目錄
    return config.CACHE_DIR / f"{trade_date}.json"


def _write(day: dict) -> dict:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    day = {**day, "fetched_at": datetime.now(timezone.utc).isoformat()}
    _day_file(day["trade_date"]).write_text(json.dumps(day, ensure_ascii=False), encoding="utf-8")
    return day


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# 只有 YYYY-MM-DD.json 才是日檔。快取目錄裡還有 watchlist.json，早期版本無條件
# glob *.json 會把它當成交易日 —— 而且它排序在日期之後，於是被當成「最新交易日」，
# 讀進來沒有 fetched_at 就永遠判定過期，磁碟快取因此完全失效（每次呼叫都打網路）。
_DAY_FILE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def cached_dates() -> list[str]:
    """本地已有的交易日，由舊到新。非日期檔名一律忽略。"""
    if not config.CACHE_DIR.is_dir():
        return []
    return sorted(
        p.stem for p in config.CACHE_DIR.glob("*.json") if _DAY_FILE_PATTERN.match(p.stem)
    )


def _is_fresh(day: dict) -> bool:
    fetched_at = day.get("fetched_at")
    if not fetched_at:
        return False
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
    return age < config.FRESH_TTL_SECONDS


def load_or_fetch(trade_date: str | None = None) -> dict:
    """取得一個交易日的全市場資料。

    Args:
        trade_date: ISO 日期。None 表示取最新交易日。

    Raises:
        DataUnavailable: 指定的交易日不在本地 cache。
    """
    if trade_date is not None:
        path = _day_file(trade_date)
        if not path.is_file():
            available = cached_dates()
            hint = f"本地已有：{', '.join(available)}" if available else "本地目前沒有任何日期的資料"
            raise DataUnavailable(
                f"沒有 {trade_date} 的資料。TWSE 開放端點只提供最新交易日，"
                f"歷史資料需靠每日抓取累積。{hint}"
            )
        return _read(path)

    dates = cached_dates()
    if dates:
        newest = _read(_day_file(dates[-1]))
        if _is_fresh(newest):
            return newest

    return _write(twse.fetch_latest())
