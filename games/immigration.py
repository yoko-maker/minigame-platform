"""AI入国審査官。

限られた情報（提出書類・会話ログ）から、入国者が「AI」か「人間」かを
見抜く推理ゲーム。外部APIは使わず、乱数と定型応答テンプレートだけで
AI/人間の応答らしさを演出する（ルールベース）。

契約:
- `render()` を公開する（引数なし）。app.py から呼ばれる。
- 画面冒頭で `utils.ui.game_header("🛂 AI入国審査官", NAME, how_to_play=...)` を呼ぶ。
- 状態は `utils.state.game_state(NAME, ...)` が返す dict に保存する。
- 全ウィジェットの key は "imm_" で始める。

設計メモ:
- `generate_arrival` / `answer_question` / `judge` は st に依存しない純粋関数。
  round ごとに再現可能にするため、乱数は毎回 `random.Random(seed_式)` を
  その場で生成して渡す（Random インスタンス自体は state に保存しない）。
- state["seed"] がゲーム全体の基準乱数シード。入国者ID・質問トピック・
  質問回数から決定的にサブシードを導出することで、同じ入力なら同じ
  応答が得られるようにしている。
"""

from __future__ import annotations

import random
import string
import time
from typing import Any

import streamlit as st

from utils import state, ui

NAME = "immigration"

# ---------------------------------------------------------------------------
# ゲームパラメータ
# ---------------------------------------------------------------------------

TOTAL_ARRIVALS = 5          # 既定値。実際の値は難易度から取る
QUESTIONS_PER_ARRIVAL = 3
WIN_THRESHOLD = 3

# 1人あたりの持ち時間。時間内に判定できなければ「見逃し」＝不正解になる。
# 後ろに列ができている審査ブースなので、迷い続けること自体が失点になる。
TIME_LIMIT_SEC = 60.0
TIME_WARN_SEC = 15.0        # ここを切ったら警告表示に変える

# 難易度。審査する人数・持ち時間・質問できる回数・合格ライン、そして
# 「AIがどれだけ尻尾を出すか」が変わる。上の難易度ほど手がかりが薄くなる。
DIFFICULTIES: dict[str, dict[str, Any]] = {
    "easy": {
        "label": "新人審査官",
        "emoji": "🔰",
        "arrivals": 5,
        "questions": 4,
        "time": 90.0,
        "win": 3,
        "ai_tell": 0.75,   # AIが不自然な応答/書類不備を出す確率
        "human_tell": 0.10,
        "check_cost": 22.5,     # 精密照会の消費秒（time の25%）
        "check_accuracy": 0.85,  # 精密照会がAIを正しく言い当てる確率
        "desc": "5人を審査。1人90秒、質問4回。AIはあからさまに尻尾を出す。",
    },
    "normal": {
        "label": "審査官",
        "emoji": "🎯",
        "arrivals": 5,
        "questions": 3,
        "time": 60.0,
        "win": 3,
        "ai_tell": 0.60,
        "human_tell": 0.15,
        "check_cost": 15.0,
        "check_accuracy": 0.75,
        "desc": "5人を審査。1人60秒、質問3回。標準的な配属先。",
    },
    "hard": {
        "label": "主任審査官",
        "emoji": "🔥",
        "arrivals": 7,
        "questions": 3,
        "time": 45.0,
        "win": 5,
        "ai_tell": 0.48,
        "human_tell": 0.22,
        "check_cost": 11.25,
        "check_accuracy": 0.65,
        "desc": "7人を審査。1人45秒、質問3回。AIの偽装が巧妙になり、人間にも挙動不審が混じる。",
    },
    "expert": {
        "label": "国境監察官",
        "emoji": "💀",
        "arrivals": 8,
        "questions": 2,
        "time": 35.0,
        "win": 6,
        "ai_tell": 0.40,
        "human_tell": 0.30,
        "check_cost": 8.75,
        "check_accuracy": 0.55,
        "desc": "8人を審査。1人35秒、質問2回。書類も会話もほとんど当てにならない。",
    },
}
DEFAULT_DIFFICULTY = state.DEFAULT_LEVEL


def settings_for(level: str) -> dict[str, Any]:
    """難易度の設定を取り出す（未知のキーは標準に落とす）。"""
    return DIFFICULTIES.get(level, DIFFICULTIES[DEFAULT_DIFFICULTY])

