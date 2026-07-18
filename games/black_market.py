"""ブラックマーケット。

価値が不明な商品をAIとの心理戦で競り落とし、利益を最大化するゲーム。

契約:
- `render()` を公開する（引数なし）。app.py から呼ばれる。
- 画面冒頭で `utils.ui.game_header("💰 ブラックマーケット", NAME, how_to_play=...)` を呼ぶ。
- 状態は `utils.state.game_state(NAME, ...)` が返す dict に保存する。

設計方針:
- 外部APIは使わない。AIの入札はルールベース＋乱数（`random.Random(rng_seed)`）で決定し、
  seed を state に保存することで進行を再現可能にしている。
- ゲームロジックは Streamlit に依存しない純粋関数として切り出し
  （`generate_item`, `estimate_value`, `compute_ai_ceiling`, `ai_opening_bid`,
  `ai_response`, `apply_pressure`, `settle`）、UI 側はそれらを呼び出すだけにしている。
"""

from __future__ import annotations

import random
from typing import Any

import streamlit as st

from utils import state, ui

NAME = "black_market"

# ---------------------------------------------------------------------------
# ゲームバランス定数
# ---------------------------------------------------------------------------

START_MONEY = 1000          # 既定値。実際の値は難易度から取る
TOTAL_ROUNDS = 5
TARGET_PROFIT = 300
MAX_EXCHANGES = 5           # 1商品あたりの最大「入札の押し合い」回数（無限ループ防止）

# 難易度。開始資金・商品数・目標利益に加えて、相手の強気度（上限の底上げ）と
# 情報の値段が変わる。上の難易度ほど「情報を買う余裕」が無くなっていく。
DIFFICULTIES: dict[str, dict[str, Any]] = {
    "easy": {
        "label": "冷やかし",
        "emoji": "🔰",
        "money": 1400,
        "rounds": 5,
        "target": 200,
        "rival_boost": 0.85,   # 相手の入札上限にかかる倍率（小さいほど降りやすい）
        "info_scale": 0.7,     # 情報の値段の倍率
        "desc": "資金1,400円で5品。相手は弱気で、情報も安い。目標は+200円。",
    },
    "normal": {
        "label": "常連",
        "emoji": "🎯",
        "money": 1000,
        "rounds": 5,
        "target": 300,
        "rival_boost": 1.0,
        "info_scale": 1.0,
        "desc": "資金1,000円で5品。標準の卓。目標は+300円。",
    },
    "hard": {
        "label": "同業者",
        "emoji": "🔥",
        "money": 800,
        "rounds": 6,
        "target": 450,
        "rival_boost": 1.15,
        "info_scale": 1.3,
        "desc": "資金800円で6品。相手は強気で情報も高い。目標は+450円。",
    },
    "expert": {
        "label": "元締め",
        "emoji": "💀",
        "money": 600,
        "rounds": 6,
        "target": 600,
        "rival_boost": 1.3,
        "info_scale": 1.6,
        "desc": "資金600円で6品。相手は上限まで食い下がり、情報は贅沢品。目標は+600円。",
    },
}
DEFAULT_DIFFICULTY = state.DEFAULT_LEVEL


def settings_for(level: str) -> dict[str, Any]:
    return DIFFICULTIES.get(level, DIFFICULTIES[DEFAULT_DIFFICULTY])


def info_cost(key: str, info_scale: float = 1.0) -> int:
    """難易度に応じた情報の値段。"""
    return max(1, round(INFO_TYPES[key]["cost"] * info_scale))

# ---------------------------------------------------------------------------
# データ表（モジュール定数）
# ---------------------------------------------------------------------------

ITEM_CATEGORIES: dict[str, dict[str, Any]] = {
    "painting": {
        "label": "絵画",
        "emoji": "🖼️",
        "value_range": (150, 900),
        "flavor": "額縁だけは立派。真贋も由来も闇の中。",
    },
    "jewel": {
        "label": "宝石",
        "emoji": "💎",
        "value_range": (200, 700),
        "flavor": "鑑定書はない。輝きだけが本物っぽい。",
    },
    "usb": {
        "label": "USB",
        "emoji": "💾",
        "value_range": (50, 500),
        "flavor": "中身は誰も確認していない。当たり外れが大きい。",
    },
    "medicine": {
        "label": "薬品",
        "emoji": "🧪",
        "value_range": (80, 400),
        "flavor": "ラベルは外国語で読めない。効能は自己責任。",
    },
    "book": {
        "label": "古書",
        "emoji": "📜",
        "value_range": (100, 600),
        "flavor": "虫食いだらけの一冊。愛好家には堪らないらしい。",
    },
}

