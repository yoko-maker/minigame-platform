"""癒し猫カフェ。

10営業日で高評価を目指す経営シミュレーションゲーム。

契約:
- `render()` を公開する（引数なし）。app.py から呼ばれる。
- 画面冒頭で `utils.ui.game_header("🐈 癒し猫カフェ", NAME, how_to_play=...)` を呼ぶ。
- 状態は `utils.state.game_state(NAME, ...)` が返す dict に保存する。
- 全ウィジェットの key は "cc_" で始める。

設計:
- 外部APIは使わず、ルールベース + 乱数（random.Random）でシミュレートする。
- ゲーム開始時に rng_seed を1つ決めて state に保存し、日ごとに
  `random.Random(rng_seed + day)` を作ることで、同じシードなら同じ日は
  常に同じ結果になる（再現可能）。
- ロジックは `roll_event` / `simulate_day` / `final_evaluation` という
  Streamlit に依存しない純粋関数にまとめ、UI(`render` 以下)から分離する。
"""

from __future__ import annotations

import random
from typing import Any

import streamlit as st

from utils import state, ui

NAME = "cat_cafe"

# ---------------------------------------------------------------------------
# ゲームバランス定数
# ---------------------------------------------------------------------------

TOTAL_DAYS = 10
INITIAL_FUNDS = 5000

PRICE_MIN = 500
PRICE_MAX = 1800
PRICE_STEP = 50
PRICE_REF = 900  # この価格を基準に客の反応が変わる

BASE_DAILY_COST = 900  # 家賃・光熱費など固定費
CAT_CARE_COST = 300  # 猫の餌代などの固定費

BANKRUPTCY_LIMIT = -4000  # これを下回ると経営破綻でゲームオーバー

# 猫のコンディションの目盛り。UI で「いくつまであるのか」を必ず示すために使う。
CAT_STAT_MAX = 100
CAT_MOOD_GOOD = 70      # これ以上なら機嫌が良いと言える
CAT_FATIGUE_WARN = 75   # これを超えると機嫌が下がり始める（simulate_day と対応）

AD_COSTS = [0, 700, 1600, 3000]
AD_LABELS = ["投資しない", "軽め", "普通", "積極的"]

FACILITY_MAX = 3
FACILITY_UPGRADE_COSTS = [1500, 2800, 4200]  # index = 現在のレベル
FACILITY_LABELS = ["簡素", "標準", "快適", "豪華"]

# 客層: 価格感度が高いほど値上げで来店確率が下がりやすい。
# 広告感度が高いほど広告投資の効果を受けやすい。
SEGMENTS: list[dict[str, Any]] = [
    {"key": "student", "label": "🎒 学生", "base_visitors": 16, "price_sensitivity": 1.5, "ad_sensitivity": 0.9},
    {"key": "family", "label": "👪 家族", "base_visitors": 10, "price_sensitivity": 0.9, "ad_sensitivity": 1.0},
    {"key": "office", "label": "💼 会社員", "base_visitors": 14, "price_sensitivity": 0.7, "ad_sensitivity": 0.6},
    {"key": "tourist", "label": "🧳 観光客", "base_visitors": 7, "price_sensitivity": 0.5, "ad_sensitivity": 1.3},
]

# ランダムイベント（確率の合計は 1.0）
EVENTS: list[dict[str, Any]] = [
    {
        "id": "none", "label": "特に何も起こらなかった", "prob": 0.40,
        "visitor_mult": 1.0, "mood_delta": 0, "popularity_delta": 0,
        "extra_cost": 0, "satisfaction_delta": 0,
        "desc": "いつも通り、穏やかな1日でした。",
    },
    {
        "id": "rain", "label": "☔ 雨で客足が鈍った", "prob": 0.20,
        "visitor_mult": 0.7, "mood_delta": -2, "popularity_delta": 0,
        "extra_cost": 0, "satisfaction_delta": -2,
        "desc": "雨のため来客数が伸び悩みました。",
    },
    {
        "id": "tv", "label": "📺 テレビで紹介された", "prob": 0.12,
        "visitor_mult": 1.6, "mood_delta": 5, "popularity_delta": 14,
        "extra_cost": 0, "satisfaction_delta": 5,
        "desc": "人気番組で紹介され、行列ができました！",
    },
    {
        "id": "sns", "label": "📱 SNSで話題になった", "prob": 0.13,
        "visitor_mult": 1.35, "mood_delta": 3, "popularity_delta": 9,
        "extra_cost": 0, "satisfaction_delta": 3,
        "desc": "看板猫の写真がSNSでバズりました。",
    },
    {
        "id": "escape", "label": "🐾 猫が脱走した！", "prob": 0.15,
        "visitor_mult": 0.8, "mood_delta": -12, "popularity_delta": -3,
        "extra_cost": 1200, "satisfaction_delta": -8,
        "desc": "営業中に猫が脱走。捜索でバタバタし、費用もかかりました。",
    },
]