# 時計は「審査画面を開いている間」だけ進める。1秒ごとに刻むので、これを超える
# 空白はページを見ていなかった時間とみなして数えない（ホームへ戻っている間に
# 時間切れになるのを防ぐ）。
MAX_TICK_STEP = 2.0

TOPICS = ["hobby", "family", "food", "yesterday", "childhood"]

TOPIC_LABELS = {
    "hobby": "趣味について",
    "family": "家族について",
    "food": "好きな食べ物について",
    "yesterday": "昨日の出来事について",
    "childhood": "子供時代について",
}

# ---------------------------------------------------------------------------
# データ表（架空の国・氏名プール。特定の実在国/民族への偏見を避けるため
# すべて架空名とする）
# ---------------------------------------------------------------------------

NATIONALITIES = [
    "ザンドラ王国",
    "エルビア共和国",
    "ノルグレン連合",
    "サウスヴァレー連邦",
    "キタミナ諸島",
    "フォレスタ公国",
]

FAMILY_NAMES = [
    "ハーヴェイ", "モレノ", "キンジョウ", "ラーソン", "ベルトラン",
    "ノヴァク", "シュタイン", "アルバレス", "タカクラ", "ミュラー",
]

GIVEN_NAMES = [
    "アレン", "ミア", "ソウタ", "エレナ", "カイ",
    "ノア", "ユナ", "ルカ", "サラ", "レン", "イヴ", "トム",
]

VISA_TYPES = ["観光", "商用", "留学", "就労", "外交"]

HOBBIES = [
    "読書", "登山", "料理", "写真撮影", "ガーデニング",
    "釣り", "映画鑑賞", "楽器演奏", "陶芸", "サイクリング",
]

FINGERPRINT_ANOMALIES = ["軽微な不一致あり", "読み取り不安定", "登録データと部分的に相違"]
PHOTO_ANOMALIES = ["表情がやや不自然", "陰影の付き方に違和感", "経年変化にしては差異が大きい"]

# 精密照会（セカンダリチェック）のフレーバー文言。結果そのものは deep_check() が
# 決める。ここは「何を調べ直したか」の演出だけ。
DEEP_CHECK_FLAVORS = [
    "渡航記録の再照会",
    "指紋の再スキャン",
    "顔写真の再照合",
    "本国データベースとの照会",
]

# 審査を行っている「今年」。生年月日から年齢を出すのに使う。
CURRENT_YEAR = 2026

# ビザ種別と、それに見合う滞在目的。ここがズレていれば書類と話が食い違っている。
VISA_PURPOSE = {
    "観光": "観光",
    "商用": "商談",
    "留学": "進学",
    "就労": "赴任",
    "外交": "公務",
}

# 書類と会話をまたぐ矛盾。会話の不自然さ（曖昧な手がかり）と違い、
# こちらは確実な証拠になる。ただし対応する話題を質問しないと露見しない。
CROSS_CONTRADICTIONS = {
    "nationality": {
        "topic": "childhood",
        "doc": "国籍",
        "hint": "語った出身地が、書類の国籍と違う国だった",
    },
    "age": {
        "topic": "childhood",
        "doc": "生年月日",
        "hint": "語った年代が、書類の生年月日から計算した年齢と合わない",
    },
    "visa": {
        "topic": "yesterday",
        "doc": "ビザ種別",
        "hint": "語った滞在目的が、書類のビザ種別と噛み合わない",
    },
}

CROSS_TEMPLATES = {
    "nationality": [
        "子供の頃は{other}の田舎で育ちました。近所の川でよく遊んでいましたね。",
        "{other}で生まれ育ちました。あの町の景色は今でも覚えています。",
    ],
    "age": [
        "{decade}年代の子供でしたから、遊びといえば外を走り回ることでした。",
        "{decade}年代生まれなので、その頃の流行はよく覚えています。",
    ],
    "visa": [
        "昨日は{purpose}の打ち合わせで一日潰れました。今回もその続きです。",
        "昨日から{purpose}の予定が詰まっていて、休む暇もありません。",
    ],
}

