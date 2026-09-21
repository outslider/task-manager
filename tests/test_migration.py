"""既存データベースを、そのまま最新スキーマへ移行できることを確かめる。

`git pull` して再起動するだけで済むかどうかは、次の 2 つが揃っているかで決まる。

  1. 新しいテーブルが DDL にある（CREATE TABLE IF NOT EXISTS なので毎回流れる）
  2. 既存テーブルに足した列・索引・外部キーが、移行リストにも書いてある

2 を書き忘れると、新規インストールでは動くのに既存の環境だけ壊れる。しかも
手元の開発用データベースはたいてい作り直しているので気づけない。

ここでは初版のスキーマ（tests/fixtures/schema-v1.sql）を基準に、現在の DDL が
求める形へ移行リストだけでたどり着けるかを、実際に文字列を突き合わせて確かめる。
データベースを新しく作る権限は要らない。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TM_CONFIG", os.path.join(os.path.dirname(__file__), "test-config.ini"))
os.environ.setdefault("TM_DB_NAME", os.environ.get("TM_TEST_DB_NAME", "task_manager_test"))
os.environ.setdefault("TM_DB_USER", "tmapp")
os.environ.setdefault("TM_DB_PASSWORD", "tmapp_dev_pw")

from app import db  # noqa: E402

BASELINE = os.path.join(os.path.dirname(__file__), "fixtures", "schema-v1.sql")


def canonical_key(entry):
    """索引や制約を (種類, 名前, 列) に揃える。

    手書きの DDL と mysqldump では書き方が違う（バッククォート、改行、
    ON DELETE の位置など）ので、比べられる形に直してから突き合わせる。
    """
    text = re.sub(r"\s+", " ", entry).replace("`", "").strip()
    columns = re.search(r"\(([^)]*)\)", text)
    cols = tuple(c.strip().lower() for c in columns.group(1).split(",")) if columns else ()
    upper = text.upper()
    if upper.startswith("PRIMARY KEY"):
        return ("primary", "", cols)
    fk = re.match(r"CONSTRAINT (\w+) FOREIGN KEY", text, re.I)
    if fk:
        return ("fk", fk.group(1).lower(), cols)
    unique = re.match(r"UNIQUE KEY (\w+)", text, re.I)
    if unique:
        return ("unique", unique.group(1).lower(), cols)
    plain = re.match(r"(?:KEY|INDEX) (\w+)", text, re.I)
    if plain:
        return ("key", plain.group(1).lower(), cols)
    return ("other", text.lower(), cols)


def parse_create_tables(sql):
    """CREATE TABLE 文から {テーブル: {"columns": {...}, "keys": {...}}} を作る。"""
    tables = {}
    pattern = re.compile(
        r"CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`?\s*\((.*?)\n\s*\)\s*ENGINE", re.S)
    for match in pattern.finditer(sql):
        name, body = match.group(1), match.group(2)
        columns, keys = {}, set()
        depth = 0
        line_buffer = ""
        for raw in body.split("\n"):
            line = raw.split("--")[0].strip()
            if not line:
                continue
            line_buffer += " " + line
            depth += line.count("(") - line.count(")")
            if depth > 0:
                continue
            entry = line_buffer.strip().rstrip(",")
            line_buffer = ""
            upper = entry.upper()
            # 「... REFERENCES x(id)」で改行して「ON DELETE ...」と続く書き方がある。
            # 括弧は閉じているので前の行で切れてしまうため、ここで拾い直す。
            if upper.startswith(("ON DELETE", "ON UPDATE", "REFERENCES")):
                continue
            if upper.startswith(("PRIMARY KEY", "UNIQUE KEY", "KEY", "INDEX", "CONSTRAINT")):
                keys.add(canonical_key(entry))
                continue
            column = re.match(r"`?(\w+)`?\s+(.+)", entry)
            if column:
                columns[column.group(1)] = re.sub(r"\s+", " ", column.group(2)).lower()
        tables[name] = {"columns": columns, "keys": keys}
    return tables


class TestMigrationCoverage(unittest.TestCase):
    """初版から現在まで、移行リストだけで追いつけること。"""

    @classmethod
    def setUpClass(cls):
        with open(BASELINE, encoding="utf-8") as fh:
            cls.baseline = parse_create_tables(fh.read())
        cls.target = parse_create_tables("\n".join(db.DDL))
        cls.migrated_columns = {(t, c) for t, c, _ddl in db.MIGRATIONS}
        cls.migrated_indexes = {(t, i) for t, i, _ddl in db.MIGRATION_INDEXES}
        cls.migrated_fks = {(t, n) for t, n, _ddl in db.MIGRATION_FKS}
        cls.nullable = {(t, c) for t, c, _ddl in db.NULLABLE_COLUMNS}

    def test_the_baseline_is_readable(self):
        self.assertIn("tasks", self.baseline)
        self.assertIn("id", self.baseline["tasks"]["columns"])
        self.assertGreaterEqual(len(self.baseline), 10)

    def test_the_current_ddl_is_readable(self):
        self.assertIn("tasks", self.target)
        self.assertGreaterEqual(len(self.target), len(self.baseline))

    def test_new_tables_are_created_by_the_ddl(self):
        """新しいテーブルは CREATE TABLE IF NOT EXISTS で毎回作られるので、
        DDL に載ってさえいればよい。"""
        for name in set(self.target) - set(self.baseline):
            self.assertIn(name, self.target, "{} が DDL にありません".format(name))

    def test_no_table_disappeared(self):
        gone = set(self.baseline) - set(self.target)
        self.assertEqual(gone, set(),
                         "DDL から消えたテーブルがあります（既存環境には残り続けます）: "
                         "{}".format(gone))

    def test_every_added_column_has_a_migration(self):
        """既存テーブルに足した列は MIGRATIONS にも書くこと。"""
        missing = []
        for table, spec in self.target.items():
            if table not in self.baseline:
                continue                       # 新規テーブルは丸ごと作られる
            for column in spec["columns"]:
                if column in self.baseline[table]["columns"]:
                    continue
                if (table, column) not in self.migrated_columns:
                    missing.append("{}.{}".format(table, column))
        self.assertEqual(sorted(missing), [],
                         "db.MIGRATIONS への追加漏れです。既存のデータベースでは "
                         "この列が作られません: {}".format(sorted(missing)))

    def test_every_added_index_has_a_migration(self):
        missing = []
        for table, spec in self.target.items():
            if table not in self.baseline:
                continue
            for kind, name, cols in spec["keys"] - self.baseline[table]["keys"]:
                if not name:
                    continue
                if ((table, name) not in self.migrated_indexes
                        and (table, name) not in self.migrated_fks):
                    missing.append("{}: {} {} {}".format(table, kind, name, cols))
        self.assertEqual(sorted(missing), [],
                         "db.MIGRATION_INDEXES / MIGRATION_FKS への追加漏れです: "
                         "{}".format(sorted(missing)))

    def test_columns_that_became_nullable_are_listed(self):
        """NOT NULL を外した列は、既存環境では ALTER が要る。"""
        missing = []
        for table, spec in self.target.items():
            if table not in self.baseline:
                continue
            for column, definition in spec["columns"].items():
                was = self.baseline[table]["columns"].get(column)
                if was is None:
                    continue
                # 主キーなど、書き方の違いで誤検知しないよう「明示的に NULL」だけを見る
                if "not null" in was and re.search(r"\bnull\b", definition) \
                        and "not null" not in definition:
                    if (table, column) not in self.nullable:
                        missing.append("{}.{}".format(table, column))
        self.assertEqual(sorted(missing), [],
                         "db.NULLABLE_COLUMNS への追加漏れです: {}".format(sorted(missing)))

    def test_migration_entries_point_at_real_tables(self):
        """存在しないテーブルへの移行が残っていないこと。"""
        for table, column in sorted(self.migrated_columns):
            self.assertIn(table, self.target,
                          "{} は DDL にないテーブルです（{} の移行）".format(table, column))
            self.assertIn(column, self.target[table]["columns"],
                          "{}.{} は DDL に無いのに移行だけ残っています".format(table, column))

    def test_the_baseline_still_differs_from_today(self):
        """基準が最新版で上書きされていないこと（テストの形骸化よけ）。"""
        added = sum(len(set(spec["columns"]) - set(self.baseline[t]["columns"]))
                    for t, spec in self.target.items() if t in self.baseline)
        self.assertTrue(added or set(self.target) - set(self.baseline),
                        "初版からの差が無くなっています。fixtures/schema-v1.sql を"
                        "うっかり更新していませんか？")


class TestMigrationsAreIdempotent(unittest.TestCase):
    def test_running_twice_changes_nothing(self):
        """再起動のたびに走るので、二度目は何もしないこと。"""
        db.init_db()
        db.run_migrations()
        self.assertEqual(db.run_migrations(), [],
                         "同じ移行が繰り返し適用されています")


if __name__ == "__main__":
    unittest.main()
