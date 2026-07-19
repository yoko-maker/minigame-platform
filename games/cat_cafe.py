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

BANKRUPTCY_LIMIT = -4000  # 難易度に bankruptcy_limit が無い場合のフォールバック

# 連続赤字（その日の利益がマイナス）が続くと信用を失う。
# 資金の破産ラインとは別の、もう一つの失敗条件。
DEFICIT_WARN_STREAK = 2        # これ以上続くと評価が余分に下がり始める
DEFICIT_REP_PENALTY = 6.0      # 警告ラインを超えた1日ごとに評価から引く点
DEFICIT_BANKRUPTCY_STREAK = 3  # これだけ連続で赤字だと信用を失い経営破綻

# 猫のコンディションの目盛り。UI で「いくつまであるのか」を必ず示すために使う。
CAT_STAT_MAX = 100
CAT_MOOD_GOOD = 70      # これ以上なら機嫌が良いと言える
CAT_FATIGUE_WARN = 75   # これを超えると機嫌が下がり始める（simulate_day と対応）

# 広告費は入場料と同じ50円単位で選ぶ。効果は下のアンカー点を折れ線で補間し、
# 旧4段階（¥0/700/1600/3000 → 集客係数 0/0.15/0.30/0.45）と同じバランスを保つ。
AD_MIN = 0
AD_MAX = 3000
AD_STEP = 50
# (広告費, 集客係数) のアンカー。この間は線形に補間する。
AD_ANCHORS = [(0, 0.0), (700, 0.15), (1600, 0.30), (3000, 0.45)]


def ad_effect(budget: float) -> float:
    """広告費から集客係数を返す（アンカー点を折れ線補間）。"""
    b = max(AD_MIN, min(budget, AD_MAX))
    for (x0, y0), (x1, y1) in zip(AD_ANCHORS, AD_ANCHORS[1:]):
        if b <= x1:
            return y0 + (y1 - y0) * (b - x0) / (x1 - x0)
    return AD_ANCHORS[-1][1]


def ad_word(budget: float) -> str:
    """広告費の目安を日本語一言で（スライダーの補助表示用）。"""
    if budget <= 0:
        return "投資しない"
    if budget < 700:
        return "ひかえめ"
    if budget < 1600:
        return "軽め"
    if budget < 3000:
        return "普通"
    return "積極的"

FACILITY_MAX = 3
FACILITY_UPGRADE_COSTS = [1500, 2800, 4200]  # index = 現在のレベル → 次へ上げる費用
FACILITY_LABELS = ["簡素", "標準", "快適", "豪華"]


def facility_upgrade_cost(current: int, target: int) -> int:
    """設備を current から target まで一気に上げる累計費用。"""
    target = max(current, min(target, FACILITY_MAX))
    return sum(FACILITY_UPGRADE_COSTS[current:target])

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

# 猫の気質。同じ「猫」でも、客の呼び方と消耗の仕方が違う。
# 誰を働かせて誰を休ませるかが、この違いによって意味を持つ。
CAT_BREEDS: dict[str, dict[str, Any]] = {
    "friendly": {
        "label": "人懐こい",
        "emoji": "😻",
        "appeal": 1.25,        # 客を呼ぶ力
        "fatigue_rate": 1.30,  # 疲れやすさ
        "crowd": 0.0,          # 混雑で機嫌を損ねる度合い
        "desc": "誰にでも寄っていく。よく懐くぶん、すぐ疲れる。",
    },
    "shy": {
        "label": "人見知り",
        "emoji": "🙀",
        "appeal": 0.80,
        "fatigue_rate": 0.85,
        "crowd": 1.6,
        "desc": "客が多いと隠れてしまう。静かな日ほど機嫌がいい。",
    },
    "star": {
        "label": "看板猫",
        "emoji": "😺",
        "appeal": 1.55,
        "fatigue_rate": 1.45,
        "crowd": 0.0,
        "desc": "この子目当ての客が来る。人気は抜群だが消耗も激しい。",
    },
    "calm": {
        "label": "マイペース",
        "emoji": "😽",
        "appeal": 0.95,
        "fatigue_rate": 0.65,
        "crowd": 0.0,
        "desc": "何があっても動じない。疲れにくく、機嫌も安定している。",
    },
}

CAT_NAMES = ["みかん", "そら", "だいふく", "こむぎ", "レオ", "ゆず", "もち", "ちゃちゃ"]