# トピックごとの応答テンプレート。
# natural  = 人間らしい/自然な応答（人間で出やすいが、AIでも稀に出る）
# unnatural = ぎこちない/機械的な応答（AIで出やすいが、人間でも稀に出る）
RESPONSE_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "hobby": {
        "natural": [
            "休日はよく{hobby}をして過ごします。没頭すると時間を忘れてしまいますね。",
            "{hobby}が好きで、もう何年も続けています。下手の横好きですが。",
            "最近は{hobby}にハマっていて、友人にもよく誘われます。",
        ],
        "unnatural": [
            "趣味は{hobby}です。当該活動は精神的充足度を最適化します。",
            "{hobby}、を、行います。理由は……楽しい、から、です。",
            "趣味の質問ですね。統計的に人気の高い{hobby}と回答しておきます。",
        ],
    },
    "family": {
        "natural": [
            "両親と姉が一人います。実家には年に数回帰るくらいですね。",
            "家族は仲が良い方だと思います。母がよく心配性で電話してきます。",
            "妻と息子が待っています。早く帰ってあげたいです。",
        ],
        "unnatural": [
            "家族構成は父・母・兄弟の3名で構成されています。関係性は良好です。",
            "家族、については……プライベートな情報のため詳細は控えます。",
            "家族はいます。一般的な核家族の形態に該当します。",
        ],
    },
    "food": {
        "natural": [
            "母が作ってくれたシチューが一番好きです。恋しくなります。",
            "実は辛い物が苦手で、いつも人より控えめに頼みます。",
            "旅行先の屋台料理が忘れられません。あの味をまた食べたいです。",
        ],
        "unnatural": [
            "好きな食べ物は栄養価の高いものを選定しています。",
            "食べ物への嗜好はデータ不足のため一般的な回答をします。米、パン、麺類です。",
            "特に好みはありません。摂取できれば問題ありません。",
        ],
    },
    "yesterday": {
        "natural": [
            "昨日は友人とカフェで長話をしていました。気づいたら3時間経っていました。",
            "実は昨日は寝坊してしまって、慌てて準備していました。",
            "近所を散歩して、それから溜まっていた洗濯をしました。",
        ],
        "unnatural": [
            "昨日のログは……えっと、通常通りの一日でした。詳細は割愛します。",
            "昨日の行動記録は特筆すべき点がありませんでした。",
            "昨日、という概念について、時間の経過を認識しています。特に何もしていません。",
        ],
    },
    "childhood": {
        "natural": [
            "近所の川でよく友達と遊んでいました。よく怒られましたね。",
            "引っ込み思案な子供でしたが、絵を描くのが好きでした。",
            "田舎育ちで、夏休みは虫取りばかりしていました。",
        ],
        "unnatural": [
            "子供時代のデータは限定的です。一般的な成長過程を経たと認識しています。",
            "幼少期の記憶については、確認に時間を要します。",
            "子供の頃、について。特に語るべきエピソードは保持していません。",
        ],
    },
}

HOW_TO_PLAY = """
- 入国審査官として、次々にやってくる入国者が **AI** か **人間** かを見抜きましょう。
- 左側の「提出書類」（国籍・生年月日・ビザ種別・指紋照合・顔写真照合）を確認します。
- 中央下のボタンで質問すると、応答が会話ログに追記されます。判定は
  「🤖 AIと判定」「🧑 人間と判定」から。判定すると正解が発表されます。

**手がかりは2種類あります**

1. **受け答えの違和感**（曖昧な手がかり）
   AI はぎこちない言い回しをしがちですが、**人間でも稀に不自然な受け答えをします。**
   指紋や写真の照合不一致も同様で、これだけでは決め手になりません。

2. **書類と会話の食い違い**（確実な証拠）
   語った出身地が書類の国籍と違う、語った年代が生年月日と合わない、話す滞在目的が
   ビザ種別と噛み合わない——**こうした矛盾は人間には起こりません。見つけたらAIです。**
   ただし対応する話題を質問しないと表に出ません。限られた質問をどこに使うかが鍵です。

**🗂 精密照会（セカンダリチェック）**
1人につき1回だけ、残り時間と引き換えに追加照会ができます。確度つきの
手がかりが得られますが、これも確定情報ではありません（人間でも稀に
「AI寄り」と誤って出ることがあります）。時間を使ってでも情報を買うか、
それとも判断を急ぐか——立ち回りが問われます。

**時間制限**
1人あたりの持ち時間があります。残り時間がゼロになると判定できないまま見逃した
ことになり、不正解として数えられます。後ろには列ができています。迷い続けること自体が失点です。

**難易度によって、審査人数・持ち時間・質問回数・合格ライン、そして
AIがどれだけ尻尾を出すかが変わります。**
"""


# ---------------------------------------------------------------------------
# 純粋ロジック関数（st に依存しない。単体テスト可能）
# ---------------------------------------------------------------------------