WIN_SCORE_THRESHOLD = 60.0

HOW_TO_PLAY = f"""
**目標**: {TOTAL_DAYS}営業日を終えたときの「総合評価スコア」が {WIN_SCORE_THRESHOLD:.0f} 点以上なら勝利です。

1. 毎朝、**価格・広告投資・設備投資・猫を休ませるか** を決めます。
2. 「営業開始」を押すと、その日の来客数・売上・コスト・利益と、ランダムイベントの結果が表示されます。
3. 結果画面には **🗣️ 入場者の声** が出ます。値段・広告・設備・猫の様子への反応なので、
   不満が出た点を翌日の方針で直していくと評価が伸びます。
4. 「次の日へ」で翌日に進みます。これを{TOTAL_DAYS}日間繰り返します。

**猫のコンディションはすべて 0〜{CAT_STAT_MAX} の目盛りです**
- 😺 機嫌: 高いほど良い（{CAT_MOOD_GOOD}以上を保ちたい）
- 😪 疲労: 低いほど良い（{CAT_FATIGUE_WARN}を超えると機嫌が下がり始める）
- ⭐ 人気: 高いほど客が増える

**客層によって反応が違います**
- 🎒 学生: 価格にとても敏感
- 👪 家族: 価格・広告どちらもバランス重視
- 💼 会社員: 価格にはあまり敏感でないが、数はあまり増えない
- 🧳 観光客: 広告（口コミ）に敏感で客単価が高い

**猫のコンディション**
- 疲労が溜まると機嫌・人気が下がりやすくなります。
- 「今日は休ませる」を選ぶと営業時間は短縮されますが、疲労が大きく回復します。
- 設備投資（レベルアップ）は疲労の蓄積を抑え、満足度を底上げします。

**ランダムイベント**: 雨・テレビ紹介・SNSで話題・猫の脱走などが日替わりで発生し、
来客数や猫の状態、コストに影響します。

**資金が {BANKRUPTCY_LIMIT:,} 円を下回ると経営破綻で即ゲームオーバーです。**
"""


# ---------------------------------------------------------------------------
# 純粋ロジック（Streamlit 非依存）
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def roll_event(rng: random.Random) -> dict[str, Any]:
    """ランダムイベントを1つ抽選して返す（EVENTS のコピー）。"""
    r = rng.random()
    cumulative = 0.0
    for event in EVENTS:
        cumulative += event["prob"]
        if r < cumulative:
            return dict(event)
    return dict(EVENTS[0])


