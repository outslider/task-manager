"""Claude API による自然言語解析とタスク分解（任意機能）。

anthropic SDK が入っていて、管理画面で有効にしたときだけ使われる。
使えない・失敗した場合は呼び出し元が app/nlp.py のルールベースへ戻す。

    pip install anthropic
"""
import json
import logging
from datetime import date

from . import db

log = logging.getLogger("tm.llm")

DEFAULT_MODEL = "claude-opus-5"
MODELS = [
    ("claude-opus-5", "Claude Opus 5（既定・最も高精度）"),
    ("claude-sonnet-5", "Claude Sonnet 5（バランス型）"),
    ("claude-haiku-4-5", "Claude Haiku 4.5（高速・低コスト）"),
]

def category_values():
    """カテゴリは画面から増やせるので、スキーマもそのつど組み立てる。"""
    from . import taxonomy
    return [c["value"] for c in taxonomy.categories()] + [""]

def with_categories(schema):
    """スキーマの category に、いま登録されているカテゴリを enum として入れる。"""
    import copy
    values = category_values()
    filled = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "category" and isinstance(value, dict):
                    value["enum"] = values
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(filled)
    return filled


PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "タスク名。依頼文ではなく簡潔な名詞句にする"},
        "description": {"type": "string", "description": "補足。なければ空文字"},
        "category": {"type": "string"},
        "priority": {"type": "integer", "enum": [0, 1, 2, 3],
                     "description": "重要度 0=低 1=中 2=高 3=最重要"},
        "assignee_name": {"type": "string", "description": "担当者名。不明なら空文字"},
        "project_name": {"type": "string", "description": "プロジェクト名。不明なら空文字"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD。不明なら空文字"},
        "due_date": {"type": "string", "description": "YYYY-MM-DD。不明なら空文字"},
        "is_milestone": {"type": "boolean"},
    },
    "required": ["title", "description", "category", "priority", "assignee_name",
                 "project_name", "start_date", "due_date", "is_milestone"],
    "additionalProperties": False,
}

DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": "実行順に並べた子タスク。5〜10件程度",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "子タスク名（簡潔な名詞句）"},
                    "category": {"type": "string"},
                    # 構造化出力のスキーマは integer の minimum/maximum を受け付けないため
                    # 取りうる値を enum で列挙する
                    "weight": {"type": "integer", "enum": [1, 2, 3, 4, 5],
                               "description": "相対的な所要期間。標準は 1〜3"},
                },
                "required": ["title", "category", "weight"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["steps"],
    "additionalProperties": False,
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "description": "メモから読み取った、やるべきこと。書かれていないものは作らない",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string",
                              "description": "タスク名。一覧で読みやすい簡潔な名詞句にする"},
                    "assignee_name": {"type": "string",
                                      "description": "メモに書かれている呼び方をそのまま"
                                                     "（「鈴木さん」なら「鈴木さん」）。"
                                                     "誰の担当か書かれていなければ空文字"},
                    "due_date": {"type": "string",
                                 "description": "YYYY-MM-DD。書かれていなければ空文字"},
                    "category": {"type": "string"},
                    "priority": {"type": "integer", "enum": [0, 1, 2, 3],
                                 "description": "重要度 0=低 1=中 2=高 3=最重要。既定は 1"},
                    "description": {"type": "string",
                                    "description": "メモ中の補足。なければ空文字"},
                    "source": {"type": "string",
                               "description": "根拠になったメモ中の一文をそのまま写す"},
                },
                "required": ["title", "assignee_name", "due_date", "category",
                             "priority", "description", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tasks"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string",
                     "description": "今の状況をひとことで。誇張せず、事実に基づいて書く"},
        "risks": {
            "type": "array",
            "description": "放っておくと困ることを、重い順に 2〜4 件",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "何が問題かを一行で"},
                    "detail": {"type": "string",
                               "description": "そう言える根拠。渡したデータの数字を使って書く"},
                    "action": {"type": "string", "description": "今週やると効くこと。具体的に"},
                    "level": {"type": "string", "enum": ["高", "中", "低"]},
                    "task_ids": {"type": "array", "items": {"type": "integer"},
                                 "description": "関係するタスクの id。渡した一覧にあるものだけ"},
                },
                "required": ["title", "detail", "action", "level", "task_ids"],
                "additionalProperties": False,
            },
        },
        "focus": {
            "type": "array",
            "description": "今週いちばん先に手を付けるべきタスク 1〜3 件",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "why": {"type": "string", "description": "なぜそれが先かを一行で"},
                },
                "required": ["task_id", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "risks", "focus"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """あなたは社内の会議メモから、やるべきことを拾い出すアシスタントです。

- 「やる」と決まったことだけを拾う。検討中の話、感想、決定事項の記録は拾わない
- title は依頼文のままにせず、一覧で読みやすい簡潔な名詞句にする
- 日付は必ず YYYY-MM-DD に変換する。相対表現は「今日の日付」を基準に解釈する。
  「来週◯曜」は今日を含む週（月曜始まり）の次の週、「月末」はその月の最終日とする。
  曜日から出した日付が今日より前になってしまう場合は、次に来るその曜日にする
  （「先週」「昨日」のように過去を明示しているときを除く）
- 担当者は、メモに書かれている呼び方をそのまま書く（「鈴木さん」ならそのまま）。
  誰の担当か書かれていなければ空文字
- メモに書かれていない期限や担当者を推測で埋めない
- description にはメモ中の補足だけを書く。自分の判断や注記は書かない
- 同じことを指す記述が複数あってもタスクは 1 つにまとめる
- 拾うものがなければ空の配列を返す"""

REVIEW_SYSTEM = """あなたは社内プロジェクトの進行を見ているアシスタントです。
渡された数字をもとに、今週この先どこが危ないかを、担当者に向けて日本語で書きます。

- 渡されたデータに書かれていないことは言わない。推測で数字を作らない
- 「頑張りましょう」のような一般論は書かない。どのタスクをどうするかを書く
- 期限超過そのものより、他の作業を止めているものを重く見る
- 担当者を責める書き方はしない。事実と、次にやると効くことだけを書く"""


PARSE_SYSTEM = """あなたは社内タスク管理システムの入力アシスタントです。
担当者が書いた一文から、登録すべきタスクの項目を抜き出します。

- title は依頼文のままにせず、一覧で読みやすい簡潔な名詞句にする
  （例:「来週金曜までに移行手順書を作ってください」→「移行手順書の作成」）
- 日付は必ず YYYY-MM-DD に変換する。相対表現は「今日の日付」を基準に解釈する
- 担当者名・プロジェクト名は、与えられた一覧の表記に完全一致させる。該当がなければ空文字
- 推測で項目を埋めない。書かれていないものは空文字・既定値にする"""

DECOMPOSE_SYSTEM = """あなたは社内プロジェクトの進行管理を支援するアシスタントです。
大きな作業を、担当者がそのまま着手できる粒度の子タスクに分解します。

- 実行順に並べる。前工程・実作業・確認・締めの報告まで含める
- 1つの子タスクは1人が数時間〜数日で終わる粒度にする
- 抽象語（検討、対応、推進）だけの項目は作らない。何をするか分かる名前にする
- その作業に固有の工程を書く。どんな仕事にも当てはまる一般論は避ける
- 5〜10件に収める"""


def settings():
    values = db.all_settings()
    return {
        "enabled": values.get("llm_enabled") == "1",
        "api_key": values.get("llm_api_key", ""),
        "model": values.get("llm_model") or DEFAULT_MODEL,
    }


def sdk_installed():
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def available():
    config = settings()
    return bool(config["enabled"] and config["api_key"] and sdk_installed())


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings()["api_key"])


