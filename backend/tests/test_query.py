"""口语化查询扩写：只追加术语，不改变原问题。"""

from rag.query import expand_query


def test_expand_query_appends_legal_terms():
    expanded = expand_query("公司没交社保，我辞职有补偿吗")
    assert expanded.startswith("公司没交社保，我辞职有补偿吗")
    assert "社会保险" in expanded
    assert "解除劳动合同" in expanded
    assert "经济补偿" in expanded


def test_expand_query_keeps_original_when_no_rule_matches():
    query = "民法典第一百七十九条规定的承担民事责任方式有哪些？"
    assert expand_query(query) == query


def test_expand_query_does_not_duplicate_existing_terms():
    expanded = expand_query("房东出卖房屋后，承租人还能继续租吗")
    assert expanded.count("出卖") == 1
    assert expanded.count("承租人") == 1
    # 原查询里已有的词不再重复追加
    assert expanded.startswith("房东出卖房屋后，承租人还能继续租吗")


def test_expand_query_handles_empty_input():
    assert expand_query("") == ""
    assert expand_query(None) == ""


def test_expand_query_matches_multiple_rules_in_order():
    expanded = expand_query("网上有人造谣，平台不删帖")
    assert "诽谤" in expanded
    assert "名誉权" in expanded
    assert "网络服务提供者" in expanded
    assert "删除" in expanded
