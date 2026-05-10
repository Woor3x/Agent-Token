"""Unit tests for synthesizer cell formatter (_fmt_cell)."""
from agents.doc_assistant.nodes.synthesizer import _fmt_cell, _fmt_number


def test_fmt_cell_unix_ms_to_date():
    # 1731859200000 = 2024-11-17 12:00:00 UTC
    assert _fmt_cell(1731859200000) == "2024-11-17"


def test_fmt_cell_huge_float_no_scientific():
    # Real Feishu sometimes returns DateTime as float (1.90311e+13). The
    # raw str(1.90311e+13) yields "19031100000000.0" or "1.90311e+13"
    # depending on platform — assert no scientific notation.
    out = _fmt_cell(1.90311e13)
    assert "e+" not in out and "E+" not in out


def test_fmt_cell_user_list_renders_names():
    users = [
        {"email": "", "en_name": "Yim1ngs", "id": "ou_x", "name": "Yim1ngs"},
        {"en_name": "刘大建", "id": "ou_y", "name": "刘大建"},
    ]
    assert _fmt_cell(users) == "Yim1ngs, 刘大建"


def test_fmt_cell_text_element_list_joins_text():
    elements = [{"text": "🚨 已延期", "type": "text"}]
    assert _fmt_cell(elements) == "🚨 已延期"


def test_fmt_cell_empty_returns_empty():
    assert _fmt_cell(None) == ""
    assert _fmt_cell("") == ""
    assert _fmt_cell([]) == ""


def test_fmt_cell_int_unchanged():
    assert _fmt_cell(42) == "42"


def test_fmt_cell_collapses_whitespace():
    s = "任务执行人于小宁正在进行年度财务报告的制作\n\n预计下周完成初稿。"
    assert "\n" not in _fmt_cell(s)


def test_fmt_number_integer_valued_float():
    assert _fmt_number(100.0) == "100"


def test_fmt_number_fraction_no_scientific():
    assert "e" not in _fmt_number(1.5e13).lower()
