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
  `ai_response`, `settle`）、UI 側はそれらを呼び出すだけにしている。
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

START_MONEY = 1000          # 開始資金
TOTAL_ROUNDS = 5            # 商品の数（ラウンド数）
TARGET_PROFIT = 300         # 勝利条件: 累計利益がこの値以上
MAX_EXCHANGES = 5           # 1商品あたりの最大「入札の押し合い」回数（無限ループ防止）

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

HOW_TO_PLAY = f"""
- 全{TOTAL_ROUNDS}回の競りで、闇市場に出品される商品を競り落として利益を稼ごう。
- 商品の**真の価値は隠されている**。「情報を購入する」で噂・簡易鑑定・市場価格を買うと、
  価値の手がかり（推定値）が得られる。ただし情報にはコストがかかり、精度も異なる
  （市場価格 > 簡易鑑定 > 噂 の順に高精度・高コスト）。
- 情報を検討したら「入札を開始する」で交渉スタート。AIがまず金額を提示してくるので、
  上回る金額を提示するか、諦めるか選ぼう。AIの性格（コレクター/投資家/転売屋）によって
  強気度やブラフの出し方が異なる。
- 落札できれば **(真の価値 − 支払額 − 情報コスト)** が利益。競り負けたら情報コストだけが
  損失になる（払いすぎにも情報の買いすぎにも注意）。
- 全{TOTAL_ROUNDS}回終了時点の累計利益が **+{TARGET_PROFIT}** 以上なら勝利。
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


def compute_ai_ceiling(item: dict[str, Any], personality_key: str, rng: random.Random) -> int:
    """AIが内心持っている入札上限（他者には非公開）を計算する。"""
    p = PERSONALITIES[personality_key]
    lo, hi = p["base_ratio"]
    ratio = rng.uniform(lo, hi)
    if item["category"] in p["favorites"]:
        ratio += p["favorite_bonus"] * rng.uniform(0.5, 1.0)
    return max(1, round(item["true_value"] * ratio))


def ai_opening_bid(ceiling: int, rng: random.Random) -> int:
    """AIの最初の提示額。上限より低めに探りを入れる（サンドバッグ）。"""
    frac = rng.uniform(0.45, 0.7)
    return max(1, round(ceiling * frac))


def ai_response(
    item: dict[str, Any],
    personality_key: str,
    ceiling: int,
    player_bid: int,
    rng: random.Random,
) -> dict[str, Any]:
    """プレイヤーの入札に対するAIの応答。

    Returns:
        {"fold": bool, "bid": int | None}
        fold=True の場合 AI は降り、プレイヤーが player_bid で落札する。
        fold=False の場合 bid にAIの新しい提示額が入る（player_bid より高い）。
    """
    p = PERSONALITIES[personality_key]
    if player_bid >= ceiling:
        # 上限を超えられた。基本は降りるが、性格によっては感情的に追い金を出す。
        if rng.random() < p["bluff_chance"]:
            bumped_ceiling = round(ceiling * rng.uniform(1.02, 1.15))
            if player_bid < bumped_ceiling:
                new_bid = min(bumped_ceiling, player_bid + max(1, round(player_bid * 0.05)))
                if new_bid > player_bid:
                    return {"fold": False, "bid": new_bid}
        return {"fold": True, "bid": None}

    # まだ上限に余裕がある。性格によっては早めに弱気になって降りることもある。
    if rng.random() < p["fold_chance"]:
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
# 状態管理ヘルパー
# ---------------------------------------------------------------------------

def _start_round(s: dict[str, Any]) -> None:
    """ラウンド固有の状態を初期化する（資金・累計利益などグローバルな値は触らない）。"""
    rng: random.Random = s["rng"]
    s["current_item"] = generate_item(rng)
    s["current_personality"] = rng.choice(list(PERSONALITIES.keys()))
    s["info_bought"] = {}
    s["info_cost_total"] = 0
    s["round_phase"] = "shop"
    s["ai_ceiling"] = None
    s["current_bid"] = None
    s["leader"] = None
    s["bid_exchanges"] = 0
    s["auction_log"] = []
    s["last_result"] = None


def _default_state() -> dict[str, Any]:
    seed = random.randint(0, 2**31 - 1)
    s: dict[str, Any] = {
        "stage": "playing",
        "rng_seed": seed,
        "rng": random.Random(seed),
        "money": START_MONEY,
        "round_num": 1,
        "total_rounds": TOTAL_ROUNDS,
        "target_profit": TARGET_PROFIT,
        "history": [],
    }
    _start_round(s)
    return s


def _buy_info(s: dict[str, Any], key: str) -> None:
    info = INFO_TYPES[key]
    if key in s["info_bought"] or s["money"] < info["cost"]:
        return
    est = estimate_value(s["current_item"], key, s["rng"])
    s["money"] -= info["cost"]
    s["info_cost_total"] += info["cost"]
    s["info_bought"][key] = est


def _start_bidding(s: dict[str, Any]) -> None:
    rng: random.Random = s["rng"]
    item = s["current_item"]
    ceiling = compute_ai_ceiling(item, s["current_personality"], rng)
    opening = ai_opening_bid(ceiling, rng)
    s["ai_ceiling"] = ceiling
    s["current_bid"] = opening
    s["leader"] = "ai"
    s["auction_log"] = [("🤖 AI", opening)]
    s["bid_exchanges"] = 0
    s["round_phase"] = "bidding"


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
        "personality": s["current_personality"],
        "won": player_won,
        "price_paid": price_paid,
        "true_value": item["true_value"],
        "info_cost": s["info_cost_total"],
        "profit": profit,
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
        # 押し合いが長引きすぎた。AIが根負けしてプレイヤーの勝ちとする。
        _finalize_round(s, True, amount)
        return

    rng: random.Random = s["rng"]
    item = s["current_item"]
    resp = ai_response(item, s["current_personality"], s["ai_ceiling"], amount, rng)
    if resp["fold"]:
        _finalize_round(s, True, amount)
    else:
        s["auction_log"].append(("🤖 AI", resp["bid"]))
        s["current_bid"] = resp["bid"]
        s["leader"] = "ai"


def _process_pass(s: dict[str, Any]) -> None:
    s["auction_log"].append(("🧑 あなた", "パス"))
    _finalize_round(s, False, 0)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render() -> None:
    ui.game_header("💰 ブラックマーケット", NAME, how_to_play=HOW_TO_PLAY)
    s = state.game_state(NAME, _default_state)

    if s["stage"] == "finished":
        _render_finished(s)
        return

    ui.metric_row(
        [
            ("資金", f"¥{s['money']}"),
            ("ラウンド", f"{s['round_num']} / {s['total_rounds']}"),
            ("累計利益", f"¥{s['money'] - START_MONEY}"),
            ("目標利益", f"¥{s['target_profit']}"),
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


def _render_shop_phase(s: dict[str, Any]) -> None:
    item = s["current_item"]
    cat = ITEM_CATEGORIES[item["category"]]
    personality = PERSONALITIES[s["current_personality"]]

    st.subheader(f"商品 {s['round_num']}: {cat['emoji']} {item['name']}")
    st.caption(cat["flavor"])
    st.write(f"今回の競争相手: {personality['emoji']} **{personality['label']}** ー {personality['desc']}")

    st.markdown("#### 🔎 情報を購入する（任意）")
    cols = st.columns(len(INFO_TYPES))
    for col, (key, info) in zip(cols, INFO_TYPES.items()):
        with col:
            st.write(f"**{info['label']}**（¥{info['cost']} / 信頼度: {info['confidence']}）")
            st.caption(info["desc"])
            bought = key in s["info_bought"]
            if bought:
                st.success(f"推定価値: 約¥{s['info_bought'][key]['estimate']}")
            else:
                disabled = s["money"] < info["cost"]
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
    st.write(f"現在の最高額: **¥{s['current_bid']}**（現在のリーダー: 🤖 AI）")

    with st.expander("交渉ログ", expanded=True):
        for who, amt in s["auction_log"]:
            if isinstance(amt, int):
                st.write(f"- {who}: ¥{amt}")
            else:
                st.write(f"- {who}: {amt}")

    money = s["money"]
    min_bid = s["current_bid"] + 1
    if min_bid > money:
        st.warning("資金不足でこれ以上の入札はできません。ここで諦めるしかなさそうだ。")
    else:
        default_bid = min(min_bid + 10, money)
        bid_amount = st.number_input(
            "入札額",
            min_value=int(min_bid),
            max_value=int(money),
            value=int(default_bid),
            step=1,
            key="bm_bid_amount",
        )
        if st.button("💰 この金額で入札する", key="bm_place_bid", use_container_width=True):
            _process_player_bid(s, int(bid_amount))
            st.rerun()

    if st.button("🏳️ 諦める（パス）", key="bm_pass", use_container_width=True):
        _process_pass(s)
        st.rerun()


def _render_settled_phase(s: dict[str, Any]) -> None:
    r = s["last_result"]
    cat = ITEM_CATEGORIES[r["category"]]
    personality = PERSONALITIES[r["personality"]]

    st.subheader(f"結果: {cat['emoji']} {r['item_name']}")
    if r["won"]:
        st.success(f"落札成功！ 支払額 ¥{r['price_paid']} ／ 真の価値は ¥{r['true_value']} だった。")
    else:
        st.error(f"{personality['emoji']} {personality['label']}に競り負けた…（真の価値は ¥{r['true_value']} だった）")

    ui.metric_row(
        [
            ("情報コスト", f"¥{r['info_cost']}"),
            ("このラウンドの利益", f"¥{r['profit']}"),
            ("累計利益", f"¥{s['money'] - START_MONEY}"),
        ]
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
    profit = s["money"] - START_MONEY
    win = profit >= s["target_profit"]

    ui.result_banner(
        win,
        f"目標達成！ 最終利益 ¥{profit}（目標 ¥{s['target_profit']}）で闇市場を制した。",
        f"目標未達… 最終利益 ¥{profit}（目標 ¥{s['target_profit']}）。次こそは情報収集と駆け引きを見直そう。",
    )

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
