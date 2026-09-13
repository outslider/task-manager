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

CATEGORY_VALUES = ["research", "design", "build", "docs", "meeting", "admin", "incident", ""]

PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "タスク名。依頼文ではなく簡潔な名詞句にする"},
        "description": {"type": "string", "description": "補足。なければ空文字"},
        "category": {"type": "string", "enum": CATEGORY_VALUES},
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
                    "category": {"type": "string", "enum": CATEGORY_VALUES},
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
        PARSE_SCHEMA, effort="low")

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
        DECOMPOSE_SCHEMA, effort="medium")

    steps = [(s.get("title", "").strip(), s.get("category", ""), int(s.get("weight", 1)))
             for s in data.get("steps", []) if s.get("title")]
    return steps


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
