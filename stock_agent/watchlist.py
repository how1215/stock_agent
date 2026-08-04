"""自選股清單。

單一清單存在 `data/watchlist.json`，格式 `{"codes": [...], "updated_at": "..."}`。
`data/` 已在 .gitignore，所以個人關注偏好不會進版控。

新增時會對最新交易日資料驗證代號存在 — 打錯的代號若默默躺在清單裡，
每天的盤前簡報都會少一檔而且不會有人發現。
"""

import json
from datetime import datetime, timezone

import config

from . import render
from . import screen as screening
from .sources import cache


def _path():
    # 透過 config 模組取值（而非 import 時綁定），讓測試能改導向
    return config.WATCHLIST_PATH


def load() -> list[str]:
    """目前的自選股代號，維持加入順序。檔案不存在時回空清單。"""
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    codes = data.get("codes", [])
    return [str(code) for code in codes if isinstance(code, (str, int))]


def _save(codes: list[str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"codes": codes, "updated_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _named(codes: list[str], quotes: list[dict]) -> list[dict]:
    """把代號清單配上名稱，讓輸出對人有意義。"""
    by_code = {q["code"]: q["name"] for q in quotes}
    return [{"code": code, "name": by_code.get(code, "?")} for code in codes]


def describe(date: str | None = None) -> dict:
    """列出自選股，附上名稱與該交易日的收盤數據。

    與 query.py 同一個約定：payload 帶 `text` 欄位（給人與 LLM 讀的渲染），
    外部呼叫者一次就同時拿到結構化資料與文字。
    """
    codes = load()
    day = cache.load_or_fetch(date)
    quotes, missing = screening.find_quotes(day["quotes"], codes)
    described = {
        "trade_date": day["trade_date"],
        "codes": codes,
        "quotes": quotes,
        "not_found": missing,
    }
    described["text"] = render.render_watchlist(described)
    return described


def add(keys: list[str], date: str | None = None) -> dict:
    """加入自選股。keys 可為代號或公司名稱。

    Returns:
        added / already_present / not_found / codes（操作後的完整清單）
    """
    current = load()
    day = cache.load_or_fetch(date)
    found, not_found = screening.find_quotes(day["quotes"], keys)

    added, already = [], []
    codes = list(current)
    for quote in found:
        if quote["code"] in codes:
            already.append({"code": quote["code"], "name": quote["name"]})
        elif len(codes) >= config.MAX_WATCHLIST_SIZE:
            not_found.append(
                f"{quote['code']}（清單已達上限 {config.MAX_WATCHLIST_SIZE} 檔）"
            )
        else:
            codes.append(quote["code"])
            added.append({"code": quote["code"], "name": quote["name"]})

    if added:
        _save(codes)

    result = {
        "added": added,
        "already_present": already,
        "not_found": not_found,
        "codes": codes,
        "watchlist": _named(codes, day["quotes"]),
    }
    result["text"] = render.render_watchlist_change(result)
    return result


def remove(keys: list[str], date: str | None = None) -> dict:
    """移除自選股。keys 可為代號或公司名稱。

    找不到對應個股時仍嘗試按原字串移除 — 若某檔已從市場消失（下市），
    只認得市場資料就永遠移不掉它。
    """
    current = load()
    day = cache.load_or_fetch(date)
    found, unresolved = screening.find_quotes(day["quotes"], keys)

    targets = {quote["code"] for quote in found}
    targets.update(key.strip() for key in unresolved)

    codes = [code for code in current if code not in targets]
    removed = [code for code in current if code in targets]
    not_in_list = sorted(targets - set(current))

    if removed:
        _save(codes)

    result = {
        "removed": removed,
        "not_in_list": not_in_list,
        "codes": codes,
        "watchlist": _named(codes, day["quotes"]),
    }
    result["text"] = render.render_watchlist_change(result)
    return result


def clear() -> dict:
    """清空自選股。刻意不開放給 LLM 工具與 Pi extension — 破壞性操作只留在 CLI。"""
    removed = load()
    _save([])
    result = {"removed": removed, "codes": [], "watchlist": []}
    result["text"] = render.render_watchlist_change(result)
    return result