def generate_arrival(
    rng: random.Random,
    arrival_no: int,
    ai_tell: float = 0.60,
    human_tell: float = 0.15,
) -> dict[str, Any]:
    """乱数 rng から1人分の入国者データを生成する。

    Args:
        ai_tell: AI が尻尾（書類の不整合・不自然な応答・書類との矛盾）を出す確率。
        human_tell: 人間がノイズとして不整合を出す確率。

    AI の場合、指紋/顔写真照合に不整合が出やすい（ただし確定ではない）。
    加えて AI には「書類と会話の食い違い」が仕込まれることがある。こちらは
    人間には決して起きないので、見つけられれば確実な証拠になる。ただし
    対応する話題を質問しなければ表に出ない。
    """
    is_ai = rng.random() < 0.5
    nationality = rng.choice(NATIONALITIES)
    given = rng.choice(GIVEN_NAMES)
    family = rng.choice(FAMILY_NAMES)
    name = f"{family} {given}"

    birth_year = rng.randint(1955, 2005)
    birth_month = rng.randint(1, 12)
    birth_day = rng.randint(1, 28)
    dob = f"{birth_year}-{birth_month:02d}-{birth_day:02d}"

    passport_no = f"{rng.choice(string.ascii_uppercase)}{rng.randint(1_000_000, 9_999_999)}"
    visa_type = rng.choice(VISA_TYPES)
    hobby = rng.choice(HOBBIES)

    # AI は書類に不整合が出やすいが、確定情報にはしない（人間でもノイズとして稀に発生）。
    anomaly_prob = ai_tell if is_ai else human_tell
    fingerprint_ok = rng.random() >= anomaly_prob
    photo_ok = rng.random() >= anomaly_prob

    fingerprint_note = "一致" if fingerprint_ok else rng.choice(FINGERPRINT_ANOMALIES)
    photo_note = "一致" if photo_ok else rng.choice(PHOTO_ANOMALIES)

    # 書類と会話の矛盾は AI にしか起きない（人間は自分の経歴を間違えない）。
    contradiction = None
    cross_payload: dict[str, Any] = {}
    if is_ai and rng.random() < ai_tell:
        contradiction = rng.choice(list(CROSS_CONTRADICTIONS))
        if contradiction == "nationality":
            others = [n for n in NATIONALITIES if n != nationality]
            cross_payload["other"] = rng.choice(others)
        elif contradiction == "age":
            # 実際の生まれと 20 年以上ずれた年代を語らせる
            true_decade = birth_year // 10 * 10
            candidates = [d for d in range(1940, 2011, 10) if abs(d - true_decade) >= 20]
            cross_payload["decade"] = rng.choice(candidates)
        elif contradiction == "visa":
            others = [p for v, p in VISA_PURPOSE.items() if v != visa_type]
            cross_payload["purpose"] = rng.choice(others)

    return {
        "id": arrival_no,
        "is_ai": is_ai,
        "name": name,
        "nationality": nationality,
        "dob": dob,
        "birth_year": birth_year,
        "age": CURRENT_YEAR - birth_year,
        "passport_no": passport_no,
        "visa_type": visa_type,
        "fingerprint_note": fingerprint_note,
        "photo_note": photo_note,
        "hobby_seed": hobby,
        "contradiction": contradiction,
        "cross_payload": cross_payload,
    }


def answer_question(
    arrival: dict[str, Any],
    topic: str,
    rng: random.Random,
    ai_tell: float = 0.60,
    human_tell: float = 0.15,
) -> str:
    """入国者への質問(topic)に対する応答文を生成する。

    2種類の手がかりが出る。

    1. 口調の不自然さ … AI で出やすいが人間でも稀に出る。曖昧な手がかり。
    2. 書類との食い違い … その話題に矛盾が仕込まれていれば必ず出る。確実な証拠。
       口調はあくまで自然なので、書類を見比べないと気づけない。
    """
    if topic not in RESPONSE_TEMPLATES:
        raise ValueError(f"unknown topic: {topic}")

    cross = arrival.get("contradiction")
    if cross and CROSS_CONTRADICTIONS[cross]["topic"] == topic:
        template = rng.choice(CROSS_TEMPLATES[cross])
        return template.format(**arrival.get("cross_payload", {}))

    is_ai = arrival["is_ai"]
    natural_prob = (1.0 - ai_tell) if is_ai else (1.0 - human_tell)
    category = "natural" if rng.random() < natural_prob else "unnatural"
    template = rng.choice(RESPONSE_TEMPLATES[topic][category])
    return template.format(hobby=arrival.get("hobby_seed", "読書"))


