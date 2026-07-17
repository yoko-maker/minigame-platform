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

TOTAL_ARRIVALS = 5          # 審査する入国者の総数
QUESTIONS_PER_ARRIVAL = 3   # 1人あたりの質問回数上限
WIN_THRESHOLD = 3           # この人数以上正解すれば勝利

# 1人あたりの持ち時間。時間内に判定できなければ「見逃し」＝不正解になる。
# 後ろに列ができている審査ブースなので、迷い続けること自体が失点になる。
TIME_LIMIT_SEC = 60.0
TIME_WARN_SEC = 15.0        # ここを切ったら警告表示に変える

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
- 左側の「提出書類」（パスポート番号・指紋照合・顔写真照合など）に矛盾がないか確認します。
- 中央下のボタンで質問を行うと（1人につき最大 **{q}回** ）、応答が会話ログに追記されます。応答の自然さ・違和感から推理してください。
- 質問のあと、「🤖 AIと判定」または「🧑 人間と判定」ボタンで判定します。判定すると正解が発表され、次の入国者に進めます。
- **1人あたりの持ち時間は {t:.0f} 秒です。** 画面上部の残り時間がゼロになると
  判定できないまま見逃したことになり、その入国者は不正解として数えられます。
  後ろには列ができています。迷い続けること自体が失点です。
- 全 **{total}人** を審査し、**{win}人以上正解** すれば審査官として合格（勝利）です。
- 書類の矛盾やぎこちない受け答えは手がかりですが、確実な証拠ではありません。慎重に、しかし手早く見極めましょう。
""".format(total=TOTAL_ARRIVALS, win=WIN_THRESHOLD, q=QUESTIONS_PER_ARRIVAL, t=TIME_LIMIT_SEC)


# ---------------------------------------------------------------------------
# 純粋ロジック関数（st に依存しない。単体テスト可能）
# ---------------------------------------------------------------------------

def generate_arrival(rng: random.Random, arrival_no: int) -> dict[str, Any]:
    """乱数 rng から1人分の入国者データを生成する。

    AI の場合、指紋/顔写真照合に不整合が出やすい（ただし確定ではない）。
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
    anomaly_prob = 0.6 if is_ai else 0.15
    fingerprint_ok = rng.random() >= anomaly_prob
    photo_ok = rng.random() >= anomaly_prob

    fingerprint_note = "一致" if fingerprint_ok else rng.choice(FINGERPRINT_ANOMALIES)
    photo_note = "一致" if photo_ok else rng.choice(PHOTO_ANOMALIES)

    return {
        "id": arrival_no,
        "is_ai": is_ai,
        "name": name,
        "nationality": nationality,
        "dob": dob,
        "passport_no": passport_no,
        "visa_type": visa_type,
        "fingerprint_note": fingerprint_note,
        "photo_note": photo_note,
        "hobby_seed": hobby,
    }


def answer_question(arrival: dict[str, Any], topic: str, rng: random.Random) -> str:
    """入国者への質問(topic)に対する応答文を生成する。

    is_ai な入国者は「不自然/機械的」なテンプレートが出やすいが、
    人間でも稀に不自然な応答が混ざる（心理戦としての揺さぶり）。
    """
    if topic not in RESPONSE_TEMPLATES:
        raise ValueError(f"unknown topic: {topic}")

    is_ai = arrival["is_ai"]
    natural_prob = 0.35 if is_ai else 0.85
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


# ---------------------------------------------------------------------------
# state 初期化・遷移ヘルパー（st.session_state を扱う非純粋関数）
# ---------------------------------------------------------------------------

def _default_state() -> dict[str, Any]:
    return {
        "seed": random.randint(0, 10_000_000),
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
    }


def _start_next_arrival(s: dict[str, Any]) -> None:
    """次の入国者を生成して state にセットする。持ち時間もここで巻き戻す。"""
    round_no = s["round_no"]
    rng = random.Random(s["seed"] * 1_000_003 + round_no * 97 + 1)
    s["current_arrival"] = generate_arrival(rng, round_no)
    s["questions_left"] = QUESTIONS_PER_ARRIVAL
    s["asked_topics"] = []
    s["log"] = []
    s["phase"] = "asking"
    s["time_left"] = TIME_LIMIT_SEC
    s["last_tick"] = None


def _ask(s: dict[str, Any], arrival: dict[str, Any], topic: str) -> None:
    topic_idx = TOPICS.index(topic)
    ask_count = len(s["asked_topics"])
    rng = random.Random(s["seed"] * 31 + arrival["id"] * 977 + topic_idx * 13 + ask_count)
    answer = answer_question(arrival, topic, rng)
    s["log"].append((TOPIC_LABELS[topic], answer))
    s["asked_topics"].append(topic)
    s["questions_left"] -= 1


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
    s["round_no"] += 1
    s["current_arrival"] = None
    s["last_result"] = None
    if s["round_no"] >= TOTAL_ARRIVALS:
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
    ui.metric_row([
        ("審査中", f"{s['round_no'] + 1} / {TOTAL_ARRIVALS} 人目"),
        ("正解数", f"{s['score']} / {s['arrivals_judged']}"),
        ("残り質問回数", s["questions_left"]),
        ("残り時間", f"{left:.0f} 秒"),
    ])

    st.progress(max(0.0, min(1.0, left / TIME_LIMIT_SEC)))
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
    st.write(f"**生年月日**：{arrival['dob']}")
    st.write(f"**パスポート番号**：{arrival['passport_no']}")
    st.write(f"**ビザ種別**：{arrival['visa_type']}")
    st.divider()
    st.write(f"**指紋照合**：{arrival['fingerprint_note']}")
    st.write(f"**顔写真照合**：{arrival['photo_note']}")


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

        if st.button("次の入国者へ ▶", key="imm_next", use_container_width=True):
            _advance(s)
            st.rerun()


def _render_game_over(s: dict[str, Any]) -> None:
    ui.metric_row([
        ("最終正解数", f"{s['score']} / {TOTAL_ARRIVALS}"),
        ("合格ライン", f"{WIN_THRESHOLD} 人以上"),
        ("時間切れ", f"{s.get('timeouts', 0)} 人"),
    ])
    if s.get("timeouts"):
        st.caption(
            f"⏱️ {s['timeouts']} 人は時間内に判定できず見逃しました。"
            "書類の矛盾は一目で分かるものから当たると速く捌けます。"
        )
    win = s["score"] >= WIN_THRESHOLD
    ui.result_banner(
        win,
        f"{s['score']} / {TOTAL_ARRIVALS} 人を正しく見抜き、審査官として合格です！",
        f"{s['score']} / {TOTAL_ARRIVALS} 人しか見抜けませんでした。もう一度鍛錬を積みましょう。",
    )
    st.caption("🔄 リセットボタンでもう一度挑戦できます。")


def render() -> None:
    ui.game_header("🛂 AI入国審査官", NAME, how_to_play=HOW_TO_PLAY)

    s = state.game_state(NAME, _default_state)

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
