"""自然言語からのタスク解析と、タスクの分解提案（ルールベース）。

外部サービスを使わずに動く実装。LLM を有効にした場合は app/llm.py が
この結果を上書きし、失敗したときはここへフォールバックする。
"""
import re
import unicodedata
from datetime import date, timedelta

WEEKDAYS = "月火水木金土日"

# カテゴリ推定に使うキーワード（app.api.CATEGORIES の値と対応）
CATEGORY_KEYWORDS = {
    "research": ["調査", "リサーチ", "検討", "調べ", "比較", "選定", "分析", "情報収集"],
    "design": ["設計", "企画", "構成", "要件定義", "デザイン", "方針", "検討会", "たたき台"],
    "build": ["実装", "構築", "開発", "作成", "修正", "改修", "移行", "セットアップ",
              "リリース", "デプロイ", "テスト", "コーディング"],
    "docs": ["資料", "ドキュメント", "マニュアル", "手順書", "議事録", "報告書", "仕様書",
             "スライド", "説明書", "記事"],
    "meeting": ["会議", "打ち合わせ", "ミーティング", "MTG", "定例", "面談", "レビュー会",
                "キックオフ", "説明会", "商談"],
    "admin": ["申請", "稟議", "経費", "精算", "手続", "提出", "契約", "発注", "請求",
              "見積", "承認", "届出"],
    "incident": ["障害", "トラブル", "不具合", "バグ", "復旧", "エラー", "ダウン", "事故",
                 "インシデント"],
}

IMPORTANCE_KEYWORDS = {
    3: ["至急", "緊急", "最優先", "今すぐ", "大至急", "クリティカル"],
    2: ["重要", "優先", "急ぎ", "早めに", "なるはや"],
    0: ["低優先", "後回し", "いつか", "余裕があれば", "急がない"],
}

MILESTONE_KEYWORDS = ["マイルストーン", "納期", "リリース日", "締め切り日", "節目", "完了期限"]


def normalize(text):
    """全角英数を半角にし、余分な空白を潰す。"""
    return re.sub(r"[ \t　]+", " ", unicodedata.normalize("NFKC", text or "")).strip()


# --------------------------------------------------------------------------
# 日付
# --------------------------------------------------------------------------

def _week_start(base):
    return base - timedelta(days=base.weekday())


def _weekday_date(base, weekday, offset_weeks=None):
    """「来週金曜」「今週月曜」「金曜」を日付にする。"""
    if offset_weeks is None:
        ahead = (weekday - base.weekday()) % 7
        return base + timedelta(days=ahead)
    return _week_start(base) + timedelta(days=offset_weeks * 7 + weekday)