def judge(arrival: dict[str, Any], guess_is_ai: bool) -> bool:
    """審査官の判定 guess_is_ai が正しければ True を返す。"""
    return guess_is_ai == arrival["is_ai"]


def tick_timer(s: dict[str, Any], now: float, max_step: float = MAX_TICK_STEP) -> float:
    """経過時間ぶんだけ持ち時間を減らし、残り秒数を返す。

    `now` を引数で受け取るので、実時間に依存せず単体で検証できる。

    審査中（phase == "asking"）以外では減らさない。また 1 回に減らせる量を
    max_step で頭打ちにしているため、画面を離れていた長い空白は加算されない。
    1秒ごとに刻まれている限り実時間どおりに減り、離席中は実質止まる。
    """
    last = s.get("last_tick")
    if last is None:
        last = now
    elapsed = min(max(0.0, now - last), max_step)
    s["last_tick"] = now

    if s.get("phase") == "asking" and not s.get("game_over"):
        s["time_left"] = max(0.0, float(s.get("time_left", TIME_LIMIT_SEC)) - elapsed)
    return float(s.get("time_left", TIME_LIMIT_SEC))


def spend_time(s: dict[str, Any], cost: float) -> float:
    """残り時間から cost 秒を消費し、消費後の残り秒数を返す。

    `tick_timer` と同じく `s["time_left"]` を直接操作する。精密照会など
    「時間を対価にする」アクションから呼ばれる。0未満にはならない。
    """
    s["time_left"] = max(0.0, float(s.get("time_left", 0.0)) - max(0.0, cost))
    return float(s["time_left"])


def deep_check(
    arrival: dict[str, Any],
    rng: random.Random,
    ai_tell: float = 0.60,
    human_tell: float = 0.15,
    accuracy: float = 0.75,
) -> dict[str, Any]:
    """精密照会（セカンダリチェック）の結果を1件返す。

    書類×会話の矛盾のような「確実な証拠」とは違い、あくまで確度つきの
    手がかりを返す純粋関数。AI は `accuracy` の確率で「ai寄り」の反応が出るが、
    人間側にも `human_tell` を土台にした確率で誤検知（ai寄りに出る）が起きるため、
    これ単体で確定情報にはならない。

    Args:
        accuracy: 対象が AI のとき「ai寄り」の反応が出る確率（難易度の check_accuracy）。

    Returns:
        {"text": 照会結果の表示文, "signal": "ai寄り"|"人間寄り"|"不明",
         "confidence": "低"|"中"|"高"}
    """
    is_ai = arrival["is_ai"]
    flavor = rng.choice(DEEP_CHECK_FLAVORS)

    # 人間側の誤検知率。human_tell を土台にしつつ、accuracy が低い（＝難しい
    # 難易度）ほど誤検知も増えるようにして、高難易度で照会の価値が下がるようにする。
    false_positive = min(0.45, human_tell + (1.0 - accuracy) * 0.3)
    hit_prob = accuracy if is_ai else false_positive

    if rng.random() < hit_prob:
        signal = "ai寄り"
    else:
        # 外れても即座に逆と分かるわけではなく、「不明」に割れることもある。
        signal = "不明" if rng.random() < 0.35 else "人間寄り"

    matched = (signal == "ai寄り") == is_ai
    conf_roll = rng.random()
    if signal == "不明":
        confidence = "低"
    elif matched:
        confidence = "高" if conf_roll < 0.65 else "中"
    else:
        confidence = "中" if conf_roll < 0.4 else "低"

    text = f"🗂 {flavor}の結果、**{signal}**の反応（確度：{confidence}）。"
    return {"text": text, "signal": signal, "confidence": confidence}


# ---------------------------------------------------------------------------
# state 初期化・遷移ヘルパー（st.session_state を扱う非純粋関数）
# ---------------------------------------------------------------------------

