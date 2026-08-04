"""Pi 資產的一致性檢查：skill、AGENTS.md、extension、CLI 之間的引用。

這些是散文與 TypeScript，跑不進 Python 的 eval harness。但有一類錯誤可以在這裡
擋掉：**引用了不存在的名字**。skill 叫模型呼叫 `stock_levels`、或 extension 呼叫
`cli sync` —— 這兩種錯誤在執行時都只會安靜失敗（模型找不到工具就改用別的，
子命令錯了 argparse 的錯誤只會出現在 stderr）。

自動驗得到的是「名字對得上」；模型是否真的照 skill 的順序執行，只能實機驗
（見 docs/ARCHITECTURE.md 的說明）。
"""

import re
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
EXTENSION = PROJECT / ".pi" / "extensions" / "tw-stock.ts"
AGENTS = PROJECT / ".pi" / "AGENTS.md"
SKILL = PROJECT / ".pi" / "skills" / "premarket" / "SKILL.md"
CLI = PROJECT / "stock_agent" / "cli.py"


def _registered_tools() -> set[str]:
    """extension 實際註冊的工具名稱。"""
    source = EXTENSION.read_text(encoding="utf-8")
    return set(re.findall(r'name:\s*"(stock_[a-z_]+)"', source))


def _cli_subcommands_used_by_extension() -> set[str]:
    """extension 傳給 CLI 的第一個位置參數 = 子命令。"""
    source = EXTENSION.read_text(encoding="utf-8")
    # runCli(["briefing", ...]) 與 const args = ["scan", ...] 兩種寫法
    return set(re.findall(r'(?:runCli\(\[|args = \[)\s*\n?\s*"([a-z]+)"', source))


def _cli_subcommands_defined() -> set[str]:
    return set(re.findall(r'sub\.add_parser\(\s*"([a-z]+)"', CLI.read_text(encoding="utf-8")))


# Python 套件也叫 stock_agent（`python -m stock_agent.cli`），不是工具名
_NOT_A_TOOL = {"stock_agent"}


def _tools_mentioned(path: Path) -> set[str]:
    found = set(re.findall(r"\bstock_[a-z_]+", path.read_text(encoding="utf-8")))
    return found - _NOT_A_TOOL


class TestSkillFile:
    def test_exists_with_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        assert text.startswith("---"), "Agent Skills 需要 YAML frontmatter"
        header = text.split("---")[1]
        assert re.search(r"^name:\s*\S+", header, re.M)
        assert re.search(r"^description:\s*\S+", header, re.M)

    def test_name_matches_directory(self):
        """Pi 用目錄名決定 /skill:<name>，frontmatter 不一致會讓人困惑。"""
        header = SKILL.read_text(encoding="utf-8").split("---")[1]
        name = re.search(r"^name:\s*(\S+)", header, re.M).group(1)
        assert name == SKILL.parent.name

    def test_states_the_ordering_constraint(self):
        """步驟一不能跳的理由必須寫在 skill 裡，否則模型會為了省時間跳過。"""
        text = SKILL.read_text(encoding="utf-8")
        assert "stock_market" in text
        assert "不能跳" in text or "不要跳" in text

    def test_warns_about_refresh_cost(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "refresh" in text and "80" in text


class TestReferencesResolve:
    def test_extension_registers_expected_tools(self):
        assert _registered_tools() == {
            "stock_market",
            "stock_quote",
            "stock_screen",
            "stock_scan",
            "stock_briefing",
            "stock_watchlist",
        }

    @pytest.mark.parametrize("doc", [AGENTS, SKILL], ids=["AGENTS.md", "SKILL.md"])
    def test_docs_only_reference_registered_tools(self, doc):
        """散文裡寫錯工具名，模型會找不到工具而默默改用別的。"""
        unknown = _tools_mentioned(doc) - _registered_tools()
        assert not unknown, f"{doc.name} 提到不存在的工具：{sorted(unknown)}"

    def test_extension_only_calls_defined_cli_subcommands(self):
        """extension 呼叫不存在的子命令時，argparse 的錯誤只會出現在 stderr。"""
        used, defined = _cli_subcommands_used_by_extension(), _cli_subcommands_defined()
        assert used, "沒解析到任何子命令，正規表示式可能失效了"
        assert used <= defined, f"extension 呼叫了未定義的子命令：{sorted(used - defined)}"

    def test_every_registered_tool_is_documented(self):
        """工具沒寫進 AGENTS.md，模型就少了選它的依據。"""
        undocumented = _registered_tools() - _tools_mentioned(AGENTS)
        assert not undocumented, f"AGENTS.md 未提及：{sorted(undocumented)}"


class TestDateDiscipline:
    """報告日與交易日混淆是這個專案最容易誤導使用者的地方，
    所以三份給模型讀的文件都必須提到它。"""

    @pytest.mark.parametrize("doc", [AGENTS, SKILL], ids=["AGENTS.md", "SKILL.md"])
    def test_mentions_trade_date_vs_report_date(self, doc):
        text = doc.read_text(encoding="utf-8")
        assert "交易日" in text
        assert "前一交易日" in text or "報告日" in text

    def test_python_prompt_also_mentions_it(self):
        from stock_agent.prompts import SYSTEM_PROMPT

        assert "交易日" in SYSTEM_PROMPT
        assert "前一個交易日" in SYSTEM_PROMPT or "前一交易日" in SYSTEM_PROMPT
