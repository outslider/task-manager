"""コメント中の「@名前」から、呼ばれた人を割り出す。

氏名には空白が入る（「佐藤 花子」）ので、単語境界で切ると拾えない。
長い名前から順に前方一致で当てていく。表記ゆれ（全角＠、敬称、姓だけ）も
ある程度は拾うが、誤爆させたくないので曖昧一致まではやらない。
"""
import re

# 「@」のあとに続きうる区切り。ここで名前が終わったとみなす。
TRAILING = "、。，．,.!！?？:：;；)）]｝】>＞\"'　 \t\n"


def normalize(text):
    """全角＠を半角に、全角空白を半角に揃える。"""
    return (text or "").replace("＠", "@").replace("　", " ")


def candidates(people):
    """照合に使う「呼び名 → 利用者」の対応表。長い順に並べて返す。

    姓だけ・メールのローカル部だけといった短い呼び名は、**その中で一人に
    決まるときだけ**採用する。同姓が二人いるのに勝手にどちらかへ通知すると、
    宛先を間違えたまま気づけないため。

    people: [{"id","name","email"}, ...]
    """
    full, short = [], {}

    def offer(label, person):
        """短い呼び名の候補。重複したら後で捨てる。"""
        if not label:
            return
        short.setdefault(label, set()).add(person["id"])

    by_id = {}
    for person in people:
        by_id[person["id"]] = person
        name = (person.get("name") or "").strip()
        email = (person.get("email") or "").strip()
        if name:
            full.append((name, person))
            packed = name.replace(" ", "")
            if packed != name:
                full.append((packed, person))        # 「佐藤花子」と詰めて書く人向け
            family = name.split(" ")[0]
            if family and family != name:
                offer(family, person)                # 姓だけで呼ぶ場合
        if email:
            full.append((email, person))
            offer(email.split("@")[0], person)

    table = list(full)
    for label, owners in short.items():
        if len(owners) == 1:
            table.append((label, by_id[next(iter(owners))]))
    # 長い呼び名から先に当てる（「佐藤」で「佐藤 花子」を食い潰さないため）
    table.sort(key=lambda pair: len(pair[0]), reverse=True)
    return table


def find(body, people):
    """本文から呼ばれた人を探す。戻り値は (利用者の一覧, 見つかった呼び名の一覧)。"""
    text = normalize(body)
    table = candidates(people)
    found, labels = {}, []
    for match in re.finditer(r"@", text):
        rest = text[match.end():]
        if not rest:
            continue
        for label, person in table:
            if not rest.startswith(label):
                continue
            after = rest[len(label):len(label) + 1]
            if after and after not in TRAILING and not _is_boundary(label, after):
                continue        # 「@佐藤さん」は可、「@佐藤花」のような途中一致は不可
            found[person["id"]] = person
            labels.append(label)
            break
    return list(found.values()), labels


def _is_boundary(label, after):
    """敬称（さん・様など）が続く場合は、そこで名前が終わったとみなす。"""
    return after in "さ様君ちく"


def strip_mentions(body, people):
    """通知の件名などに使う、@ を落とした本文。"""
    text = normalize(body)
    for label, _person in candidates(people):
        text = text.replace("@" + label, label)
    return text