def _default_state() -> dict[str, Any]:
    return {
        "seed": random.randint(0, 10_000_000),
        "difficulty": DEFAULT_DIFFICULTY,
        "round_no": 0,           # 0-based: 何人目を審査中か
        "score": 0,               # 正解数
        "arrivals_judged": 0,     # 判定済み人数
        "current_arrival": None,  # 現在の入国者データ (dict) or None
        "questions_left": QUESTIONS_PER_ARRIVAL,
        "asked_topics": [],       # 既に質問したtopicのリスト
        "log": [],                # [(topic_label, answer), ...]
        # {"correct": bool, "was_ai": bool, "timeout": bool} or None
        "last_result": None,
        "game_over": False,
        "phase": "asking",        # "asking" | "result"
        "time_left": TIME_LIMIT_SEC,
        "last_tick": None,        # 直近に時計を刻んだ時刻（未開始は None）
        "timeouts": 0,            # 時間切れで見逃した人数
        "deep_check_used": False,   # この入国者に精密照会を使ったか（1人1回）
        "deep_check_result": None,  # 直近の精密照会結果 dict or None
        "best_recorded": False,     # ゲーム終了時の自己ベスト記録が済んだか
    }


def _start_next_arrival(s: dict[str, Any]) -> None:
    """次の入国者を生成して state にセットする。持ち時間もここで巻き戻す。"""
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    round_no = s["round_no"]
    rng = random.Random(s["seed"] * 1_000_003 + round_no * 97 + 1)
    s["current_arrival"] = generate_arrival(rng, round_no, cfg["ai_tell"], cfg["human_tell"])
    s["questions_left"] = cfg["questions"]
    s["asked_topics"] = []
    s["log"] = []
    s["phase"] = "asking"
    s["time_left"] = cfg["time"]
    s["last_tick"] = None
    s["deep_check_used"] = False
    s["deep_check_result"] = None


def _ask(s: dict[str, Any], arrival: dict[str, Any], topic: str) -> None:
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    topic_idx = TOPICS.index(topic)
    ask_count = len(s["asked_topics"])
    rng = random.Random(s["seed"] * 31 + arrival["id"] * 977 + topic_idx * 13 + ask_count)
    answer = answer_question(arrival, topic, rng, cfg["ai_tell"], cfg["human_tell"])
    s["log"].append((TOPIC_LABELS[topic], answer))
    s["asked_topics"].append(topic)
    s["questions_left"] -= 1


def _deep_check(s: dict[str, Any], arrival: dict[str, Any]) -> None:
    """精密照会を実行する。1人1回、残り時間を対価に取る。

    照会の結果、残り時間が0になった場合はそのまま既存の _timeout() に
    つなぎ、通常の時間切れと同じ扱い（不正解・見逃し）にする。
    """
    if s.get("deep_check_used") or s["phase"] != "asking":
        return
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    cost = cfg.get("check_cost", cfg["time"] * 0.25)
    accuracy = cfg.get("check_accuracy", 0.70)
    # _ask (係数31/977/13) や generate_arrival 用の乱数(係数1_000_003/97)と
    # 衝突しない専用の係数を使う。
    rng = random.Random(s["seed"] * 524_287 + arrival["id"] * 131 + 7)
    s["deep_check_result"] = deep_check(arrival, rng, cfg["ai_tell"], cfg["human_tell"], accuracy)
    s["deep_check_used"] = True
    spend_time(s, cost)
    if s["time_left"] <= 0 and s["phase"] == "asking":
        _timeout(s)


def _submit_guess(s: dict[str, Any], arrival: dict[str, Any], guess_is_ai: bool) -> None:
    correct = judge(arrival, guess_is_ai)
    s["arrivals_judged"] += 1
    if correct:
        s["score"] += 1
    s["last_result"] = {"correct": correct, "was_ai": arrival["is_ai"], "timeout": False}
    s["phase"] = "result"


def _timeout(s: dict[str, Any]) -> None:
    """持ち時間切れ。判定できなかったので見逃し扱い（＝不正解）にする。"""
    if s["phase"] != "asking":
        return
    arrival = s["current_arrival"]
    s["arrivals_judged"] += 1
    s["timeouts"] += 1
    s["time_left"] = 0.0
    s["last_result"] = {"correct": False, "was_ai": arrival["is_ai"], "timeout": True}
    s["phase"] = "result"


def _advance(s: dict[str, Any]) -> None:
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    s["round_no"] += 1
    s["current_arrival"] = None
    s["last_result"] = None
    if s["round_no"] >= cfg["arrivals"]:
        s["game_over"] = True


# ---------------------------------------------------------------------------
# 画面描画
# ---------------------------------------------------------------------------