ITEM_NAMES: dict[str, list[str]] = {
    "painting": ["署名不明の肖像画", "色褪せた海景画", "行方不明画家の遺作", "静物画（真贋不明）"],
    "jewel": ["曰くつきのルビー", "出所不明のダイヤモンド", "古びたエメラルドの指輪", "重量感のある金の首飾り"],
    "usb": ["ラベルの剥がれたUSBメモリ", "暗号化されたUSBドライブ", "元社員が持ち出したUSB", "封蝋付きの謎のUSB"],
    "medicine": ["未承認の錠剤の瓶", "怪しい液体入りのアンプル", "外国語ラベルの粉末", "冷蔵指定の謎の薬品"],
    "book": ["装丁の傷んだ古書", "焼け跡の残る写本", "禁書と噂される一冊", "著者不明の手稿"],
}

INFO_TYPES: dict[str, dict[str, Any]] = {
    "rumor": {
        "label": "噂",
        "cost": 20,
        "accuracy": 0.45,
        "confidence": "低",
        "desc": "闇市場に流れる噂話。安いが当てにならない。",
    },
    "appraisal": {
        "label": "簡易鑑定",
        "cost": 70,
        "accuracy": 0.22,
        "confidence": "中",
        "desc": "鑑定士にざっと見てもらう。それなりに当たる。",
    },
    "market": {
        "label": "市場価格",
        "cost": 160,
        "accuracy": 0.06,
        "confidence": "高",
        "desc": "闇市場の相場データを買う。高いが精度は抜群。",
    },
}

PERSONALITIES: dict[str, dict[str, Any]] = {
    "collector": {
        "label": "コレクター",
        "emoji": "🎨",
        "desc": "気に入った品には理性を失うほど金を積む。好み次第で豹変する。",
        "favorites": {"painting", "book"},
        "base_ratio": (0.85, 1.30),
        "favorite_bonus": 0.30,
        "bluff_chance": 0.18,
        "fold_chance": 0.04,
    },
    "investor": {
        "label": "投資家",
        "emoji": "📈",
        "desc": "常に採算重視。相場から外れた高値には決して手を出さない。",
        "favorites": {"jewel"},
        "base_ratio": (0.55, 0.90),
        "favorite_bonus": 0.15,
        "bluff_chance": 0.03,
        "fold_chance": 0.22,
    },
    "reseller": {
        "label": "転売屋",
        "emoji": "💼",
        "desc": "転売益を見込んで動く。強気の駆け引きとブラフを好む。",
        "favorites": {"usb", "medicine"},
        "base_ratio": (0.70, 1.05),
        "favorite_bonus": 0.20,
        "bluff_chance": 0.12,
        "fold_chance": 0.12,
    },
}

HOW_TO_PLAY = """
- 闇市場に出品される商品を競り落として利益を稼ごう。
- 商品の**真の価値は隠されている**。「情報を購入する」で噂・簡易鑑定・市場価格を買うと、
  価値の手がかり（推定値）が得られる。ただし情報にはコストがかかり、精度も異なる
  （市場価格 > 簡易鑑定 > 噂 の順に高精度・高コスト）。
- 情報を検討したら「入札を開始する」で交渉スタート。
- **競争相手は3人（コレクター/投資家/転売屋）が同じ卓に着いています。** それぞれ胸の内に
  出せる上限を持ち、あなたの入札に対して独立に「乗る」か「降りる」かを決めます。
  **全員が降りて初めてあなたの落札**です。誰か1人でも乗れば競りは続きます。
- 3人は好みが違います。コレクターは絵画と古書、投資家は宝石、転売屋はUSBと薬品に
  強く出ます。品物を見れば、誰が最後まで食い下がるかが読めます。
- 落札できれば **(真の価値 − 支払額 − 情報コスト)** が利益。競り負けたら情報コストだけが
  損失になる（払いすぎにも情報の買いすぎにも注意）。決着後に相手の上限が開示されるので、
  次のラウンドの読みに使えます。
- 全ラウンド終了時点の累計利益が**目標以上**なら勝利。

**難易度によって、開始資金・商品数・目標利益・相手の強気さ・情報の値段が変わります。**
上の難易度ほど資金が細く情報が高いので、「どこで情報を買うか」がそのまま勝敗になります。
"""