def _month_end(base, months_ahead=0):
    month = base.month + months_ahead
    year = base.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    next_month = date(year + (month // 12), month % 12 + 1, 1)
    return next_month - timedelta(days=1)


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


RELATIVE_WEEKS = {"今週": 0, "来週": 1, "再来週": 2, "次週": 1}

# (正規表現, 日付を返す関数) を順に評価する。先に書いたものほど優先。
DATE_RULES = [
    (r"(今日|本日)", lambda m, b: b),
    (r"(明日|あした)", lambda m, b: b + timedelta(days=1)),
    (r"(明後日|あさって)", lambda m, b: b + timedelta(days=2)),
    (r"(今週末|週末)", lambda m, b: _weekday_date(b, 5)),
    (r"(今週|来週|再来週|次週)\s*の?\s*([月火水木金土日])曜?日?",
     lambda m, b: _weekday_date(b, WEEKDAYS.index(m.group(2)), RELATIVE_WEEKS[m.group(1)])),
    (r"(今週|来週|再来週|次週)\s*(?:中|いっぱい)?(?![月火水木金土日])",
     lambda m, b: _week_start(b) + timedelta(days=RELATIVE_WEEKS[m.group(1)] * 7 + 4)),
    (r"(?:今度|次)\s*の\s*([月火水木金土日])曜?日?",
     lambda m, b: _weekday_date(b + timedelta(days=1), WEEKDAYS.index(m.group(1)))),
    (r"今月末", lambda m, b: _month_end(b)),
    (r"来月末", lambda m, b: _month_end(b, 1)),
    (r"月末", lambda m, b: _month_end(b)),
    (r"来月", lambda m, b: _month_end(b, 1)),
    (r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?",
     lambda m, b: _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    (r"(\d{1,2})[/月](\d{1,2})日?", lambda m, b: _next_occurrence(b, int(m.group(1)), int(m.group(2)))),
    (r"(\d{1,3})\s*日後", lambda m, b: b + timedelta(days=int(m.group(1)))),
    (r"(\d{1,2})\s*週間後", lambda m, b: b + timedelta(weeks=int(m.group(1)))),
    (r"(\d{1,2})\s*[ヶかカ]?月後", lambda m, b: _month_end(b, int(m.group(1)))),
    (r"([月火水木金土日])曜日?", lambda m, b: _weekday_date(b, WEEKDAYS.index(m.group(1)))),
]


def _next_occurrence(base, month, day):
    """月日だけの指定は、過ぎていたら翌年とみなす。"""
    candidate = _safe_date(base.year, month, day)
    if candidate is None:
        return None
    if (base - candidate).days > 180:
        return _safe_date(base.year + 1, month, day)
    return candidate


def find_dates(text, base=None):
    """テキスト中の日付表現を (日付, 開始位置, 終了位置, 種別) の一覧で返す。"""
    base = base or date.today()
    found = []
    taken = []

    def overlaps(start, end):
        return any(not (end <= s or start >= e) for s, e in taken)

    for pattern, resolver in DATE_RULES:
        for match in re.finditer(pattern, text):
            if overlaps(match.start(), match.end()):
                continue
            try:
                value = resolver(match, base)
            except (ValueError, KeyError):
                value = None
            if value is None:
                continue
            # 「今週中」のように相対表現が過ぎた日を指す場合は今日に丸める
            if value < base and not re.search(r"\d", match.group(0)):
                value = base
            tail = text[match.end():match.end() + 4]
            head = text[max(0, match.start() - 3):match.start()]
            if re.match(r"\s*(から|より)", tail) or "開始" in tail or "開始" in head:
                kind = "start"
            else:
                kind = "due"
            taken.append((match.start(), match.end()))
            found.append({"date": value, "start": match.start(), "end": match.end(),
                          "kind": kind})
    found.sort(key=lambda d: d["start"])
    return found


# --------------------------------------------------------------------------
# 解析本体
# --------------------------------------------------------------------------

def parse(text, users=(), projects=(), base=None, default_project_id=None):
    """自然言語1行からタスクの下書きを作る。

    users:    [{"id":.., "name":..}]
    projects: [{"id":.., "name":..}]
    """
    base = base or date.today()
    raw = normalize(text)
    if not raw:
        return {"title": "", "confidence": 0.0, "matched": []}

    working = raw
    matched = []
    draft = {
        "title": "", "description": "", "category": "", "status": "todo", "priority": 1,
        "assignee_id": None, "start_date": None, "due_date": None, "progress": 0,
        "is_milestone": False, "project_id": default_project_id,
    }

    # --- 複数行なら 2 行目以降はメモ扱い ---
    if "\n" in text:
        head, _, rest = text.partition("\n")
        working = normalize(head)
        draft["description"] = rest.strip()

    # --- 日付 ---
    for item in find_dates(working, base):
        key = "start_date" if item["kind"] == "start" else "due_date"
        if draft[key] is None:
            draft[key] = item["date"].isoformat()
            matched.append({"field": key, "text": working[item["start"]:item["end"]]})
    # 「〜まで」「〜までに」などの助詞を後で落とすため位置を保持
    spans = [(d["start"], d["end"]) for d in find_dates(working, base)]

    # --- 担当者 ---
    for user in users:
        name = normalize(user["name"])
        candidates = [name, name.replace(" ", "")] + name.split()
        for candidate in candidates:
            if len(candidate) < 2:
                continue
            pattern = r"(?:@|担当[:：]?\s*)?" + re.escape(candidate) + r"\s*(?:さん|君|氏)?"
            match = re.search(pattern, working)
            if match:
                draft["assignee_id"] = user["id"]
                matched.append({"field": "assignee_id", "text": match.group(0).strip()})
                spans.append((match.start(), match.end()))
                break
        if draft["assignee_id"]:
            break

    # --- プロジェクト ---
    for project in projects:
        name = normalize(project["name"])
        if len(name) >= 2 and name in working:
            draft["project_id"] = project["id"]
            matched.append({"field": "project_id", "text": name})
            break

    # --- 重要度 ---
    for level, words in IMPORTANCE_KEYWORDS.items():
        hit = next((w for w in words if w in working), None)
        if hit:
            draft["priority"] = level
            matched.append({"field": "priority", "text": hit})
            index = working.index(hit)
            spans.append((index, index + len(hit)))
            break

    # --- カテゴリ ---
    scores = {}
    for category, words in CATEGORY_KEYWORDS.items():
        score = sum(1 for w in words if w in working)
        if score:
            scores[category] = score
    if scores:
        draft["category"] = max(scores, key=scores.get)
        matched.append({"field": "category", "text": draft["category"]})

    # --- マイルストーン ---
    if any(word in working for word in MILESTONE_KEYWORDS):
        draft["is_milestone"] = True
        matched.append({"field": "is_milestone", "text": "マイルストーン"})

    # --- タイトル（認識した部分を削る） ---
    draft["title"] = _clean_title(working, spans) or raw
    draft["confidence"] = _confidence(draft, matched)
    draft["matched"] = matched
    draft["engine"] = "rule"
    return draft


TRAILING_NOISE = re.compile(
    r"^(?:まで(?:に)?|から|の|を|は|が|に|へ|と|で|、|。|・|\s)+|"
    r"(?:まで(?:に)?|から|、|。|・|\s)+$")
TASK_VERB_TAIL = re.compile(
    r"(?:する|やる)?\s*(?:こと|件|の件|タスク|を(?:お願い|依頼)します?|"
    r"お願いします?|してください|して|ください)\s*[。．]?$")


def _clean_title(text, spans):
    """認識済みの語を取り除き、依頼文の言い回しを落として名詞句に寄せる。"""
    if spans:
        keep, last = [], 0
        for start, end in sorted(spans):
            start, end = max(start, 0), min(end, len(text))
            if start < last:
                continue
            keep.append(text[last:start])
            last = end
        keep.append(text[last:])
        text = " ".join(part.strip() for part in keep if part.strip())
    text = TRAILING_NOISE.sub("", text)
    text = TASK_VERB_TAIL.sub("", text).strip()
    text = TRAILING_NOISE.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" 　・、。")


def _confidence(draft, matched):
    """どれだけ具体的に読み取れたか（0.0-1.0）。UI で控えめに出すための目安。"""
    score = 0.35
    if draft["due_date"] or draft["start_date"]:
        score += 0.25
    if draft["assignee_id"]:
        score += 0.2
    if draft["category"]:
        score += 0.1
    if len(draft["title"]) >= 4:
        score += 0.1
    return round(min(score, 0.95), 2)


# --------------------------------------------------------------------------
# タスク分解
# --------------------------------------------------------------------------
# 「大きな作業」を定型の手順に割る辞書。キーワードに一致したテンプレートを使う。
# steps は (タスク名, カテゴリ, 相対的な重み) で、重みで期間を按分する。

TEMPLATES = [
    {
        "name": "サーバー・システム移行",
        "keywords": ["サーバ移行", "サーバー移行", "システム移行", "移行", "リプレース",
                     "マイグレーション", "入れ替え"],
        "steps": [
            ("現行環境の調査・棚卸し", "research", 2),
            ("移行計画と切り戻し手順の作成", "design", 2),
            ("移行先環境の構築", "build", 3),
            ("事前バックアップの取得", "build", 1),
            ("検証環境での移行リハーサル", "build", 3),
            ("関係者への切替日程の周知", "meeting", 1),
            ("本番切替作業", "build", 2),
            ("疎通確認・動作検証", "build", 2),
            ("監視・バックアップ設定の移行", "build", 1),
            ("旧環境の停止", "build", 1),
            ("移行完了報告", "docs", 1),
        ],
    },
    {
        "name": "リリース・デプロイ",
        "keywords": ["リリース", "デプロイ", "公開", "ローンチ", "本番反映"],
        "steps": [
            ("リリース内容の確定", "design", 1),
            ("リリース手順書の作成", "docs", 1),
            ("ステージング環境での検証", "build", 2),
            ("関係者への告知", "meeting", 1),
            ("リリース作業", "build", 1),
            ("リリース後の稼働確認", "build", 1),
            ("振り返り", "meeting", 1),
        ],
    },
    {
        "name": "障害・トラブル対応",
        "keywords": ["障害", "トラブル", "不具合", "インシデント", "復旧", "事故"],
        "steps": [
            ("一次対応・影響の切り分け", "incident", 1),
            ("影響範囲と影響ユーザーの特定", "incident", 1),
            ("暫定対処の実施", "incident", 1),
            ("原因調査", "incident", 2),
            ("恒久対策の実施", "build", 2),
            ("再発防止策の検討", "design", 1),
            ("報告書の作成・共有", "docs", 1),
        ],
    },
    {
        "name": "調査・比較検討",
        "keywords": ["調査", "検討", "選定", "比較", "リサーチ", "評価", "PoC"],
        "steps": [
            ("調査目的と評価軸の整理", "design", 1),
            ("候補の洗い出し", "research", 2),
            ("情報収集・ヒアリング", "research", 2),
            ("比較表の作成", "docs", 1),
            ("試用・検証", "research", 2),
            ("結果のまとめと提案", "docs", 1),
            ("方針決定の会議", "meeting", 1),
        ],
    },
    {
        "name": "ツール・サービス導入",
        "keywords": ["導入", "入れる", "契約して", "サブスク", "ライセンス購入"],
        "steps": [
            ("要件の整理", "design", 1),
            ("製品・ベンダーの比較", "research", 2),
            ("トライアルでの検証", "research", 2),
            ("見積取得", "admin", 1),
            ("稟議・社内承認", "admin", 2),
            ("契約手続き", "admin", 1),
            ("初期設定・アカウント発行", "build", 2),
            ("利用者への展開・説明会", "meeting", 1),
        ],
    },
    {
        "name": "資料・ドキュメント作成",
        "keywords": ["資料", "ドキュメント", "マニュアル", "手順書", "提案書", "報告書",
                     "スライド", "仕様書"],
        "steps": [
            ("目的と読み手の確認", "design", 1),
            ("構成案の作成", "design", 1),
            ("情報・データの収集", "research", 2),
            ("ドラフト作成", "docs", 3),
            ("レビュー依頼", "meeting", 1),
            ("指摘反映・最終化", "docs", 1),
        ],
    },
    {
        "name": "開発・機能追加",
        "keywords": ["開発", "実装", "機能追加", "構築", "作成する", "改修"],
        "steps": [
            ("要件の整理", "design", 1),
            ("設計", "design", 2),
            ("実装", "build", 4),
            ("テスト", "build", 2),
            ("コードレビュー", "build", 1),
            ("ドキュメント更新", "docs", 1),
            ("リリース", "build", 1),
        ],
    },
    {
        "name": "監査・セキュリティ対応",
        "keywords": ["監査", "セキュリティ", "脆弱性", "ISMS", "Pマーク", "点検"],
        "steps": [
            ("対象範囲の確認", "design", 1),
            ("現状の棚卸し", "research", 2),
            ("指摘事項・差分の整理", "docs", 1),
            ("対応計画の作成", "design", 1),
            ("対応の実施", "build", 3),
            ("エビデンスの収集", "docs", 1),
            ("報告・是正完了の確認", "meeting", 1),
        ],
    },
    {
        "name": "契約・購買手続き",
        "keywords": ["契約", "更新手続", "発注", "購買", "見積", "稟議", "申請"],
        "steps": [
            ("条件・要件の確認", "design", 1),
            ("見積の取得", "admin", 1),
            ("社内稟議の申請", "admin", 2),
            ("契約書の法務レビュー", "admin", 2),
            ("契約締結", "admin", 1),
            ("管理台帳の更新", "admin", 1),
        ],
    },
    {
        "name": "採用",
        "keywords": ["採用", "求人", "募集", "面接"],
        "steps": [
            ("採用要件の定義", "design", 1),
            ("募集要項の作成", "docs", 1),
            ("媒体選定・掲載", "admin", 1),
            ("書類選考", "admin", 2),
            ("面接の実施", "meeting", 3),
            ("条件提示・内定", "admin", 1),
            ("入社手続き・受入準備", "admin", 2),
        ],
    },
    {
        "name": "イベント・セミナー運営",
        "keywords": ["イベント", "セミナー", "説明会", "展示会", "勉強会", "研修"],
        "steps": [
            ("企画・目的の整理", "design", 1),
            ("日程と会場の確保", "admin", 1),
            ("集客・案内の送付", "admin", 2),
            ("当日資料の準備", "docs", 2),
            ("リハーサル", "meeting", 1),
            ("当日運営", "meeting", 1),
            ("アンケート集計・振り返り", "docs", 1),
        ],
    },
    {
        "name": "引き継ぎ",
        "keywords": ["引き継ぎ", "引継ぎ", "異動", "退職"],
        "steps": [
            ("担当業務の棚卸し", "research", 2),
            ("引き継ぎ資料の作成", "docs", 3),
            ("説明・レクチャー", "meeting", 2),
            ("並走期間でのフォロー", "meeting", 2),
            ("引き継ぎ完了の確認", "admin", 1),
        ],
    },
]

GENERIC_STEPS = [
    ("準備・前提の確認", "design", 1),
    ("実施", "build", 3),
    ("確認・レビュー", "research", 1),
    ("完了報告・共有", "docs", 1),
]


def _match_template(title):
    text = normalize(title)
    best, best_score = None, 0
    for template in TEMPLATES:
        score = 0
        for keyword in template["keywords"]:
            if keyword in text:
                score = max(score, len(keyword))
        if score > best_score:
            best, best_score = template, score
    return best


def decompose(title, start_date=None, due_date=None, category=""):
    """大きなタスクを子タスク候補に割る。

    期間が分かっていれば、重みに応じて各ステップに日程を割り当てる。
    """
    template = _match_template(title)
    steps = template["steps"] if template else GENERIC_STEPS
    source = template["name"] if template else "汎用"

    start = _parse_iso(start_date)
    due = _parse_iso(due_date)
    schedule = _spread(steps, start, due)

    items = []
    for index, ((name, step_category, _weight), dates) in enumerate(zip(steps, schedule)):
        items.append({
            "title": name,
            "category": step_category or category,
            "start_date": dates[0].isoformat() if dates[0] else None,
            "due_date": dates[1].isoformat() if dates[1] else None,
            "sort_order": (index + 1) * 10,
        })
    return {
        "items": items,
        "template": source,
        "matched": template is not None,
        "engine": "rule",
    }


def _parse_iso(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _spread(steps, start, due):
    """親タスクの期間を重みで按分して、各ステップの開始日と期限を決める。"""
    if not start or not due or due < start:
        return [(None, None) for _ in steps]
    total_days = (due - start).days + 1
    total_weight = sum(max(1, weight) for _, _, weight in steps)
    result = []
    cursor = start
    for index, (_, _, weight) in enumerate(steps):
        share = max(1, round(total_days * max(1, weight) / total_weight))
        step_start = cursor
        step_due = min(due, step_start + timedelta(days=share - 1))
        if index == len(steps) - 1:
            step_due = due
        result.append((step_start, step_due))
        cursor = min(due, step_due + timedelta(days=1))
    return result