# 難易度。営業日数・初期資金・合格ライン・猫の頭数・固定費が変わる。
# 猫が多いほど客は呼べるが、その日の疲労を分散できる一方で餌代がかさむ。
DIFFICULTIES: dict[str, dict[str, Any]] = {
    "easy": {
        "label": "趣味の店",
        "emoji": "🔰",
        "days": 10,
        "funds": 8000,
        "win": 50.0,
        "cats": 4,
        "cost_scale": 0.8,
        "forecast_acc": 0.85,
        "bankruptcy_limit": -3000,
        "desc": "10日・資金8,000円・猫4匹。固定費も安く、合格ラインは50点。",
    },
    "normal": {
        "label": "町の猫カフェ",
        "emoji": "🎯",
        "days": 10,
        "funds": 5000,
        "win": 60.0,
        "cats": 3,
        "cost_scale": 1.0,
        "forecast_acc": 0.72,
        "bankruptcy_limit": -2000,
        "desc": "10日・資金5,000円・猫3匹。標準の経営。合格ラインは60点。",
    },
    "hard": {
        "label": "駅前の激戦区",
        "emoji": "🔥",
        "days": 12,
        "funds": 3500,
        "win": 68.0,
        "cats": 3,
        "cost_scale": 1.25,
        "forecast_acc": 0.60,
        "bankruptcy_limit": -1000,
        "desc": "12日・資金3,500円・猫3匹。家賃が高く、合格ラインは68点。",
    },
    "expert": {
        "label": "潰れかけの店",
        "emoji": "💀",
        "days": 14,
        "funds": 2000,
        "win": 75.0,
        "cats": 2,
        "cost_scale": 1.5,
        "forecast_acc": 0.50,
        "bankruptcy_limit": -300,
        "desc": "14日・資金2,000円・猫2匹。猫が少なく休ませる余裕もない。合格ラインは75点。",
    },
}
DEFAULT_DIFFICULTY = state.DEFAULT_LEVEL
DEFAULT_FORECAST_ACC = 0.65  # forecast_acc が未設定の難易度用のフォールバック


def settings_for(level: str) -> dict[str, Any]:
    return DIFFICULTIES.get(level, DIFFICULTIES[DEFAULT_DIFFICULTY])


def create_cats(count: int, rng: random.Random) -> list[dict[str, Any]]:
    """開店時の猫たちを用意する。気質は必ずばらけさせる。"""
    breeds = list(CAT_BREEDS)
    rng.shuffle(breeds)
    names = list(CAT_NAMES)
    rng.shuffle(names)

    cats = []
    for i in range(count):
        breed = breeds[i % len(breeds)]
        cats.append({
            "id": i,
            "name": names[i],
            "breed": breed,
            "mood": rng.randint(60, 80),
            "fatigue": rng.randint(5, 15),
            "popularity": rng.randint(25, 45),
        })
    return cats


def cat_label(cat: dict[str, Any]) -> str:
    b = CAT_BREEDS[cat["breed"]]
    return f"{b['emoji']} {cat['name']}（{b['label']}）"