def simulate_day(gamestate: dict[str, Any], settings: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """1日分の営業をシミュレートする純粋関数。

    Args:
        gamestate: {"funds": float, "reputation": float,
                     "cat": {"mood": int, "fatigue": int, "popularity": int},
                     "facility_level": int}
        settings: {"price": int, "ad_level": int(0-3),
                    "rest_cat": bool, "invest_equipment": bool}
        rng: 日ごとに固定される random.Random インスタンス。

    Returns:
        当日の結果と更新後ステータス（"gamestate" キー配下）を含む dict。
        入力の gamestate/cat は変更しない。
    """
    cat = gamestate["cat"]
    funds = gamestate["funds"]
    reputation = gamestate["reputation"]
    facility_level = gamestate["facility_level"]

    price = settings["price"]
    ad_level = settings["ad_level"]
    rest_cat = settings["rest_cat"]
    invest_equipment = settings["invest_equipment"] and facility_level < FACILITY_MAX

    event = roll_event(rng)

    # --- 設備投資 ---
    equipment_cost = FACILITY_UPGRADE_COSTS[facility_level] if invest_equipment else 0
    new_facility_level = facility_level + 1 if invest_equipment else facility_level

    # --- 来客数（客層ごと） ---
    popularity_mult = 0.6 + cat["popularity"] / 100 * 0.8  # 0.6 - 1.4
    mood_mult = 0.75 + cat["mood"] / 100 * 0.5  # 0.75 - 1.25
    operation_mult = 0.4 if rest_cat else 1.0

    segment_visitors: dict[str, int] = {}
    for seg in SEGMENTS:
        price_ratio_excess = (price - PRICE_REF) / PRICE_REF
        price_mult = _clamp(1 - seg["price_sensitivity"] * price_ratio_excess, 0.15, 1.8)
        ad_mult = 1 + ad_level * 0.15 * seg["ad_sensitivity"]

        mean_visitors = seg["base_visitors"] * price_mult * ad_mult * popularity_mult * mood_mult * event["visitor_mult"] * operation_mult
        raw = rng.gauss(mean_visitors, max(mean_visitors * 0.25, 0.5))
        segment_visitors[seg["key"]] = int(_clamp(round(raw), 0, 300))

    total_visitors = sum(segment_visitors.values())
    total_visitors = min(total_visitors, 400)

    # --- 売上・コスト ---
    tip_revenue = round(total_visitors * (cat["mood"] / 100) * 15)
    revenue = total_visitors * price + tip_revenue
    cost = BASE_DAILY_COST + AD_COSTS[ad_level] + CAT_CARE_COST + event["extra_cost"] + equipment_cost
    profit = revenue - cost
    new_funds = funds + profit

    # --- 猫のコンディション更新 ---
    if rest_cat:
        new_fatigue = _clamp(cat["fatigue"] - 30, 0, 100)
        new_mood = _clamp(cat["mood"] + 10 + event["mood_delta"], 0, 100)
        new_popularity = _clamp(cat["popularity"] - 2 + event["popularity_delta"], 0, 100)
    else:
        fatigue_gain = max(total_visitors * 0.6 - new_facility_level * 5, 2)
        new_fatigue = _clamp(cat["fatigue"] + fatigue_gain, 0, 100)

        mood_shift = 0.0
        if new_fatigue > CAT_FATIGUE_WARN:
            mood_shift -= 8
        elif new_fatigue < 30:
            mood_shift += 4
        mood_shift += event["mood_delta"] + new_facility_level * 1.0
        new_mood = _clamp(cat["mood"] + mood_shift, 0, 100)

        popularity_shift = -1.0 + event["popularity_delta"]
        if new_mood >= CAT_MOOD_GOOD and new_fatigue <= 60:
            popularity_shift += 2
        new_popularity = _clamp(cat["popularity"] + popularity_shift, 0, 100)

    new_cat = {
        "mood": int(round(new_mood)),
        "fatigue": int(round(new_fatigue)),
        "popularity": int(round(new_popularity)),
    }

    # --- 評価（満足度 -> 評価スコアへゆっくり収束） ---
    price_ratio_excess = (price - PRICE_REF) / PRICE_REF
    satisfaction = 50 + (new_cat["mood"] - 50) * 0.3 + new_facility_level * 4
    satisfaction -= max(0.0, new_cat["fatigue"] - 70) * 0.4
    satisfaction -= max(0.0, price_ratio_excess) * 15
    satisfaction += event["satisfaction_delta"]
    satisfaction = _clamp(satisfaction, 0, 100)

    reputation_delta = (satisfaction - reputation) * 0.22 + rng.uniform(-1.5, 1.5)
    new_reputation = _clamp(reputation + reputation_delta, 0, 100)

    bankrupt = new_funds <= BANKRUPTCY_LIMIT

    result: dict[str, Any] = {
        "event": event,
        "settings": dict(settings),
        "segment_visitors": segment_visitors,
        "total_visitors": total_visitors,
        "revenue": revenue,
        "tip_revenue": tip_revenue,
        "cost": cost,
        "cost_breakdown": {
            "base": BASE_DAILY_COST,
            "ad": AD_COSTS[ad_level],
            "cat_care": CAT_CARE_COST,
            "event": event["extra_cost"],
            "equipment": equipment_cost,
        },
        "profit": profit,
        "satisfaction": satisfaction,
        "reputation_delta": reputation_delta,
        "cat_before": dict(cat),
        "cat_after": new_cat,
        "equipment_invested": invest_equipment,
        "bankrupt": bankrupt,
        "gamestate": {
            "funds": new_funds,
            "reputation": new_reputation,
            "cat": new_cat,
            "facility_level": new_facility_level,
        },
    }
    # 客の声はその日の結果から導くので、結果が揃ってから最後に組み立てる。
    result["voices"] = customer_voices(result, rng)
    return result


def customer_voices(
    result: dict[str, Any], rng: random.Random, limit: int = 3
) -> list[dict[str, str]]:
    """その日の来客から拾った声を返す純粋関数。

    翌日の方針決定の手がかりになるよう、実際にその日の数値を悪く（または良く）
    した原因だけを取り上げる。強い不満から順に拾うので、声を潰していけば経営が
    良くなる。客の言葉として書き、システムの助言口調にはしない。

    Returns:
        [{"who": 客層ラベル, "text": 発言, "tone": "bad"|"good"|"info"}, ...]
    """
    settings = result["settings"]
    cat = result["cat_after"]
    event = result["event"]
    price = settings["price"]
    ad_level = settings["ad_level"]
    facility = result["gamestate"]["facility_level"]
    visitors = result["total_visitors"]

    # (優先度, 声) の候補。優先度が高いほど「今いちばん効いている原因」。
    candidates: list[tuple[float, dict[str, str]]] = []

    over = (price - PRICE_REF) / PRICE_REF  # 基準価格からの乖離
    if over > 0.35:
        candidates.append((10 + over, {
            "who": "🎒 学生",
            "text": f"{price:,}円はさすがに厳しいです……。友達を誘いづらくて。",
            "tone": "bad",
        }))
    elif over > 0.15:
        candidates.append((6 + over, {
            "who": "👪 家族",
            "text": f"{price:,}円だと、家族全員で来るのは少し考えちゃいますね。",
            "tone": "bad",
        }))
    elif over < -0.25:
        candidates.append((5, {
            "who": "💼 会社員",
            "text": f"{price:,}円は正直、安すぎませんか。もう少し取っていいと思いますよ。",
            "tone": "info",
        }))
    else:
        candidates.append((3, {
            "who": "💼 会社員",
            "text": f"{price:,}円でこの時間が過ごせるなら、また寄ります。",
            "tone": "good",
        }))

    if cat["fatigue"] > CAT_FATIGUE_WARN:
        candidates.append((12, {
            "who": "👪 家族",
            "text": "猫ちゃん、ぐったりしてました……。無理させてないといいんですけど。",
            "tone": "bad",
        }))
    elif cat["fatigue"] < 30 and cat["mood"] >= CAT_MOOD_GOOD:
        candidates.append((4, {
            "who": "🧳 観光客",
            "text": "猫がのびのびしてて、見てるだけで癒されました！",
            "tone": "good",
        }))

    if cat["mood"] < 40:
        candidates.append((11, {
            "who": "🎒 学生",
            "text": "猫が全然こっち来てくれなくて……。機嫌が悪かったのかな。",
            "tone": "bad",
        }))
    elif cat["mood"] >= 85:
        candidates.append((4, {
            "who": "👪 家族",
            "text": "膝の上で寝てくれました。子どもが大喜びでしたよ。",
            "tone": "good",
        }))

    if facility == 0:
        candidates.append((8, {
            "who": "💼 会社員",
            "text": "席が硬くて、長居はしづらいですね。設備がもう少し良ければ。",
            "tone": "bad",
        }))
    elif facility >= 2:
        candidates.append((3, {
            "who": "🧳 観光客",
            "text": "内装が素敵で、写真をたくさん撮ってしまいました。",
            "tone": "good",
        }))

    if ad_level == 0:
        candidates.append((7, {
            "who": "🧳 観光客",
            "text": "こんなお店があるの、知りませんでした。たまたま通りかかって。",
            "tone": "bad",
        }))
    elif ad_level >= 2:
        candidates.append((3, {
            "who": "🎒 学生",
            "text": "広告を見て来ました！ずっと気になってたんです。",
            "tone": "good",
        }))

    if settings["rest_cat"]:
        candidates.append((9, {
            "who": "👪 家族",
            "text": "今日は早じまいだったんですね。せっかく来たのに残念でした。",
            "tone": "info",
        }))

    if event["id"] == "escape":
        candidates.append((9, {
            "who": "🎒 学生",
            "text": "猫が逃げ出して大騒ぎでしたね。無事に見つかってよかったです。",
            "tone": "info",
        }))
    elif event["id"] == "rain":
        candidates.append((5, {
            "who": "💼 会社員",
            "text": "雨宿りのつもりで入りましたが、思ったより落ち着けました。",
            "tone": "info",
        }))
    elif event["id"] in ("tv", "sns"):
        candidates.append((6, {
            "who": "🧳 観光客",
            "text": "話題になってたので来ました！混んでたけど満足です。",
            "tone": "good",
        }))

    if visitors == 0:
        return [{
            "who": "🌙 店主",
            "text": "今日は誰も来なかった。値段か、広告か、猫の様子か——どこかに理由がある。",
            "tone": "bad",
        }]

    # 強い声から順に。優先度が並んだときの順序を乱数で散らして、毎日同じ並びにしない。
    candidates.sort(key=lambda c: (-c[0], rng.random()))
    return [voice for _prio, voice in candidates[:limit]]


def final_evaluation(gamestate: dict[str, Any]) -> dict[str, Any]:
    """最終日終了後の総合評価を計算する純粋関数。"""
    funds = gamestate["funds"]
    reputation = gamestate["reputation"]

    total_profit = funds - INITIAL_FUNDS
    profit_score = _clamp(50 + total_profit / 150, 0, 100)
    composite = reputation * 0.6 + profit_score * 0.4
    stars = round(composite / 20 * 2) / 2  # 0.5刻みで 0-5
    win = composite >= WIN_SCORE_THRESHOLD and funds > 0

    return {
        "score": composite,
        "stars": stars,
        "win": win,
        "total_profit": total_profit,
        "profit_score": profit_score,
        "reputation": reputation,
        "funds": funds,
    }


# ---------------------------------------------------------------------------
# state ヘルパー
# ---------------------------------------------------------------------------

def _default_state() -> dict[str, Any]:
    return {
        "rng_seed": random.randrange(1_000_000),
        "day": 1,
        "phase": "plan",  # "plan" -> "result" -> ("plan" | "gameover")
        "funds": float(INITIAL_FUNDS),
        "reputation": 50.0,
        "cat": {"mood": 70, "fatigue": 10, "popularity": 35},
        "facility_level": 0,
        "history": [],
        "pending_result": None,
        "last_price": PRICE_REF,
        "last_ad_level": 1,
        "bankrupt": False,
    }


def _day_rng(s: dict[str, Any]) -> random.Random:
    return random.Random(s["rng_seed"] + s["day"])


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render() -> None:
    ui.game_header("🐈 癒し猫カフェ", NAME, how_to_play=HOW_TO_PLAY)
    s = state.game_state(NAME, _default_state)

    if s["phase"] == "plan":
        _render_plan(s)
    elif s["phase"] == "result":
        _render_result(s)
    else:
        _render_gameover(s)


def _render_cat_condition(cat: dict[str, int]) -> None:
    """猫の状態を「いくつまであるのか」「どちらへ動かしたいのか」が分かる形で出す。

    数字だけだと 70 が高いのか低いのかが読めないので、上限とバーを必ず添える。
    """
    st.markdown("**🐈 猫のコンディション**")
    rows = [
        ("😺 機嫌", cat["mood"], f"高いほど客が喜び、チップも増える（{CAT_MOOD_GOOD}以上を保ちたい）"),
        ("😪 疲労", cat["fatigue"], f"低いほど良い。{CAT_FATIGUE_WARN}を超えると機嫌が下がり始める"),
        ("⭐ 人気", cat["popularity"], "高いほど客が増える。機嫌が良く疲れていない日が続くと伸びる"),
    ]
    for label, value, note in rows:
        st.progress(
            _clamp(value, 0, 100) / 100,
            text=f"{label}　{value} / {CAT_STAT_MAX}　— {note}",
        )


def _render_plan(s: dict[str, Any]) -> None:
    st.subheader(f"📅 {s['day']}日目 / {TOTAL_DAYS}日 - 方針決定")

    cat = s["cat"]
    ui.metric_row([
        ("資金", f"¥{int(s['funds']):,}"),
        ("評価スコア", f"{s['reputation']:.0f} / 100"),
        ("合格ライン", f"{WIN_SCORE_THRESHOLD:.0f}"),
    ])
    _render_cat_condition(cat)

    if cat["fatigue"] > CAT_FATIGUE_WARN:
        st.warning(
            f"😿 疲労が {cat['fatigue']} / {CAT_STAT_MAX} です。"
            f"{CAT_FATIGUE_WARN} を超えると機嫌が下がり始めます。休ませることを検討しましょう。"
        )
    if s["funds"] < 0:
        st.warning(f"⚠️ 資金がマイナスです。{BANKRUPTCY_LIMIT:,} 円を下回ると経営破綻します。")

    st.markdown(f"**設備レベル**: {FACILITY_LABELS[s['facility_level']]}（{s['facility_level']} / {FACILITY_MAX}）")

    col1, col2 = st.columns(2)
    with col1:
        price = st.slider(
            "価格（1人あたり・円）", min_value=PRICE_MIN, max_value=PRICE_MAX,
            value=int(s.get("last_price", PRICE_REF)), step=PRICE_STEP, key="cc_price",
        )
        ad_level = st.select_slider(
            "広告投資", options=[0, 1, 2, 3],
            value=s.get("last_ad_level", 1),
            format_func=lambda x: f"{AD_LABELS[x]}（¥{AD_COSTS[x]:,}）",
            key="cc_ad_level",
        )
    with col2:
        rest_cat = st.checkbox(
            "😴 今日は猫を休ませる（営業時間短縮・疲労が大きく回復）",
            key="cc_rest_cat",
        )
        if s["facility_level"] < FACILITY_MAX:
            cost = FACILITY_UPGRADE_COSTS[s["facility_level"]]
            invest_equipment = st.checkbox(
                f"🛠️ 設備投資する（¥{cost:,} → {FACILITY_LABELS[s['facility_level'] + 1]}）",
                key="cc_invest_equipment",
            )
        else:
            st.caption("🛠️ 設備は最大レベルです。")
            invest_equipment = False

    if st.button("☕ 営業開始", key="cc_start_day", type="primary", use_container_width=True):
        settings = {
            "price": price,
            "ad_level": ad_level,
            "rest_cat": rest_cat,
            "invest_equipment": invest_equipment,
        }
        gamestate = {
            "funds": s["funds"],
            "reputation": s["reputation"],
            "cat": dict(s["cat"]),
            "facility_level": s["facility_level"],
        }
        rng = _day_rng(s)
        result = simulate_day(gamestate, settings, rng)

        s["funds"] = result["gamestate"]["funds"]
        s["reputation"] = result["gamestate"]["reputation"]
        s["cat"] = result["gamestate"]["cat"]
        s["facility_level"] = result["gamestate"]["facility_level"]
        s["pending_result"] = result
        s["bankrupt"] = result["bankrupt"]
        s["last_price"] = price
        s["last_ad_level"] = ad_level
        s["history"].append({
            "日": s["day"],
            "イベント": result["event"]["label"],
            "来客数": result["total_visitors"],
            "売上": result["revenue"],
            "コスト": result["cost"],
            "利益": result["profit"],
            "資金": int(result["gamestate"]["funds"]),
            "評価": round(result["gamestate"]["reputation"], 1),
        })
        s["phase"] = "result"
        st.rerun()


VOICE_TONE_STYLE = {
    "bad": ("#F2A0A0", "😕"),
    "good": ("#9CBF9A", "😊"),
    "info": ("#E0C58F", "💬"),
}


def _render_voices(voices: list[dict[str, str]]) -> None:
    """入場者の声。翌日の方針を決めるための一次情報として出す。"""
    if not voices:
        return
    st.markdown("**🗣️ 今日の入場者の声**")
    st.caption("その日の値段・広告・設備・猫の様子への反応です。明日の方針の手がかりに。")
    for v in voices:
        color, mark = VOICE_TONE_STYLE.get(v["tone"], VOICE_TONE_STYLE["info"])
        st.markdown(
            f"""
            <div style="border-left:3px solid {color};padding:.35rem 0 .35rem .7rem;
                        margin:.35rem 0;">
              <div style="font-size:.78rem;opacity:.75;">{mark} {v["who"]}</div>
              <div>{v["text"]}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_result(s: dict[str, Any]) -> None:
    result = s["pending_result"]
    st.subheader(f"📊 {s['day']}日目 - 営業結果")

    event = result["event"]
    if event["id"] == "none":
        st.info(f"🗓️ {event['desc']}")
    elif event["id"] in ("tv", "sns"):
        st.success(f"{event['label']}\n\n{event['desc']}")
    else:
        st.warning(f"{event['label']}\n\n{event['desc']}")

    ui.metric_row([
        ("来客数", f"{result['total_visitors']}人"),
        ("売上", f"¥{result['revenue']:,}"),
        ("コスト", f"¥{result['cost']:,}"),
        ("利益", f"¥{result['profit']:,}"),
    ])

    with st.expander("客層別の来客数"):
        for seg in SEGMENTS:
            st.write(f"{seg['label']}: {result['segment_visitors'][seg['key']]}人")

    _render_voices(result.get("voices", []))

    cat_before, cat_after = result["cat_before"], result["cat_after"]
    st.markdown("**猫のコンディション変化**")
    _cat_delta_row(cat_before, cat_after)
    _render_cat_condition(cat_after)

    st.markdown(
        f"**評価スコア**: {result['gamestate']['reputation']:.1f} / 100 "
        f"（{'+' if result['reputation_delta'] >= 0 else ''}{result['reputation_delta']:.1f}）"
    )
    st.markdown(f"**資金**: ¥{int(result['gamestate']['funds']):,}")

    if result["equipment_invested"]:
        st.caption(f"🛠️ 設備をレベル {s['facility_level']} にアップグレードしました。")

    if result["bankrupt"]:
        st.error("💥 資金がマイナス4,000円を下回りました。経営破綻です。")
        label = "結果を見る"
    elif s["day"] >= TOTAL_DAYS:
        label = "結果を見る"
    else:
        label = "次の日へ"

    if st.button(f"➡️ {label}", key="cc_next_day", type="primary", use_container_width=True):
        if result["bankrupt"] or s["day"] >= TOTAL_DAYS:
            s["phase"] = "gameover"
        else:
            s["day"] += 1
            s["phase"] = "plan"
        st.rerun()


def _cat_delta_row(before: dict[str, int], after: dict[str, int]) -> None:
    cols = st.columns(3)
    labels = [("mood", "機嫌"), ("fatigue", "疲労"), ("popularity", "人気")]
    for col, (key, label) in zip(cols, labels):
        delta = after[key] - before[key]
        col.metric(label, after[key], delta=delta)


def _render_gameover(s: dict[str, Any]) -> None:
    st.subheader("🏁 最終結果")

    evaluation = final_evaluation({"funds": s["funds"], "reputation": s["reputation"]})

    if s.get("bankrupt"):
        ui.result_banner(
            False,
            win_msg="",
            lose_msg=f"経営破綻してしまいました…（{s['day']}日目）総合評価スコア {evaluation['score']:.0f} 点",
        )
    else:
        ui.result_banner(
            evaluation["win"],
            win_msg=f"{TOTAL_DAYS}日間の営業お疲れ様でした！総合評価スコア {evaluation['score']:.0f} 点で繁盛店の仲間入りです！",
            lose_msg=f"{TOTAL_DAYS}日間の営業お疲れ様でした。総合評価スコア {evaluation['score']:.0f} 点… もう一歩でした。",
        )

    stars_full = int(evaluation["stars"])
    stars_half = evaluation["stars"] - stars_full >= 0.5
    star_str = "⭐" * stars_full + ("✨" if stars_half else "")
    st.markdown(f"### 総合評価: {evaluation['score']:.1f} / 100 {star_str}")

    ui.metric_row([
        ("最終資金", f"¥{int(evaluation['funds']):,}"),
        ("累計損益", f"¥{int(evaluation['total_profit']):,}"),
        ("最終評価スコア", f"{evaluation['reputation']:.0f} / 100"),
    ])

    if s["history"]:
        st.markdown("**日別の記録**")
        st.table(s["history"])

    st.caption(f"🎲 シード: {s['rng_seed']}（同じシードなら同じ展開を再現できます）")

    if st.button("🔄 もう一度プレイ", key="cc_play_again", type="primary", use_container_width=True):
        state.reset_game(NAME)
        st.rerun()