def _ask(system, prompt, schema, effort="medium", max_tokens=16000):
    """構造化出力で1往復だけ問い合わせる。戻り値は dict。"""
    import anthropic

    config = settings()
    client = _client()
    try:
        response = client.messages.create(
            model=config["model"],
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError:
        raise LlmError("APIキーが正しくありません")
    except anthropic.RateLimitError:
        raise LlmError("APIのレート制限に達しました。しばらく待って再試行してください")
    except anthropic.APIStatusError as error:
        raise LlmError("Claude API エラー ({}): {}".format(error.status_code, error.message))
    except anthropic.APIConnectionError:
        raise LlmError("Claude API に接続できませんでした（ネットワークを確認してください）")

    if response.stop_reason == "refusal":
        raise LlmError("リクエストが安全上の理由で処理されませんでした")
    if response.stop_reason == "max_tokens":
        raise LlmError("応答が長すぎて途中で切れました（入力を短くしてください）")
    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text.strip():
        raise LlmError("応答が空でした")
    try:
        return json.loads(text)
    except ValueError:
        log.warning("unparsable response: %s", text[:200])
        raise LlmError("応答を解釈できませんでした")


class LlmError(Exception):
    pass


def _context_block(users, projects, today):
    return "今日の日付: {} ({})\n担当者一覧: {}\nプロジェクト一覧: {}".format(
        today.isoformat(), "月火水木金土日"[today.weekday()],
        "、".join(u["name"] for u in users) or "なし",
        "、".join(p["name"] for p in projects) or "なし")


def parse(text, users=(), projects=(), base=None):
    """自然言語からタスク下書きを作る。失敗時は LlmError。"""
    base = base or date.today()
    data = _ask(
        PARSE_SYSTEM,
        "{}\n\n---\n登録したい内容:\n{}".format(_context_block(users, projects, base), text),
        with_categories(PARSE_SCHEMA), effort="low")

    by_name = {u["name"]: u["id"] for u in users}
    projects_by_name = {p["name"]: p["id"] for p in projects}
    return {
        "title": (data.get("title") or "").strip(),
        "description": data.get("description") or "",
        "category": data.get("category") or "",
        "status": "todo",
        "priority": int(data.get("priority", 1)),
        "assignee_id": by_name.get(data.get("assignee_name")),
        "project_id": projects_by_name.get(data.get("project_name")),
        "start_date": _iso_or_none(data.get("start_date")),
        "due_date": _iso_or_none(data.get("due_date")),
        "progress": 0,
        "is_milestone": bool(data.get("is_milestone")),
        "confidence": 0.9,
        "matched": [],
        "engine": "llm",
    }


def decompose(title, description="", start_date=None, due_date=None):
    """大きなタスクを子タスク候補に分解する。失敗時は LlmError。"""
    period = ""
    if start_date or due_date:
        period = "\n期間: {} 〜 {}".format(start_date or "未定", due_date or "未定")
    data = _ask(
        DECOMPOSE_SYSTEM,
        "分解したいタスク: {}{}{}".format(
            title, "\n補足: " + description if description else "", period),
        with_categories(DECOMPOSE_SCHEMA), effort="medium")

    steps = [(s.get("title", "").strip(), s.get("category", ""), int(s.get("weight", 1)))
             for s in data.get("steps", []) if s.get("title")]
    return steps


def extract(text, users=(), projects=(), base=None):
    """会議メモから、登録できる形のタスク候補をまとめて取り出す。失敗時は LlmError。"""
    base = base or date.today()
    data = _ask(
        EXTRACT_SYSTEM,
        "{}\n\n---\n会議メモ:\n{}".format(_context_block(users, projects, base), text),
        with_categories(EXTRACT_SCHEMA), effort="medium")

    rows = []
    for item in data.get("tasks", []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        rows.append({
            "title": title,
            "assignee": (item.get("assignee_name") or "").strip(),
            "due_date": _iso_or_none(item.get("due_date")) or "",
            "category": item.get("category") or "",
            "priority": int(item.get("priority", 1)),
            "description": item.get("description") or "",
            "source": (item.get("source") or "").strip(),
        })
    return rows


def review(context):
    """進行状況の要約テキストを渡し、今週の見立てを書いてもらう。失敗時は LlmError。"""
    return _ask(REVIEW_SYSTEM, context, REVIEW_SCHEMA, effort="medium")


def _iso_or_none(value):
    value = (value or "").strip()[:10]
    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        return None


def check():
    """管理画面の接続テスト用。(成否, メッセージ) を返す。"""
    if not sdk_installed():
        return False, ("サーバーに anthropic パッケージが入っていません。"
                       "サーバー上で .venv/bin/pip install anthropic を実行してください")
    config = settings()
    if not config["api_key"]:
        return False, "API キーが未設定です（sk-ant- で始まるキーを入力して保存してください）"
    if not config["api_key"].startswith("sk-ant-"):
        return False, "API キーの形式が正しくありません（sk-ant- で始まります）"
    try:
        steps = decompose("サーバー移行")
    except LlmError as error:
        return False, str(error)
    except Exception as error:  # noqa: BLE001 - 管理画面に出す
        log.warning("llm check failed: %s", error)
        return False, "接続に失敗しました: {}".format(error)
    return True, "接続できました（{} / 例: {}）".format(
        config["model"], "、".join(s[0] for s in steps[:3]))