def _render_status(s: dict[str, Any], left: float) -> None:
    """画面上部の状況表示。残り時間もここに含める。

    「残り時間」の数字はこの関数ごと fragment の中で描き直される。ここを
    fragment の外に置くとページ全体が再実行されるまで数字が固まったままになり、
    いちばん目立つ場所が止まって見えてしまう。
    """
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    ui.metric_row([
        ("審査中", f"{s['round_no'] + 1} / {cfg['arrivals']} 人目"),
        ("正解数", f"{s['score']} / {s['arrivals_judged']}"),
        ("残り質問回数", s["questions_left"]),
        ("残り時間", f"{left:.0f} 秒"),
    ])

    st.progress(max(0.0, min(1.0, left / cfg["time"])))
    if left <= 0:
        st.error("⏱️ 時間切れ")
    elif left <= TIME_WARN_SEC:
        st.warning(f"⏱️ 残り **{left:.0f}** 秒 — 急いで判定を")


@st.fragment(run_every="1s")
def _render_status_live(s: dict[str, Any]) -> None:
    """審査中の上部表示を1秒ごとに更新する。

    fragment なのでページ全体（書類・会話ログ・ボタン）は再実行されず、
    ここだけが刻む。時間切れになった瞬間だけアプリ全体を再実行して結果画面に移る。
    """
    left = tick_timer(s, time.time())
    _render_status(s, left)
    if left <= 0 and s["phase"] == "asking":
        _timeout(s)
        st.rerun(scope="app")


def _render_documents(arrival: dict[str, Any]) -> None:
    st.subheader("📄 提出書類")
    st.write(f"**氏名**：{arrival['name']}")
    st.write(f"**国籍**：{arrival['nationality']}")
    # 年齢を併記する。会話で語られる年代と突き合わせるために必要な情報なので、
    # 審査官に暗算を強いる意味はない。
    st.write(f"**生年月日**：{arrival['dob']}（{CURRENT_YEAR}年時点で {arrival.get('age', '?')}歳）")
    st.write(f"**パスポート番号**：{arrival['passport_no']}")
    st.write(f"**ビザ種別**：{arrival['visa_type']}（滞在目的：{VISA_PURPOSE.get(arrival['visa_type'], '—')}）")
    st.divider()
    st.write(f"**指紋照合**：{arrival['fingerprint_note']}")
    st.write(f"**顔写真照合**：{arrival['photo_note']}")
    st.caption("会話の内容がこの書類と食い違っていないか、必ず見比べること。")


def _render_conversation_log(s: dict[str, Any]) -> None:
    st.subheader("💬 会話ログ")
    if not s["log"]:
        st.caption("まだ質問していません。右側のボタンから質問してください。")
        return
    for topic_label, answer in s["log"]:
        st.markdown(f"**Q. {topic_label}**")
        st.write(answer)
        st.write("")