# ---------------------------------------------------------------------------
# 純粋ロジック関数（Streamlit 非依存）
# ---------------------------------------------------------------------------

def generate_item(rng: random.Random) -> dict[str, Any]:
    """商品を1つ生成する。真の価値はここで確定するが呼び出し側には隠す想定。"""
    category = rng.choice(list(ITEM_CATEGORIES.keys()))
    cat_info = ITEM_CATEGORIES[category]
    low, high = cat_info["value_range"]
    true_value = rng.randint(low, high)
    name = rng.choice(ITEM_NAMES[category])
    return {"category": category, "name": name, "true_value": true_value}


def estimate_value(item: dict[str, Any], info_key: str, rng: random.Random) -> dict[str, Any]:
    """情報を購入した際の推定価値を返す（真の価値に精度に応じたノイズを加える）。"""
    info = INFO_TYPES[info_key]
    acc = info["accuracy"]
    noise = rng.uniform(-acc, acc)
    estimate = max(1, round(item["true_value"] * (1 + noise)))
    return {"estimate": estimate, "cost": info["cost"], "label": info["label"], "confidence": info["confidence"]}


def compute_ai_ceiling(
    item: dict[str, Any], personality_key: str, rng: random.Random, boost: float = 1.0
) -> int:
    """AIが内心持っている入札上限（他者には非公開）を計算する。

    boost は難易度による強気度。大きいほど高値まで食い下がってくる。
    """
    p = PERSONALITIES[personality_key]
    lo, hi = p["base_ratio"]
    ratio = rng.uniform(lo, hi)
    if item["category"] in p["favorites"]:
        ratio += p["favorite_bonus"] * rng.uniform(0.5, 1.0)
    return max(1, round(item["true_value"] * ratio * boost))


def ai_opening_bid(ceiling: int, rng: random.Random) -> int:
    """AIの最初の提示額。上限より低めに探りを入れる（サンドバッグ）。"""
    frac = rng.uniform(0.45, 0.7)
    return max(1, round(ceiling * frac))


def ai_response(
    item: dict[str, Any],
    personality_key: str,
    rival: dict[str, Any],
    player_bid: int,
    rng: random.Random,
) -> dict[str, Any]:
    """プレイヤーの入札に対するAIの応答。

    Args:
        rival: `create_rivals` が作った rival dict（"ceiling" を持つ）。
               揺さぶり（`apply_pressure`）が書き込む一時補正キー
               "fold_bonus" / "ceiling_shift" / "bluff_bonus" があれば反映する。

    Returns:
        {"fold": bool, "bid": int | None}
        fold=True の場合 AI は降り、プレイヤーが player_bid で落札する。
        fold=False の場合 bid にAIの新しい提示額が入る（player_bid より高い）。
    """
    p = PERSONALITIES[personality_key]
    ceiling = rival["ceiling"] + rival.get("ceiling_shift", 0)
    fold_chance = min(0.95, max(0.0, p["fold_chance"] + rival.get("fold_bonus", 0.0)))
    bluff_chance = min(0.95, max(0.0, p["bluff_chance"] + rival.get("bluff_bonus", 0.0)))

    if player_bid >= ceiling:
        # 上限を超えられた。基本は降りるが、性格によっては感情的に追い金を出す。
        if rng.random() < bluff_chance:
            bumped_ceiling = round(ceiling * rng.uniform(1.02, 1.15))
            if player_bid < bumped_ceiling:
                new_bid = min(bumped_ceiling, player_bid + max(1, round(player_bid * 0.05)))
                if new_bid > player_bid:
                    return {"fold": False, "bid": new_bid}
        return {"fold": True, "bid": None}

    # まだ上限に余裕がある。性格によっては早めに弱気になって降りることもある。
    if rng.random() < fold_chance:
        return {"fold": True, "bid": None}

    increment = max(1, round((ceiling - player_bid) * rng.uniform(0.2, 0.5)))
    new_bid = min(ceiling, player_bid + increment)
    if new_bid <= player_bid:
        return {"fold": True, "bid": None}
    return {"fold": False, "bid": new_bid}


def settle(won: bool, price_paid: int, true_value: int, info_cost_total: int) -> int:
    """1ラウンドの利益を計算する。落札できなければ情報コストのみが損失になる。"""
    if won:
        return true_value - price_paid - info_cost_total
    return -info_cost_total


