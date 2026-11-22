import json

from conftest import MEETINGS, SAMPLES
from pmo.ingest import ingest_files


def test_ingest_ten_meetings_no_missing_no_duplicates(conn):
    res = ingest_files(conn, MEETINGS)
    assert res["status"] == "success"
    assert res["new"] == 10
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 10
    projects = {r[0] for r in conn.execute("SELECT DISTINCT project FROM sources")}
    assert projects == {"販売管理刷新", "経費精算クラウド導入", "社内ポータル移行"}
    dates = sorted(r[0] for r in conn.execute("SELECT meeting_date FROM sources"))
    assert dates[0] == "2026-10-05" and dates[-1] == "2026-10-30"   # 4週間分
    # 出典（URL / パス）が全件に入っている
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE url IS NULL OR url=''").fetchone()[0] == 0


def test_reingest_and_duplicate_file_do_not_add_versions(conn):
    ingest_files(conn, MEETINGS)
    res = ingest_files(conn, MEETINGS + [SAMPLES / "variants" / "alpha_01_duplicate.md"])
    assert res["unchanged"] == 11 and res["new"] == 0 and res["new_version"] == 0
    assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 10


def test_edited_page_creates_new_version(conn):
    ingest_files(conn, MEETINGS)
    res = ingest_files(conn, [SAMPLES / "variants" / "alpha_01_edited.md"])
    assert res["new_version"] == 1
    n = conn.execute("SELECT COUNT(*) FROM source_versions WHERE source_id='sample-alpha-01'").fetchone()[0]
    assert n == 2


def test_bad_file_is_recorded_not_success(conn, tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("先頭の情報が無いファイル", encoding="utf-8")
    res = ingest_files(conn, MEETINGS[:2] + [bad])
    assert res["status"] == "partial"
    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (res["run_id"],)).fetchone()
    assert run["status"] == "partial" and "bad.md" in run["error"]
    assert json.loads(run["detail"])["new"] == 2