def _render_actions(s: dict[str, Any], arrival: dict[str, Any]) -> None:
    st.subheader("🔎 尋問 / 判定")

    check_result = s.get("deep_check_result")
    if check_result:
        conf_icon = {"高": "🔴", "中": "🟡", "低": "⚪"}.get(check_result["confidence"], "⚪")
        st.info(f"{conf_icon} {check_result['text']}")
        st.caption("精密照会の結果もあくまで手がかりの一つ。確実な証拠は書類×会話の矛盾のみ。")

    if s["phase"] == "asking":
        st.caption(f"残り質問回数：{s['questions_left']} 回　/　持ち時間内に判定すること")
        for topic in TOPICS:
            label = TOPIC_LABELS[topic]
            already_asked = topic in s["asked_topics"]
            disabled = s["questions_left"] <= 0 or already_asked
            btn_label = f"質問：{label}" + ("（済）" if already_asked else "")
            if st.button(btn_label, key=f"imm_ask_{topic}", disabled=disabled, use_container_width=True):
                _ask(s, arrival, topic)
                st.rerun()

        st.divider()
        cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
        check_cost = cfg.get("check_cost", cfg["time"] * 0.25)
        check_used = s.get("deep_check_used", False)
        check_disabled = check_used or s["time_left"] < check_cost
        check_label = f"🗂 精密照会（−{check_cost:.0f}秒 / 1人1回）" + ("（済）" if check_used else "")
        if st.button(check_label, key="imm_deep_check", disabled=check_disabled, use_container_width=True):
            _deep_check(s, arrival)
            st.rerun()
        st.caption("残り時間を対価に、確度つきの追加の手がかりを1回だけ得られます。")

        st.divider()
        st.write("この入国者は AI だと思いますか？")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🤖 AIと判定", key="imm_guess_ai", use_container_width=True):
                _submit_guess(s, arrival, True)
                st.rerun()
        with c2:
            if st.button("🧑 人間と判定", key="imm_guess_human", use_container_width=True):
                _submit_guess(s, arrival, False)
                st.rerun()
    else:
        r = s["last_result"]
        truth_label = "AI" if r["was_ai"] else "人間"
        if r["timeout"]:
            st.error(
                f"⏱️ 時間切れ。判定できないまま入国させてしまった。"
                f"\n\nこの入国者の正体は **{truth_label}** でした。"
            )
        elif r["correct"]:
            st.success(f"正解！ この入国者の正体は **{truth_label}** でした。")
        else:
            st.error(f"不正解…。この入国者の正体は **{truth_label}** でした。")

        # 仕込まれていた矛盾を種明かしする。次の審査で「どこを見るか」の学習材料。
        cross = arrival.get("contradiction")
        if cross:
            info = CROSS_CONTRADICTIONS[cross]
            asked = info["topic"] in s["asked_topics"]
            if asked:
                st.info(f"🔍 この入国者には矛盾がありました：{info['hint']}（書類の「{info['doc']}」）")
            else:
                st.info(
                    f"🔍 実は「{TOPIC_LABELS[info['topic']]}」を聞いていれば、"
                    f"{info['hint']}ことに気づけました（書類の「{info['doc']}」）。"
                )

        if st.button("次の入国者へ ▶", key="imm_next", use_container_width=True):
            _advance(s)
            st.rerun()


def _render_game_over(s: dict[str, Any]) -> None:
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    ui.metric_row([
        ("難易度", f"{cfg['emoji']} {cfg['label']}"),
        ("最終正解数", f"{s['score']} / {cfg['arrivals']}"),
        ("合格ライン", f"{cfg['win']} 人以上"),
        ("時間切れ", f"{s.get('timeouts', 0)} 人"),
    ])
    if s.get("timeouts"):
        st.caption(
            f"⏱️ {s['timeouts']} 人は時間内に判定できず見逃しました。"
            "書類の矛盾は一目で分かるものから当たると速く捌けます。"
        )

    # 自己ベスト（正解数）。二重記録を防ぐため、この結果画面で一度だけ記録する。
    best_label = f"{s['score']} / {cfg['arrivals']} 人"
    if not s.get("best_recorded"):
        ui.record_and_show_best(NAME, s.get("difficulty", DEFAULT_DIFFICULTY), s["score"], best_label)
        s["best_recorded"] = True
    else:
        ui.personal_best_line(NAME, s.get("difficulty", DEFAULT_DIFFICULTY))

    win = s["score"] >= cfg["win"]
    ui.result_banner(
        win,
        f"{s['score']} / {cfg['arrivals']} 人を正しく見抜き、{cfg['label']}として合格です！",
        f"{s['score']} / {cfg['arrivals']} 人しか見抜けませんでした。もう一度鍛錬を積みましょう。",
    )
    st.caption("🔄 リセットボタンでもう一度挑戦できます。")


def render() -> None:
    ui.game_header("🛂 AI入国審査官", NAME, how_to_play=HOW_TO_PLAY)

    s = state.game_state(NAME, _default_state)

    # 難易度は遊び方画面で選ばれている。開始後に変えられると審査人数や
    # 持ち時間が途中で変わってしまうので、1人目を出す前だけ取り込む。
    if s["round_no"] == 0 and s["current_arrival"] is None:
        s["difficulty"] = state.difficulty(NAME)

    if s["game_over"]:
        _render_game_over(s)
        return

    if s["current_arrival"] is None:
        _start_next_arrival(s)

    arrival = s["current_arrival"]

    # 仕様どおり残り時間は画面の一番上。審査中だけ1秒ごとに刻み、
    # 判定後は止まった値をそのまま静かに表示する。
    if s["phase"] == "asking":
        _render_status_live(s)
    else:
        _render_status(s, float(s["time_left"]))

    st.divider()

    doc_col, log_col, action_col = st.columns([1.1, 1.6, 1.3])
    with doc_col:
        _render_documents(arrival)
    with log_col:
        _render_conversation_log(s)
    with action_col:
        _render_actions(s, arrival)
