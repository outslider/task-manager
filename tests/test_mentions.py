"""コメント中の「@名前」から、呼ばれた人を割り出す。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import mentions  # noqa: E402

PEOPLE = [
    {"id": 1, "name": "佐藤 花子", "email": "hanako@example.com"},
    {"id": 2, "name": "佐藤 太郎", "email": "taro@example.com"},
    {"id": 3, "name": "鈴木 一郎", "email": "suzuki@example.com"},
    {"id": 4, "name": "Alice", "email": "alice@example.com"},
]


class TestFindMentions(unittest.TestCase):
    def names(self, text, people=PEOPLE):
        found, _labels = mentions.find(text, people)
        return sorted(p["name"] for p in found)

    def test_full_name(self):
        self.assertEqual(self.names("@佐藤 花子 確認おねがいします"), ["佐藤 花子"])

    def test_full_width_at_sign(self):
        self.assertEqual(self.names("＠鈴木 一郎 どうでしょう"), ["鈴木 一郎"])

    def test_name_without_a_space(self):
        self.assertEqual(self.names("@佐藤花子 見てください"), ["佐藤 花子"])

    def test_several_people(self):
        self.assertEqual(self.names("@佐藤 太郎 と @鈴木 一郎 で進めます"),
                         ["佐藤 太郎", "鈴木 一郎"])

    def test_honorific_after_the_name(self):
        self.assertEqual(self.names("@鈴木 一郎さん、おねがいします"), ["鈴木 一郎"])
        self.assertEqual(self.names("@鈴木 一郎様"), ["鈴木 一郎"])

    def test_punctuation_after_the_name(self):
        self.assertEqual(self.names("@鈴木 一郎、確認を"), ["鈴木 一郎"])
        self.assertEqual(self.names("（@鈴木 一郎）"), ["鈴木 一郎"])

    def test_email_address(self):
        self.assertEqual(self.names("@suzuki 見てください"), ["鈴木 一郎"])
        self.assertEqual(self.names("@hanako@example.com にも共有"), ["佐藤 花子"])

    def test_a_shared_family_name_is_not_guessed(self):
        """同姓が二人いるときに、勝手にどちらかへ通知しないこと。"""
        self.assertEqual(self.names("@佐藤 よろしく"), [])
        self.assertEqual(self.names("@鈴木 よろしく"), ["鈴木 一郎"])

    def test_a_plain_email_in_the_text_is_not_a_mention(self):
        self.assertEqual(self.names("連絡先は foo@bar.example です"), [])

    def test_unknown_name(self):
        self.assertEqual(self.names("@山田 太郎 に聞いてください"), [])

    def test_the_same_person_twice_is_counted_once(self):
        found, _ = mentions.find("@Alice と @Alice", PEOPLE)
        self.assertEqual(len(found), 1)

    def test_no_at_sign(self):
        self.assertEqual(self.names("普通のコメントです"), [])

    def test_empty_body(self):
        self.assertEqual(self.names(""), [])
        self.assertEqual(self.names(None), [])

    def test_only_the_given_people_are_matched(self):
        """プロジェクトのメンバー以外は呼べない（宛先を絞るため）。"""
        self.assertEqual(self.names("@鈴木 一郎 へ", PEOPLE[:2]), [])

    def test_ascii_name(self):
        self.assertEqual(self.names("@Alice please check"), ["Alice"])

    def test_strip_mentions_for_a_subject_line(self):
        self.assertEqual(mentions.strip_mentions("@佐藤 花子 確認を", PEOPLE),
                         "佐藤 花子 確認を")


if __name__ == "__main__":
    unittest.main()