HOW_TO_PLAY = f"""
**目標**: 決められた営業日数を終えたときの「総合評価スコア」が合格ライン以上なら勝利です。

1. 毎朝、**価格・広告投資・設備投資・どの猫を休ませるか** を決めます。
2. 「営業開始」を押すと、その日の来客数・売上・コスト・利益と、ランダムイベントの結果が表示されます。
3. 結果画面には **🗣️ 入場者の声** が出ます。値段・広告・設備・猫の様子への反応なので、
   不満が出た点を翌日の方針で直していくと評価が伸びます。
4. 「次の日へ」で翌日に進みます。

**猫は1匹ずつ性格が違います**
- 😻 人懐こい … よく客に懐いて集客できるが、すぐ疲れる
- 🙀 人見知り … 客が多い日は隠れてしまい機嫌が下がる。静かな日向き
- 😺 看板猫 … この子目当ての客が来る。集客は抜群だが消耗が激しい
- 😽 マイペース … 疲れにくく機嫌も安定。集客は普通

**その日の客は、働いている猫で分け合います。** 何匹も出勤させれば1匹あたりの負担は
軽くなりますが、餌代は頭数ぶんかかります。疲れた子を休ませると回復しますが、
その子目当ての客は来ません。**全員休ませると休業日**になります。

**猫のコンディションはすべて 0〜{CAT_STAT_MAX} の目盛りです**
- 😺 機嫌: 高いほど良い（{CAT_MOOD_GOOD}以上を保ちたい）
- 😪 疲労: 低いほど良い（{CAT_FATIGUE_WARN}を超えると機嫌が下がり始める）
- ⭐ 人気: 高いほど客が増える

**客層によって反応が違います**
- 🎒 学生: 価格にとても敏感
- 👪 家族: 価格・広告どちらもバランス重視
- 💼 会社員: 価格にはあまり敏感でないが、数はあまり増えない
- 🧳 観光客: 広告（口コミ）に敏感で客単価が高い

**ランダムイベント**: 雨・テレビ紹介・SNSで話題・猫の脱走などが日替わりで発生し、
来客数や猫の状態、コストに影響します。

**経営破綻（ゲームオーバー）には2つの条件があります**
- 資金が難易度ごとの破産ライン（易しいほど深く、難しいほど0円に近い）を下回る。
- **{DEFICIT_BANKRUPTCY_STREAK}日連続で赤字（その日の利益がマイナス）になると、信用を失って破綻します。**
  {DEFICIT_WARN_STREAK}日続いた時点から評価も下がり始めるので、赤字は早めに止めましょう。

**難易度によって、営業日数・初期資金・合格ライン・猫の頭数・固定費・破産ラインが変わります。**
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


# 予報対象にできるイベント。猫の脱走(escape)は前触れのない事故なので、
# 「当てにいく」対象からそもそも外す（外れの選択肢としても使わない）。
FORECAST_EVENT_IDS = [e["id"] for e in EVENTS if e["id"] != "escape"]

FORECAST_TEXTS: dict[str, str] = {
    "none": "特に変わった噂は聞こえてきません。穏やかな一日になりそうです。",
    "rain": "雲行きが怪しく、明日は雨になりそうだと近所で噂になっています。",
    "tv": "近くでテレビカメラを見かけた、取材が入るかもという噂です。",
    "sns": "SNSで火がつきそうな気配がする、と常連さんが話していました。",
}
FORECAST_NO_OMEN_TEXT = "特に気になる噂は聞こえてきません。（ただし、何が起こるかは開けてみないと分かりません）"


def forecast(rng_seed: int, next_day: int, accuracy: float) -> dict[str, Any]:
    """翌日の来客イベントを「街の噂」として予報する純粋関数。

    実際の翌日イベントは `roll_event(random.Random(rng_seed + next_day))` で
    決まる（`_day_rng` と同じ式）。予報の「当たり/外れ」自体も再現可能にする
    ため、予報専用の乱数系統 `random.Random(rng_seed * 31 + next_day)` を
    別に使う。同じ引数なら常に同じ結果を返す（決定的）。

    accuracy の確率で実際のイベントをそのまま予報し（当たり）、外れた場合は
    実際とは異なるイベントを予報する。猫の脱走(escape)には前触れがないという
    設定のため予報の候補から外してあり、実際の翌日イベントが脱走だった日は
    どう転んでも「当てられない」（必ず外れ扱いで、噂なしの文言にフォール
    バックする）。

    Returns:
        {"predicted": event_id, "hit": bool, "text": str}
    """
    actual = roll_event(random.Random(rng_seed + next_day))
    forecast_rng = random.Random(rng_seed * 31 + next_day)
    roll = forecast_rng.random()

    if actual["id"] == "escape":
        # 予兆のない事故。乱数は消費しつつ、当たりようがないので必ず外れにする。
        predicted = forecast_rng.choice(FORECAST_EVENT_IDS)
        return {"predicted": predicted, "hit": False, "text": FORECAST_NO_OMEN_TEXT}

    if roll < accuracy:
        predicted = actual["id"]
    else:
        predicted = forecast_rng.choice([eid for eid in FORECAST_EVENT_IDS if eid != actual["id"]])

    hit = predicted == actual["id"]
    return {"predicted": predicted, "hit": hit, "text": FORECAST_TEXTS[predicted]}


def cafe_appeal(cats: list[dict[str, Any]]) -> float:
    """店として客を呼ぶ力。働く猫の人気を、気質の集客力で重み付けした平均。"""
    if not cats:
        return 0.0
    total = sum(c["popularity"] * CAT_BREEDS[c["breed"]]["appeal"] for c in cats)
    return total / len(cats)


def cafe_mood(cats: list[dict[str, Any]]) -> float:
    if not cats:
        return 0.0
    return sum(c["mood"] for c in cats) / len(cats)


def simulate_day(gamestate: dict[str, Any], settings: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """1日分の営業をシミュレートする純粋関数。

    Args:
        gamestate: {"funds": float, "reputation": float,
                     "cats": [{"id","name","breed","mood","fatigue","popularity"}, ...],
                     "facility_level": int, "cost_scale": float}
        settings: {"price": int, "ad_budget": int(0-3000, 50円単位),
                    "resting": [猫のid], "target_facility": int}
        rng: 日ごとに固定される random.Random インスタンス。

    Returns:
        当日の結果と更新後ステータス（"gamestate" キー配下）を含む dict。
        入力の gamestate/cats は変更しない。
    """
    cats = [dict(c) for c in gamestate["cats"]]
    funds = gamestate["funds"]
    reputation = gamestate["reputation"]
    facility_level = gamestate["facility_level"]
    cost_scale = gamestate.get("cost_scale", 1.0)
    bankruptcy_limit = gamestate.get("bankruptcy_limit", BANKRUPTCY_LIMIT)
    deficit_streak = gamestate.get("deficit_streak", 0)

    price = settings["price"]
    # 広告は50円単位の予算(ad_budget)で指定する。
    # 後方互換: 旧来の ad_level(0-3) が来た場合は旧アンカーの金額に読み替える。
    if "ad_budget" in settings:
        ad_budget = int(settings["ad_budget"])
    else:
        ad_budget = AD_ANCHORS[int(settings.get("ad_level", 1))][0]
    ad_budget = max(AD_MIN, min(ad_budget, AD_MAX))
    resting_ids = set(settings.get("resting", []))
    # 設備投資は「目標レベル」で指定する。1日で複数段階上げてもよい。
    # 後方互換: 旧来の invest_equipment(bool) が来た場合は1段階アップと解釈する。
    if "target_facility" in settings:
        target_facility = int(settings["target_facility"])
    elif settings.get("invest_equipment"):
        target_facility = facility_level + 1
    else:
        target_facility = facility_level
    target_facility = max(facility_level, min(target_facility, FACILITY_MAX))

    working = [c for c in cats if c["id"] not in resting_ids]
    resting = [c for c in cats if c["id"] in resting_ids]
    closed = not working  # 全員休ませたら開店できない

    event = roll_event(rng)

    # --- 設備投資（目標レベルまで一気に上げる） ---
    equipment_cost = facility_upgrade_cost(facility_level, target_facility)
    new_facility_level = target_facility
    invest_equipment = target_facility > facility_level

    # --- 来客数（客層ごと） ---
    segment_visitors: dict[str, int] = {seg["key"]: 0 for seg in SEGMENTS}
    if not closed:
        appeal = cafe_appeal(working)
        mood_avg = cafe_mood(working)
        popularity_mult = 0.6 + min(appeal, 100) / 100 * 0.8   # 0.6 - 1.4
        mood_mult = 0.75 + mood_avg / 100 * 0.5                # 0.75 - 1.25
        # 何匹働いているかで店の回転が決まる
        operation_mult = 0.4 + 0.6 * (len(working) / max(1, len(cats)))

        for seg in SEGMENTS:
            price_ratio_excess = (price - PRICE_REF) / PRICE_REF
            price_mult = _clamp(1 - seg["price_sensitivity"] * price_ratio_excess, 0.15, 1.8)
            ad_mult = 1 + ad_effect(ad_budget) * seg["ad_sensitivity"]

            mean_visitors = (
                seg["base_visitors"] * price_mult * ad_mult * popularity_mult
                * mood_mult * event["visitor_mult"] * operation_mult
            )
            raw = rng.gauss(mean_visitors, max(mean_visitors * 0.25, 0.5))
            segment_visitors[seg["key"]] = int(_clamp(round(raw), 0, 300))

    total_visitors = min(sum(segment_visitors.values()), 400)

    # --- 売上・コスト ---
    mood_now = cafe_mood(working) if working else 0.0
    tip_revenue = round(total_visitors * (mood_now / 100) * 15)
    revenue = total_visitors * price + tip_revenue
    # 猫の世話代は頭数ぶんかかる。多く飼うほど固定費が重い。
    care_cost = round(CAT_CARE_COST * len(cats) / 3 * cost_scale)
    base_cost = round(BASE_DAILY_COST * cost_scale)
    cost = base_cost + ad_budget + care_cost + event["extra_cost"] + equipment_cost
    profit = revenue - cost
    new_funds = funds + profit

    # --- 猫ごとのコンディション更新 ---
    # その日の客を働いた猫で分け合う。頭数が多いほど1匹あたりの負担が軽い。
    load = total_visitors / max(1, len(working))
    new_cats: list[dict[str, Any]] = []
    for c in cats:
        b = CAT_BREEDS[c["breed"]]
        if c["id"] in resting_ids:
            fatigue = _clamp(c["fatigue"] - 30, 0, 100)
            mood = _clamp(c["mood"] + 10 + event["mood_delta"], 0, 100)
            popularity = _clamp(c["popularity"] - 2 + event["popularity_delta"], 0, 100)
        else:
            gain = max(load * 0.6 * b["fatigue_rate"] - new_facility_level * 5, 2)
            fatigue = _clamp(c["fatigue"] + gain, 0, 100)

            mood_shift = 0.0
            if fatigue > CAT_FATIGUE_WARN:
                mood_shift -= 8
            elif fatigue < 30:
                mood_shift += 4
            # 人見知りは混雑そのものが応える
            mood_shift -= b["crowd"] * max(0.0, load - 12) * 0.35
            mood_shift += event["mood_delta"] + new_facility_level * 1.0
            mood = _clamp(c["mood"] + mood_shift, 0, 100)

            popularity_shift = -1.0 + event["popularity_delta"]
            if mood >= CAT_MOOD_GOOD and fatigue <= 60:
                popularity_shift += 2 * b["appeal"]
            popularity = _clamp(c["popularity"] + popularity_shift, 0, 100)

        new_cats.append({
            **c,
            "mood": int(round(mood)),
            "fatigue": int(round(fatigue)),
            "popularity": int(round(popularity)),
        })

    # --- 評価（満足度 -> 評価スコアへゆっくり収束） ---
    price_ratio_excess = (price - PRICE_REF) / PRICE_REF
    if closed:
        # 休業日は評価が少しだけ落ちる（来た客が入れないため）
        satisfaction = _clamp(reputation - 6, 0, 100)
    else:
        worked_after = [c for c in new_cats if c["id"] not in resting_ids]
        satisfaction = 50 + (cafe_mood(worked_after) - 50) * 0.3 + new_facility_level * 4
        satisfaction -= max(0.0, max(c["fatigue"] for c in worked_after) - 70) * 0.4
        satisfaction -= max(0.0, price_ratio_excess) * 15
        satisfaction += event["satisfaction_delta"]
        satisfaction = _clamp(satisfaction, 0, 100)

    # --- 連続赤字の判定 ---
    # その日の利益がマイナスなら赤字。連続すると信用（評価）を失い、
    # 一定日数続くと資金に関わらず経営破綻する。
    new_deficit_streak = deficit_streak + 1 if profit < 0 else 0
    deficit_penalty = 0.0
    if new_deficit_streak >= DEFICIT_WARN_STREAK:
        deficit_penalty = DEFICIT_REP_PENALTY * (new_deficit_streak - DEFICIT_WARN_STREAK + 1)

    reputation_delta = (satisfaction - reputation) * 0.22 + rng.uniform(-1.5, 1.5)
    new_reputation = _clamp(reputation + reputation_delta - deficit_penalty, 0, 100)

    # 破産条件は2つ: 資金が破産ラインを下回る / 連続赤字が続いて信用を失う。
    funds_bankrupt = new_funds <= bankruptcy_limit
    credit_bankrupt = new_deficit_streak >= DEFICIT_BANKRUPTCY_STREAK
    bankrupt = funds_bankrupt or credit_bankrupt
    bankrupt_reason = ""
    if funds_bankrupt:
        bankrupt_reason = "資金が底をつきました"
    elif credit_bankrupt:
        bankrupt_reason = f"{new_deficit_streak}日連続の赤字で信用を失いました"

    result: dict[str, Any] = {
        "event": event,
        "settings": dict(settings),
        "ad_budget": ad_budget,
        "closed": closed,
        "segment_visitors": segment_visitors,
        "total_visitors": total_visitors,
        "revenue": revenue,
        "tip_revenue": tip_revenue,
        "cost": cost,
        "cost_breakdown": {
            "base": base_cost,
            "ad": ad_budget,
            "cat_care": care_cost,
            "event": event["extra_cost"],
            "equipment": equipment_cost,
        },
        "profit": profit,
        "satisfaction": satisfaction,
        "reputation_delta": reputation_delta,
        "cats_before": [dict(c) for c in gamestate["cats"]],
        "cats_after": new_cats,
        "worked": [c["id"] for c in working],
        "rested": [c["id"] for c in resting],
        "load": load,
        "equipment_invested": invest_equipment,
        "bankrupt": bankrupt,
        "bankrupt_reason": bankrupt_reason,
        "deficit_streak": new_deficit_streak,
        "deficit_penalty": deficit_penalty,
        "gamestate": {
            "funds": new_funds,
            "reputation": new_reputation,
            "cats": new_cats,
            "facility_level": new_facility_level,
            "cost_scale": cost_scale,
            "bankruptcy_limit": bankruptcy_limit,
            "deficit_streak": new_deficit_streak,
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
    cats = result["cats_after"]
    worked = [c for c in cats if c["id"] in result["worked"]]
    event = result["event"]
    price = settings["price"]
    ad_budget = result.get("ad_budget", 0)
    facility = result["gamestate"]["facility_level"]
    visitors = result["total_visitors"]

    if result.get("closed"):
        return [{
            "who": "🚪 貼り紙を見た人",
            "text": "今日はお休みだったんですね……。楽しみにして来たのですが。",
            "tone": "info",
        }]

    # 店の代表値は「働いた猫」から取る。休ませた猫の機嫌は客に見えていない。
    pool = worked or cats
    tired = max(pool, key=lambda c: c["fatigue"])
    grumpy = min(pool, key=lambda c: c["mood"])
    star = max(pool, key=lambda c: c["popularity"])

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

    if tired["fatigue"] > CAT_FATIGUE_WARN:
        candidates.append((12, {
            "who": "👪 家族",
            "text": f"{tired['name']}ちゃん、ぐったりしてました……。無理させてないといいんですけど。",
            "tone": "bad",
        }))
    elif tired["fatigue"] < 30 and grumpy["mood"] >= CAT_MOOD_GOOD:
        candidates.append((4, {
            "who": "🧳 観光客",
            "text": "猫たちがのびのびしてて、見てるだけで癒されました！",
            "tone": "good",
        }))

    if grumpy["mood"] < 40:
        candidates.append((11, {
            "who": "🎒 学生",
            "text": f"{grumpy['name']}が全然こっち来てくれなくて……。機嫌が悪かったのかな。",
            "tone": "bad",
        }))
    elif grumpy["mood"] >= 85:
        candidates.append((4, {
            "who": "👪 家族",
            "text": f"{star['name']}が膝の上で寝てくれました。子どもが大喜びでしたよ。",
            "tone": "good",
        }))

    # 気質ごとの声。誰を働かせるかの判断に直結する。
    shy_crowded = [c for c in worked if c["breed"] == "shy" and result["load"] > 14]
    if shy_crowded:
        candidates.append((10, {
            "who": "🧳 観光客",
            "text": f"{shy_crowded[0]['name']}はずっと棚の上で隠れてました。人が多すぎたのかも。",
            "tone": "bad",
        }))

    star_worked = [c for c in worked if c["breed"] == "star"]
    if star_worked and star_worked[0]["fatigue"] > CAT_FATIGUE_WARN:
        candidates.append((11, {
            "who": "🎒 学生",
            "text": f"{star_worked[0]['name']}目当てで来たんですけど、しんどそうで見てられなくて。",
            "tone": "bad",
        }))
    elif star_worked:
        candidates.append((5, {
            "who": "🧳 観光客",
            "text": f"{star_worked[0]['name']}に会いに来ました！写真いっぱい撮れて満足です。",
            "tone": "good",
        }))

    resting_star = [c for c in cats if c["breed"] == "star" and c["id"] in result["rested"]]
    if resting_star:
        candidates.append((8, {
            "who": "👪 家族",
            "text": f"{resting_star[0]['name']}に会いたかったのに、今日はお休みなんですね……。",
            "tone": "info",
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

    if ad_budget <= 0:
        candidates.append((7, {
            "who": "🧳 観光客",
            "text": "こんなお店があるの、知りませんでした。たまたま通りかかって。",
            "tone": "bad",
        }))
    elif ad_budget >= 1600:
        candidates.append((3, {
            "who": "🎒 学生",
            "text": "広告を見て来ました！ずっと気になってたんです。",
            "tone": "good",
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


def final_evaluation(
    gamestate: dict[str, Any],
    initial_funds: int = INITIAL_FUNDS,
    win_threshold: float = WIN_SCORE_THRESHOLD,
) -> dict[str, Any]:
    """最終日終了後の総合評価を計算する純粋関数。

    initial_funds / win_threshold は難易度によって変わる。
    """
    funds = gamestate["funds"]
    reputation = gamestate["reputation"]

    total_profit = funds - initial_funds
    profit_score = _clamp(50 + total_profit / 150, 0, 100)
    composite = reputation * 0.6 + profit_score * 0.4
    stars = round(composite / 20 * 2) / 2  # 0.5刻みで 0-5
    win = composite >= win_threshold and funds > 0

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

def _default_state(level: str | None = None) -> dict[str, Any]:
    level = level or DEFAULT_DIFFICULTY
    cfg = settings_for(level)
    seed = random.randrange(1_000_000)
    return {
        "rng_seed": seed,
        "difficulty": level,
        "day": 1,
        "phase": "plan",  # "plan" -> "result" -> ("plan" | "gameover")
        "funds": float(cfg["funds"]),
        "initial_funds": cfg["funds"],
        "total_days": cfg["days"],
        "win_threshold": cfg["win"],
        "cost_scale": cfg["cost_scale"],
        "bankruptcy_limit": cfg.get("bankruptcy_limit", BANKRUPTCY_LIMIT),
        "deficit_streak": 0,
        "reputation": 50.0,
        "cats": create_cats(cfg["cats"], random.Random(seed)),
        "facility_level": 0,
        "history": [],
        "pending_result": None,
        "last_price": PRICE_REF,
        "last_ad_budget": 700,
        "bankrupt": False,
        "best_recorded": False,
    }


def _day_rng(s: dict[str, Any]) -> random.Random:
    return random.Random(s["rng_seed"] + s["day"])


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render() -> None:
    ui.game_header("🐈 癒し猫カフェ", NAME, how_to_play=HOW_TO_PLAY)
    # 難易度は遊び方画面で選ばれている。開店時にその条件で店を用意する。
    s = state.game_state(NAME, lambda: _default_state(state.difficulty(NAME)))

    if s["phase"] == "plan":
        _render_plan(s)
    elif s["phase"] == "result":
        _render_result(s)
    else:
        _render_gameover(s)


def _mood_word(v: int) -> str:
    if v >= 80:
        return "ごきげん"
    if v >= CAT_MOOD_GOOD:
        return "上機嫌"
    if v >= 40:
        return "ふつう"
    if v >= 20:
        return "不機嫌"
    return "ごきげん斜め"


def _fatigue_word(v: int) -> str:
    if v < 30:
        return "元気"
    if v <= 60:
        return "ややお疲れ"
    if v <= CAT_FATIGUE_WARN:
        return "お疲れ"
    return "くたくた"


def _popularity_word(v: int) -> str:
    if v >= 70:
        return "看板級"
    if v >= 45:
        return "人気者"
    if v >= 25:
        return "そこそこ"
    return "これから"


def _render_cat_condition(cats: list[dict[str, Any]], resting_ids: set[int] | None = None) -> None:
    """猫たちの状態を数値と日本語の一言で表示する（バーは使わず場所を取らない）。"""
    resting_ids = resting_ids or set()
    st.markdown("**🐈 猫のコンディション**（すべて 0〜100）")
    st.caption(
        f"😺機嫌は高いほど良い（{CAT_MOOD_GOOD}以上を保ちたい）／"
        f"😪疲労は低いほど良い（{CAT_FATIGUE_WARN}超で機嫌が下がる）／⭐人気は高いほど客が増える"
    )
    for c in cats:
        rest = "　💤 お休み" if c["id"] in resting_ids else ""
        st.markdown(
            f"**{cat_label(c)}**{rest}　　"
            f"😺 機嫌 **{c['mood']}**（{_mood_word(c['mood'])}）・"
            f"😪 疲労 **{c['fatigue']}**（{_fatigue_word(c['fatigue'])}）・"
            f"⭐ 人気 **{c['popularity']}**（{_popularity_word(c['popularity'])}）"
        )


def _render_plan(s: dict[str, Any]) -> None:
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    st.subheader(f"📅 {s['day']}日目 / {s['total_days']}日 - 方針決定")

    next_day = s["day"] + 1
    if next_day <= s["total_days"]:
        acc = cfg.get("forecast_acc", DEFAULT_FORECAST_ACC)
        fc = forecast(s["rng_seed"], next_day, acc)
        st.info(f"📻 開店前ニュース: {fc['text']}")

    cats = s["cats"]
    ui.metric_row([
        ("難易度", f"{cfg['emoji']} {cfg['label']}"),
        ("資金", f"¥{int(s['funds']):,}"),
        ("評価スコア", f"{s['reputation']:.0f} / 100"),
        ("合格ライン", f"{s['win_threshold']:.0f}"),
    ])

    tired = [c for c in cats if c["fatigue"] > CAT_FATIGUE_WARN]
    if tired:
        st.warning(
            "😿 " + "、".join(c["name"] for c in tired) +
            f" の疲労が {CAT_FATIGUE_WARN} を超えています。休ませることを検討しましょう。"
        )

    limit = s["bankruptcy_limit"]
    streak = s.get("deficit_streak", 0)
    # 連続赤字はあと1日で破綻、という段階を強めに警告する。
    if streak >= DEFICIT_BANKRUPTCY_STREAK - 1:
        st.error(
            f"🔴 {streak}日連続で赤字です。あと1日でも赤字だと信用を失い経営破綻します。"
            "今日は黒字を死守しましょう。"
        )
    elif streak >= DEFICIT_WARN_STREAK:
        st.warning(
            f"⚠️ {streak}日連続の赤字で評価が下がっています。"
            f"{DEFICIT_BANKRUPTCY_STREAK}日続くと破綻します。"
        )
    if s["funds"] < 0:
        st.warning(f"💸 資金がマイナスです。{limit:,} 円を下回ると経営破綻します。")

    st.markdown(f"**設備レベル**: {FACILITY_LABELS[s['facility_level']]}（{s['facility_level']} / {FACILITY_MAX}）")

    col1, col2 = st.columns(2)
    with col1:
        price = st.slider(
            "価格（1人あたり・円）", min_value=PRICE_MIN, max_value=PRICE_MAX,
            value=int(s.get("last_price", PRICE_REF)), step=PRICE_STEP, key="cc_price",
        )
        ad_budget = st.slider(
            "広告費（円）", min_value=AD_MIN, max_value=AD_MAX,
            value=int(s.get("last_ad_budget", 700)), step=AD_STEP, key="cc_ad_budget",
        )
        st.caption(f"広告費 ¥{ad_budget:,}（{ad_word(ad_budget)}）。高いほど集客が増えます。")
    with col2:
        st.markdown("**😴 今日休ませる猫**")
        st.caption("休ませると疲労が大きく回復しますが、その子目当ての客は来ません。全員休ませると休業日になります。")
        resting: list[int] = []
        for c in cats:
            b = CAT_BREEDS[c["breed"]]
            if st.checkbox(
                f"{b['emoji']} {c['name']}を休ませる（疲労 {c['fatigue']}）",
                key=f"cc_rest_{c['id']}",
            ):
                resting.append(c["id"])

        cur = s["facility_level"]
        st.markdown("**🛠️ 設備投資**")
        if cur < FACILITY_MAX:
            # 現状維持〜最大まで、どのレベルまで上げるかを選べる（1日で複数段も可）。
            options = list(range(cur, FACILITY_MAX + 1))

            def _fac_label(lv: int) -> str:
                if lv == cur:
                    return f"現状維持（{FACILITY_LABELS[cur]}）"
                total = facility_upgrade_cost(cur, lv)
                return f"{FACILITY_LABELS[lv]} まで（累計 ¥{total:,}）"

            target_facility = st.selectbox(
                "どのレベルまで上げるか",
                options=options,
                index=0,
                format_func=_fac_label,
                key="cc_facility_target",
                label_visibility="collapsed",
            )
            if target_facility > cur:
                st.caption(
                    f"{FACILITY_LABELS[cur]} → {FACILITY_LABELS[target_facility]}："
                    f"¥{facility_upgrade_cost(cur, target_facility):,}。"
                    "設備が良いほど猫の疲労が溜まりにくく、満足度も上がります。"
                )
        else:
            st.caption("設備は最大レベル（豪華）です。")
            target_facility = cur

    _render_cat_condition(cats, set(resting))

    if resting and len(resting) == len(cats):
        st.info("🚪 全員を休ませると今日は休業日になります（売上ゼロ・評価が少し下がります）。")

    if st.button("☕ 営業開始", key="cc_start_day", type="primary", use_container_width=True):
        settings = {
            "price": price,
            "ad_budget": ad_budget,
            "resting": resting,
            "target_facility": target_facility,
        }
        gamestate = {
            "funds": s["funds"],
            "reputation": s["reputation"],
            "cats": [dict(c) for c in s["cats"]],
            "facility_level": s["facility_level"],
            "cost_scale": s["cost_scale"],
            "bankruptcy_limit": s["bankruptcy_limit"],
            "deficit_streak": s["deficit_streak"],
        }
        rng = _day_rng(s)
        result = simulate_day(gamestate, settings, rng)

        s["funds"] = result["gamestate"]["funds"]
        s["reputation"] = result["gamestate"]["reputation"]
        s["cats"] = result["gamestate"]["cats"]
        s["facility_level"] = result["gamestate"]["facility_level"]
        s["deficit_streak"] = result["gamestate"]["deficit_streak"]
        s["pending_result"] = result
        s["bankrupt"] = result["bankrupt"]
        s["last_price"] = price
        s["last_ad_budget"] = ad_budget
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

    st.markdown("**猫のコンディション変化**")
    _cat_delta_row(result["cats_before"], result["cats_after"], set(result["rested"]))

    st.markdown(
        f"**評価スコア**: {result['gamestate']['reputation']:.1f} / 100 "
        f"（{'+' if result['reputation_delta'] >= 0 else ''}{result['reputation_delta']:.1f}）"
    )
    st.markdown(f"**資金**: ¥{int(result['gamestate']['funds']):,}")

    if result["equipment_invested"]:
        st.caption(
            f"🛠️ 設備を **{FACILITY_LABELS[s['facility_level']]}**"
            f"（レベル {s['facility_level']} / {FACILITY_MAX}）にアップグレードしました。"
        )

    if result["bankrupt"]:
        reason = result.get("bankrupt_reason") or "経営が立ち行かなくなりました"
        st.error(f"💥 {reason}。経営破綻です。")
        label = "結果を見る"
    elif s["day"] >= s["total_days"]:
        label = "結果を見る"
    else:
        label = "次の日へ"

    if st.button(f"➡️ {label}", key="cc_next_day", type="primary", use_container_width=True):
        if result["bankrupt"] or s["day"] >= s["total_days"]:
            s["phase"] = "gameover"
        else:
            s["day"] += 1
            s["phase"] = "plan"
        st.rerun()


def _cat_delta_row(
    before: list[dict[str, Any]], after: list[dict[str, Any]], rested: set[int]
) -> None:
    """猫ごとの変化。誰にしわ寄せが行ったのかが分かるように1匹ずつ出す。"""
    prev = {c["id"]: c for c in before}
    for c in after:
        b0 = prev.get(c["id"], c)
        with st.container(border=True):
            head = f"**{cat_label(c)}**"
            head += "　💤 お休み" if c["id"] in rested else "　🏪 出勤"
            st.markdown(head)
            cols = st.columns(3)
            for col, (key, label) in zip(
                cols, [("mood", "😺 機嫌"), ("fatigue", "😪 疲労"), ("popularity", "⭐ 人気")]
            ):
                col.metric(
                    label,
                    f"{c[key]} / {CAT_STAT_MAX}",
                    delta=c[key] - b0[key],
                    # 疲労は増えると悪いので、色の意味を反転させる
                    delta_color="inverse" if key == "fatigue" else "normal",
                )


def _render_gameover(s: dict[str, Any]) -> None:
    st.subheader("🏁 最終結果")

    evaluation = final_evaluation(
        {"funds": s["funds"], "reputation": s["reputation"]},
        initial_funds=s["initial_funds"],
        win_threshold=s["win_threshold"],
    )

    if s.get("bankrupt"):
        ui.result_banner(
            False,
            win_msg="",
            lose_msg=f"経営破綻してしまいました…（{s['day']}日目）総合評価スコア {evaluation['score']:.0f} 点",
        )
    else:
        ui.result_banner(
            evaluation["win"],
            win_msg=f"{s['total_days']}日間の営業お疲れ様でした！総合評価スコア {evaluation['score']:.0f} 点で繁盛店の仲間入りです！",
            lose_msg=f"{s['total_days']}日間の営業お疲れ様でした。総合評価スコア {evaluation['score']:.0f} 点… もう一歩でした。",
        )

    stars_full = int(evaluation["stars"])
    stars_half = evaluation["stars"] - stars_full >= 0.5
    star_str = "⭐" * stars_full + ("✨" if stars_half else "")
    st.markdown(f"### 総合評価: {evaluation['score']:.1f} / 100 {star_str}")

    # 自己ベストは総合評価スコアで管理する。二重記録を避けるため1回だけ試みる。
    level = s.get("difficulty", DEFAULT_DIFFICULTY)
    if not s.get("best_recorded"):
        ui.record_and_show_best(NAME, level, evaluation["score"], f"{evaluation['score']:.0f} 点")
        s["best_recorded"] = True
    else:
        ui.personal_best_line(NAME, level)

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