# ---------------------------------------------------------------------------
# 競争相手（3体同席）
#
# 3つの性格が同じ卓に着く。それぞれ自分の上限を持ち、プレイヤーの入札に対して
# 独立に「乗る／降りる」を決める。誰かが乗れば競りは続き、全員が降りて初めて
# プレイヤーの落札になる。何が好きな相手なのかを読むと、どこまで積めば全員を
# 振り切れるかが見えてくる。
# ---------------------------------------------------------------------------

def create_rivals(
    item: dict[str, Any], rng: random.Random, boost: float = 1.0
) -> list[dict[str, Any]]:
    """その商品に対する3体の競争相手を作る。上限は各自の胸の内（非公開）。"""
    rivals = []
    for key in PERSONALITIES:
        rivals.append({
            "key": key,
            "ceiling": compute_ai_ceiling(item, key, rng, boost),
            "folded": False,
        })
    return rivals


def rivals_opening(rivals: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    """競りの口火。各自が探りの額を出し、いちばん高い者が最初のリーダーになる。"""
    offers = [(r["key"], ai_opening_bid(r["ceiling"], rng)) for r in rivals]
    top_key, top_bid = max(offers, key=lambda kv: kv[1])
    return {"offers": offers, "leader": top_key, "bid": top_bid}


def rivals_respond(
    item: dict[str, Any],
    rivals: list[dict[str, Any]],
    player_bid: int,
    rng: random.Random,
) -> dict[str, Any]:
    """プレイヤーの入札に対する、降りていない相手全員の反応。

    rivals の "folded" を直接更新する。

    Returns:
        {"actions": [(key, bid|None), ...],   # bid=None は降りた
         "leader": key|None, "bid": int|None}  # 誰も乗らなければ leader=None
    """
    actions: list[tuple[str, int | None]] = []
    for r in rivals:
        if r["folded"]:
            continue
        resp = ai_response(item, r["key"], r, player_bid, rng)
        if resp["fold"]:
            r["folded"] = True
            actions.append((r["key"], None))
        else:
            actions.append((r["key"], resp["bid"]))

    live = [(k, b) for k, b in actions if b is not None]
    if not live:
        return {"actions": actions, "leader": None, "bid": None}
    leader, bid = max(live, key=lambda kv: kv[1])
    return {"actions": actions, "leader": leader, "bid": bid}


def all_folded(rivals: list[dict[str, Any]]) -> bool:
    return all(r["folded"] for r in rivals)


# ---------------------------------------------------------------------------
# 揺さぶり（心理戦のプレイヤー側の一手）
#
# 1商品につき1回だけ宣言できる。まだ卓に残っている相手に対して、性格に応じた
# 一時的な補正（fold_bonus / ceiling_shift / bluff_bonus）を rival dict に
# 書き込む。補正は ai_response がその都度読むだけで、rival dict 自体は
# create_rivals が毎ラウンド作り直すので、次の商品には持ち越さない。
# ---------------------------------------------------------------------------

PRESSURE_FOLD_BONUS = 0.10          # 投資家・(非好物の)コレクターの fold_chance 上乗せ
PRESSURE_COLLECTOR_CEILING_RATIO = 0.08  # 好物のコレクターが意地を張って積む上限の上乗せ比率
PRESSURE_COLLECTOR_FOLD_PENALTY = 0.05   # 好物のコレクターは逆に fold_chance が下がる
PRESSURE_RESELLER_BLUFF_BONUS = 0.12     # 転売屋はブラフ返しに寄る


def apply_pressure(
    item: dict[str, Any], rivals: list[dict[str, Any]], rng: random.Random
) -> list[tuple[str, str]]:
    """揺さぶりをかけたときの、まだ卓に残っている相手それぞれの反応。

    各 rival dict に一時補正キーを書き込む（fold_bonus: float, ceiling_shift: int,
    bluff_bonus: float）。降りた相手には効果がないので何もしない。

    Returns:
        [(rival_key, 反応の一言), ...] 順序は rivals の並び順。
    """
    reactions: list[tuple[str, str]] = []
    for r in rivals:
        if r["folded"]:
            continue
        key = r["key"]
        p = PERSONALITIES[key]
        likes_this = item["category"] in p["favorites"]

        if key == "investor":
            r["fold_bonus"] = r.get("fold_bonus", 0.0) + PRESSURE_FOLD_BONUS
            reactions.append((key, "採算を計算し直し、明らかに及び腰になった。"))
        elif key == "collector":
            if likes_this:
                shift = max(1, round(r["ceiling"] * PRESSURE_COLLECTOR_CEILING_RATIO))
                r["ceiling_shift"] = r.get("ceiling_shift", 0) + shift
                r["fold_bonus"] = r.get("fold_bonus", 0.0) - PRESSURE_COLLECTOR_FOLD_PENALTY
                reactions.append((key, "挑発されてむしろ意地になった。絶対に譲らない目つきだ。"))
            else:
                r["fold_bonus"] = r.get("fold_bonus", 0.0) + PRESSURE_FOLD_BONUS
                reactions.append((key, "興味のない品で意地を張る気はないらしく、及び腰になった。"))
        else:  # reseller
            r["bluff_bonus"] = r.get("bluff_bonus", 0.0) + PRESSURE_RESELLER_BLUFF_BONUS
            reactions.append((key, "動揺した素振りを見せつつ、逆に強気な駆け引きを仕掛け返してきた。"))

    return reactions


# ---------------------------------------------------------------------------
# 状態管理ヘルパー
# ---------------------------------------------------------------------------

def _start_round(s: dict[str, Any]) -> None:
    """ラウンド固有の状態を初期化する（資金・累計利益などグローバルな値は触らない）。"""
    rng: random.Random = s["rng"]
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    s["current_item"] = generate_item(rng)
    s["rivals"] = create_rivals(s["current_item"], rng, cfg["rival_boost"])
    s["info_bought"] = {}
    s["info_cost_total"] = 0
    s["round_phase"] = "shop"
    s["current_bid"] = None
    s["leader"] = None
    s["bid_draft"] = None  # 入札開始時に _reset_bid_draft() が入れ直す
    s["bid_exchanges"] = 0
    s["auction_log"] = []
    s["last_result"] = None
    s["pressure_used"] = False


def _default_state(level: str | None = None) -> dict[str, Any]:
    cfg = settings_for(level or DEFAULT_DIFFICULTY)
    seed = random.randint(0, 2**31 - 1)
    s: dict[str, Any] = {
        "stage": "playing",
        "difficulty": level or DEFAULT_DIFFICULTY,
        "rng_seed": seed,
        "rng": random.Random(seed),
        "money": cfg["money"],
        "start_money": cfg["money"],
        "round_num": 1,
        "total_rounds": cfg["rounds"],
        "target_profit": cfg["target"],
        "history": [],
        "best_recorded": False,
    }
    _start_round(s)
    return s


def _buy_info(s: dict[str, Any], key: str) -> None:
    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    cost = info_cost(key, cfg["info_scale"])
    if key in s["info_bought"] or s["money"] < cost:
        return
    est = estimate_value(s["current_item"], key, s["rng"])
    s["money"] -= cost
    s["info_cost_total"] += cost
    est["cost"] = cost
    s["info_bought"][key] = est


def min_bid(s: dict[str, Any]) -> int:
    """AIの提示額を上回るのに必要な最低額。"""
    return int(s["current_bid"]) + 1


def clamp_bid(s: dict[str, Any], amount: int) -> int:
    """入札額を「最低額以上・手持ち以下」に収める。"""
    return max(min_bid(s), min(int(s["money"]), int(amount)))


def _reset_bid_draft(s: dict[str, Any]) -> None:
    """入札額の初期値を最低額に置く。ここから増減ボタンで積み上げる。"""
    s["bid_draft"] = clamp_bid(s, min_bid(s))


def adjust_bid_draft(s: dict[str, Any], delta: int) -> int:
    """入札額を delta だけ増減して返す（範囲外にはならない）。"""
    s["bid_draft"] = clamp_bid(s, s.get("bid_draft", min_bid(s)) + delta)
    return s["bid_draft"]


def _rival_label(key: str) -> str:
    p = PERSONALITIES[key]
    return f"{p['emoji']} {p['label']}"


def _start_bidding(s: dict[str, Any]) -> None:
    rng: random.Random = s["rng"]
    opening = rivals_opening(s["rivals"], rng)

    s["current_bid"] = opening["bid"]
    s["leader"] = opening["leader"]
    s["auction_log"] = [
        (_rival_label(key), amount) for key, amount in opening["offers"]
    ]
    s["bid_exchanges"] = 0
    s["round_phase"] = "bidding"
    _reset_bid_draft(s)


def _finalize_round(s: dict[str, Any], player_won: bool, price_paid: int) -> None:
    item = s["current_item"]
    if player_won:
        s["money"] -= price_paid
        s["money"] += item["true_value"]
    profit = settle(player_won, price_paid, item["true_value"], s["info_cost_total"])
    result = {
        "round": s["round_num"],
        "item_name": item["name"],
        "category": item["category"],
        # プレイヤーが競り負けたときに、実際に競り落とした相手
        "winner_rival": None if player_won else s.get("leader"),
        "rivals": [
            {"key": r["key"], "ceiling": r["ceiling"], "folded": r["folded"]}
            for r in s["rivals"]
        ],
        "won": player_won,
        "price_paid": price_paid,
        "true_value": item["true_value"],
        "info_cost": s["info_cost_total"],
        "profit": profit,
        "pressure_used": s.get("pressure_used", False),
    }
    s["last_result"] = result
    s["history"].append(result)
    s["round_phase"] = "settled"


def _process_player_bid(s: dict[str, Any], amount: int) -> None:
    s["auction_log"].append(("🧑 あなた", amount))
    s["current_bid"] = amount
    s["leader"] = "player"
    s["bid_exchanges"] += 1

    if s["bid_exchanges"] > MAX_EXCHANGES:
        # 押し合いが長引きすぎた。相手が根負けしてプレイヤーの勝ちとする。
        _finalize_round(s, True, amount)
        return

    rng: random.Random = s["rng"]
    outcome = rivals_respond(s["current_item"], s["rivals"], amount, rng)

    for key, bid in outcome["actions"]:
        if bid is None:
            s["auction_log"].append((_rival_label(key), "降りた"))
        else:
            s["auction_log"].append((_rival_label(key), bid))

    if outcome["leader"] is None:
        # 全員が降りた。プレイヤーの落札。
        _finalize_round(s, True, amount)
        return

    s["current_bid"] = outcome["bid"]
    s["leader"] = outcome["leader"]
    # 相手が上乗せしてきたので、次に必要な最低額まで入札額を引き上げ直す。
    _reset_bid_draft(s)


def _process_pressure(s: dict[str, Any]) -> None:
    """揺さぶりを宣言する。1商品につき1回だけ（呼び出し側が pressure_used を見て制御）。"""
    rng: random.Random = s["rng"]
    reactions = apply_pressure(s["current_item"], s["rivals"], rng)
    s["pressure_used"] = True
    s["auction_log"].append(("🧑 あなた", "😤 揺さぶりをかけた！"))
    if reactions:
        for key, text in reactions:
            s["auction_log"].append((_rival_label(key), text))
    else:
        s["auction_log"].append(("卓の空気", "誰も反応しなかった…（残っている相手がいない）"))


def _process_pass(s: dict[str, Any]) -> None:
    s["auction_log"].append(("🧑 あなた", "パス"))
    _finalize_round(s, False, 0)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render() -> None:
    ui.game_header("💰 ブラックマーケット", NAME, how_to_play=HOW_TO_PLAY)
    # 難易度は遊び方画面で選ばれている。開始時にその設定で卓を組む。
    s = state.game_state(NAME, lambda: _default_state(state.difficulty(NAME)))

    if s["stage"] == "finished":
        _render_finished(s)
        return

    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    ui.metric_row(
        [
            ("難易度", f"{cfg['emoji']} {cfg['label']}"),
            ("資金", f"¥{s['money']:,}"),
            ("ラウンド", f"{s['round_num']} / {s['total_rounds']}"),
            ("累計利益", f"¥{s['money'] - s['start_money']:,}"),
            ("目標利益", f"¥{s['target_profit']:,}"),
        ]
    )
    st.divider()

    phase = s["round_phase"]
    if phase == "shop":
        _render_shop_phase(s)
    elif phase == "bidding":
        _render_bidding_phase(s)
    else:
        _render_settled_phase(s)


def _render_rivals(s: dict[str, Any], show_status: bool = False) -> None:
    """卓に着いている3体。何を好むかが、どこまで積んでくるかの手がかりになる。"""
    st.markdown("#### 🪑 今回の競争相手（3人とも同じ卓にいる）")
    cols = st.columns(len(s["rivals"]))
    item_category = s["current_item"]["category"]
    for col, r in zip(cols, s["rivals"]):
        p = PERSONALITIES[r["key"]]
        likes_this = item_category in p["favorites"]
        with col:
            with st.container(border=True):
                st.markdown(f"**{p['emoji']} {p['label']}**")
                st.caption(p["desc"])
                if likes_this:
                    st.caption("👀 この手の品には目がないらしい。")
                if show_status:
                    st.caption("🏳️ 降りた" if r["folded"] else "🔥 まだ乗っている")


def _render_bought_info(s: dict[str, Any]) -> None:
    """この商品で購入済みの情報（推定価値）を一覧表示する。

    入札中は記憶頼みになりがちなので、買った手がかりをいつでも見返せるようにする。
    表示順は情報の並び（噂→簡易鑑定→市場価格）に合わせる。
    """
    bought = s["info_bought"]
    if not bought:
        return
    st.markdown("#### 🔎 購入済みの情報")
    for key, info in INFO_TYPES.items():
        est = bought.get(key)
        if est:
            st.write(
                f"- **{info['label']}**（信頼度: {est['confidence']}）: "
                f"推定価値 約 **¥{est['estimate']:,}**"
            )


def _render_shop_phase(s: dict[str, Any]) -> None:
    item = s["current_item"]
    cat = ITEM_CATEGORIES[item["category"]]

    st.subheader(f"商品 {s['round_num']}: {cat['emoji']} {item['name']}")
    st.caption(cat["flavor"])
    _render_rivals(s)

    cfg = settings_for(s.get("difficulty", DEFAULT_DIFFICULTY))
    st.markdown("#### 🔎 情報を購入する（任意）")
    cols = st.columns(len(INFO_TYPES))
    for col, (key, info) in zip(cols, INFO_TYPES.items()):
        cost = info_cost(key, cfg["info_scale"])
        with col:
            st.write(f"**{info['label']}**（¥{cost:,} / 信頼度: {info['confidence']}）")
            st.caption(info["desc"])
            bought = key in s["info_bought"]
            if bought:
                st.success(f"推定価値: 約¥{s['info_bought'][key]['estimate']:,}")
            else:
                disabled = s["money"] < cost
                if st.button("購入する", key=f"bm_buy_{key}", disabled=disabled, use_container_width=True):
                    _buy_info(s, key)
                    st.rerun()

    st.divider()
    if st.button("🔨 入札を開始する", key="bm_start_bidding", use_container_width=True):
        _start_bidding(s)
        st.rerun()


def _render_bidding_phase(s: dict[str, Any]) -> None:
    item = s["current_item"]
    cat = ITEM_CATEGORIES[item["category"]]

    st.subheader(f"入札交渉中: {cat['emoji']} {item['name']}")

    # 購入済みの情報（推定価値）をいつでも見返せるようにする。
    _render_bought_info(s)

    leader = s["leader"]
    leader_label = "🧑 あなた" if leader == "player" else _rival_label(leader)
    remaining = [r for r in s["rivals"] if not r["folded"]]
    st.write(
        f"現在の最高額: **¥{s['current_bid']:,}**　/　リーダー: **{leader_label}**"
        f"　/　まだ卓に残っている相手: **{len(remaining)}人**"
    )
    if remaining:
        st.caption("残っている相手: " + "、".join(_rival_label(r["key"]) for r in remaining))
    st.caption("全員が降りればあなたの落札です。")

    _render_rivals(s, show_status=True)

    with st.expander("交渉ログ", expanded=True):
        for who, amt in s["auction_log"]:
            if isinstance(amt, int):
                st.write(f"- {who}: ¥{amt:,}")
            else:
                st.write(f"- {who}: {amt}")

    money = s["money"]
    lowest = min_bid(s)
    if lowest > money:
        st.warning("資金不足でこれ以上の入札はできません。ここで諦めるしかなさそうだ。")
    else:
        draft = clamp_bid(s, s.get("bid_draft", lowest))
        s["bid_draft"] = draft

        st.markdown("#### 入札額")
        st.markdown(
            f"<div style='font-size:2.4rem;font-weight:800;line-height:1.1;'>¥{draft:,}</div>",
            unsafe_allow_html=True,
        )
        st.caption(f"最低 ¥{lowest:,} 〜 手持ち ¥{money:,}")

        # 増減ボタン。上限・下限に達したものは押せなくする。
        step_cols = st.columns(4)
        for col, delta in zip(step_cols, (-10, -1, 1, 10)):
            with col:
                label = f"{delta:+d}"
                blocked = clamp_bid(s, draft + delta) == draft
                if st.button(
                    label,
                    key=f"bm_bid_step_{delta}",
                    use_container_width=True,
                    disabled=blocked,
                ):
                    adjust_bid_draft(s, delta)
                    st.rerun()

        if st.button(
            f"💰 ¥{draft:,} で入札する", key="bm_place_bid", type="primary", use_container_width=True
        ):
            _process_player_bid(s, int(draft))
            st.rerun()

    pressure_used = s.get("pressure_used", False)
    st.caption("😤 揺さぶり: 相手の反応を引き出す（この商品につき1回だけ）。降りやすくなる相手もいれば、好物の品では逆に意地になる相手もいる。")
    if st.button(
        "😤 揺さぶりをかける（この商品で1回）",
        key="bm_pressure",
        use_container_width=True,
        disabled=pressure_used,
    ):
        _process_pressure(s)
        st.rerun()

    if st.button("🏳️ 諦める（パス）", key="bm_pass", use_container_width=True):
        _process_pass(s)
        st.rerun()


def _render_settled_phase(s: dict[str, Any]) -> None:
    r = s["last_result"]
    cat = ITEM_CATEGORIES[r["category"]]

    st.subheader(f"結果: {cat['emoji']} {r['item_name']}")
    if r["won"]:
        st.success(f"落札成功！ 支払額 ¥{r['price_paid']:,} ／ 真の価値は ¥{r['true_value']:,} だった。")
    else:
        winner = r.get("winner_rival")
        who = _rival_label(winner) if winner else "競争相手"
        st.error(f"{who}に競り負けた…（真の価値は ¥{r['true_value']:,} だった）")

    ui.metric_row(
        [
            ("情報コスト", f"¥{r['info_cost']:,}"),
            ("このラウンドの利益", f"¥{r['profit']:,}"),
            ("累計利益", f"¥{s['money'] - s['start_money']:,}"),
        ]
    )

    # 手の内を最後に開示する。次のラウンドで誰をどこまで警戒すべきかの学習材料。
    with st.expander("🃏 相手の手の内（決着後に開示）"):
        if r.get("pressure_used"):
            st.caption("😤 この商品では揺さぶりをかけた影響が、相手の降り際や粘りに出ていたかもしれない。")
        for rv in r.get("rivals", []):
            p = PERSONALITIES[rv["key"]]
            st.write(
                f"- {p['emoji']} **{p['label']}** … 出せる上限は ¥{rv['ceiling']:,} だった"
                f"（{'降りた' if rv['folded'] else '最後まで残っていた'}）"
            )

    st.divider()
    is_last = s["round_num"] >= s["total_rounds"]
    label = "🏁 最終結果を見る" if is_last else "➡ 次の商品へ"
    if st.button(label, key="bm_next_round", use_container_width=True):
        if is_last:
            s["stage"] = "finished"
        else:
            s["round_num"] += 1
            _start_round(s)
        st.rerun()


def _render_finished(s: dict[str, Any]) -> None:
    profit = s["money"] - s["start_money"]
    win = profit >= s["target_profit"]

    ui.result_banner(
        win,
        f"目標達成！ 最終利益 ¥{profit}（目標 ¥{s['target_profit']}）で闇市場を制した。",
        f"目標未達… 最終利益 ¥{profit}（目標 ¥{s['target_profit']}）。次こそは情報収集と駆け引きを見直そう。",
    )

    profit_label = f"¥{profit:,}" if profit >= 0 else f"-¥{abs(profit):,}"
    if not s.get("best_recorded"):
        ui.record_and_show_best(NAME, s["difficulty"], profit, profit_label)
        s["best_recorded"] = True
    else:
        ui.personal_best_line(NAME, s["difficulty"])

    ui.metric_row(
        [
            ("最終資金", f"¥{s['money']}"),
            ("最終利益", f"¥{profit}"),
            ("目標利益", f"¥{s['target_profit']}"),
        ]
    )

    st.markdown("#### 📜 ラウンド履歴")
    for r in s["history"]:
        cat = ITEM_CATEGORIES[r["category"]]
        outcome = "落札" if r["won"] else "競り負け"
        st.write(f"- 第{r['round']}回 {cat['emoji']} {r['item_name']}: {outcome} / 利益 ¥{r['profit']}")

    st.divider()
    if st.button("🔁 もう一度プレイ", key="bm_play_again", use_container_width=True):
        state.reset_game(NAME)
        st.rerun()
