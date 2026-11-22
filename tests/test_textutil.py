from pmo.textutil import content_hash, find_quote, normalize, parse_due, similarity


def test_hash_ignores_trailing_spaces_and_crlf():
    assert content_hash("a \r\nb\n") == content_hash("a\nb")
    assert content_hash("a\nb") != content_hash("a\nc")


def test_normalize_width_and_punct():
    assert normalize("ＡＢＣ　１２３。") == normalize("abc 123")


def test_parse_due():
    assert parse_due("10/9", "2026-10-05") == "2026-10-09"
    assert parse_due("11月6日まで", "2026-10-26") == "2026-11-06"
    assert parse_due("2027-01-15", "2026-10-26") == "2027-01-15"
    assert parse_due("1/10", "2026-12-20") == "2027-01-10"   # 年をまたぐ
    assert parse_due("来週中", "2026-10-12") is None           # 読めないものは日付にしない
    assert parse_due("10/9", None) is None                    # 年が分からなければ作らない
    assert parse_due("2/30", "2026-10-01") is None


def test_find_quote():
    body = "佐藤さんが現行帳票の一覧を10/9までに作成する。"
    assert find_quote(body, "現行帳票の一覧を10/9までに作成する。") is not None
    assert find_quote(body, "佐藤さんが帳票を作る") is None
    assert find_quote(body, "") is None


def test_similarity():
    assert similarity("データ移行方針を整理する", "データ移行方針を整理する") == 1.0
    assert similarity("データ移行方針を整理する", "交通費の精算") < 0.3
