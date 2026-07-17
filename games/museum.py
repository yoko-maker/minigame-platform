"""博物館潜入。

契約:
- `render()` を公開する（引数なし）。app.py から呼ばれる。
- 画面冒頭で `utils.ui.game_header("💎 博物館潜入", NAME, how_to_play=...)` を呼ぶ。
- 状態は `utils.state.game_state(NAME, ...)` が返す dict に保存する。

設計メモ:
- ロジック（マップ生成・移動判定・特性/アイテム使用）は Streamlit に依存しない
  純粋関数として実装し、UI 描画関数から呼び出す構成にしている。
  自己テストは scratchpad 上のスクリプトから直接 import して検証した。
- マップは「移動を遮る壁」を持たない全結線グリッド（上下左右に常に移動可）
  として生成するため、宝石マス・出口マスは start から常に到達可能である
  ことが構造的に保証される。念のため BFS (`reachable_cells`) で明示的に
  検証してから採用する。
"""

from __future__ import annotations

import random
from collections import deque
from typing import Any

import streamlit as st

from utils import state, ui

NAME = "museum"

# ---------------------------------------------------------------------------
# 定数・データテーブル
# ---------------------------------------------------------------------------

GRID_SIZE = 7
MAX_TURNS = 32
ALERT_MAX = 100
TRAIT_CHARGES = 3
HAZARD_DENSITY = 0.32

# 難易度。盤面の広さ・障害物の多さ・持ち時間・特性とアイテムの余裕を一括で変える。
# 広さと密度が変わると生成されるマップの表情も変わるので、選ぶだけで遊べる盤面が増える。
DIFFICULTIES: dict[str, dict[str, Any]] = {
    "novice": {
        "label": "見習い",
        "emoji": "🔰",
        "size": 6,
        "turns": 40,
        "density": 0.20,
        "charges": 4,
        "items": {"emp": 3, "smoke": 3, "mirror": 3, "drone": 2},
        "desc": "6×6の小さな館。障害物は少なく、道具にも余裕がある。まずはここから。",
    },
    "normal": {
        "label": "一人前",
        "emoji": "🎯",
        "size": 7,
        "turns": 32,
        "density": 0.32,
        "charges": 3,
        "items": {"emp": 2, "smoke": 2, "mirror": 2, "drone": 1},
        "desc": "7×7。障害物と道具が釣り合った標準の館。",
    },
    "hard": {
        "label": "怪盗",
        "emoji": "🔥",
        "size": 8,
        "turns": 34,
        "density": 0.42,
        "charges": 2,
        "items": {"emp": 1, "smoke": 1, "mirror": 1, "drone": 1},
        "desc": "8×8。警備が厚く道具も乏しい。通る道をよく選ぶこと。",
    },
    "master": {
        "label": "伝説",
        "emoji": "💀",
        "size": 9,
        "turns": 36,
        "density": 0.46,
        "charges": 2,
        "items": {"emp": 1, "smoke": 1, "mirror": 0, "drone": 0},
        "desc": "9×9。鏡もドローンも無い。館の記憶と勘だけが頼り。",
    },
}
DIFFICULTY_ORDER = ["novice", "normal", "hard", "master"]
DEFAULT_DIFFICULTY = "normal"

CELL_EMPTY = "empty"
CELL_LASER = "laser"
CELL_CAMERA = "camera"
CELL_GUARD = "guard"
CELL_GEM = "gem"
CELL_EXIT = "exit"

CELL_ICON = {
    CELL_EMPTY: "・",
    CELL_LASER: "🔴",
    CELL_CAMERA: "📷",
    CELL_GUARD: "💂",
    CELL_GEM: "💎",
    CELL_EXIT: "🚪",
}
PLAYER_ICON = "🥷"
FOG_ICON = "❔"

HAZARD_LABELS = {CELL_LASER: "レーザー", CELL_CAMERA: "監視カメラ", CELL_GUARD: "警備員"}
ALERT_GAIN = {CELL_LASER: 30, CELL_CAMERA: 25}

DIRECTIONS: dict[str, tuple[int, int]] = {
    "up": (-1, 0),
    "down": (1, 0),
    "left": (0, -1),
    "right": (0, 1),
}

TRAITS: dict[str, dict[str, str]] = {
    "hacker": {
        "label": "ハッカー",
        "emoji": "💻",
        "counters": CELL_CAMERA,
        "desc": "監視カメラの回線に侵入し、無力化しながら進む。",
    },
    "engineer": {
        "label": "エンジニア",
        "emoji": "🔧",
        "counters": CELL_LASER,
        "desc": "センサーの配線を熟知し、レーザーを一時停止できる。",
    },
    "thief": {
        "label": "怪盗",
        "emoji": "🥷",
        "counters": CELL_GUARD,
        "desc": "足音を完全に消し、警備員の横をすり抜ける。",
    },
    "illusionist": {
        "label": "幻術師",
        "emoji": "🎭",
        "counters": CELL_GUARD,
        "desc": "幻影で気を引き、警備員を別方向へ誘導する。",
    },
}
TRAIT_ORDER = ["hacker", "engineer", "thief", "illusionist"]

ITEMS: dict[str, dict[str, str]] = {
    "emp": {"label": "EMP", "emoji": "⚡", "counters": CELL_CAMERA, "desc": "監視カメラを一時停止する。"},
    "smoke": {"label": "煙幕", "emoji": "💨", "counters": CELL_GUARD, "desc": "警備員の視界を遮る。"},
    "mirror": {"label": "鏡", "emoji": "🪞", "counters": CELL_LASER, "desc": "レーザーを反射して通過する。"},
    "drone": {"label": "ドローン", "emoji": "🛸", "counters": "reveal", "desc": "マップ全体を偵察して可視化する。"},
}
ITEM_ORDER = ["emp", "smoke", "mirror", "drone"]
ITEM_START_COUNTS = {"emp": 2, "smoke": 2, "mirror": 2, "drone": 1}
ITEM_FOR_HAZARD = {CELL_LASER: "mirror", CELL_CAMERA: "emp", CELL_GUARD: "smoke"}

HOW_TO_PLAY = """
**目的**: 博物館に忍び込み、💎宝石を手に入れて🚪出口から脱出しよう。

**進め方**
1. 潜入する館（難易度）を選ぶ。広さ・警備の厚さ・持ち時間・道具の数が変わる。
   「🎲 別の館にする」で同じ難易度のまま別の間取りを引き直せる。
2. 得意な特性を1つ選ぶ（対応する障害物を数回まで無効化できる）。
3. **キーボードの十字キー（← ↑ ↓ →）** か ⬆️⬇️⬅️➡️ ボタンで移動する
   （1手ごとにターンを消費）。
4. 💎宝石のマスに入ると自動的に回収する。
5. 宝石を持って🚪出口のマスに入れば脱出成功（勝利）。

**障害物**
- 🔴レーザー・📷監視カメラに無防備で踏み込むと警戒度が上昇する。警戒度が100%になると失敗。
- 💂警備員に無防備で踏み込むと、その場で見つかり即座に失敗になる。

**特性（開始時に1つ選択。対応する障害物を無効化できる。回数は難易度による）**
- 💻ハッカー: 監視カメラを無効化
- 🔧エンジニア: レーザーを停止
- 🥷怪盗: 足音を消して警備員をやり過ごす
- 🎭幻術師: 幻術で警備員を誘導

**アイテム（対応する障害物に入ったとき自動で消費される）**
- ⚡EMP: カメラを一時停止 / 💨煙幕: 警備員をやり過ごす / 🪞鏡: レーザーを反射
- 🛸ドローンだけはボタンで手動使用。使うとマップ全体が見えるようになる（それ以外は歩いた周辺しか見えない）。

**その他**
- ターン数か警戒度が上限に達すると脱出失敗。
"""


# ---------------------------------------------------------------------------
# 純粋ロジック（Streamlit 非依存）
# ---------------------------------------------------------------------------


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def reachable_cells(size: int, start: tuple[int, int]) -> set[tuple[int, int]]:
    """start から上下左右移動のみで到達可能な全マスを BFS で求める。

    このゲームのマップは移動を遮る壁を持たない設計のため、理論上は常に
    盤面全体が返るはずだが、宝石・出口の到達可能性を明示的に保証するため
    generate_map() から必ずこの関数で検証してから採用する。
    """
    visited = {start}
    queue: deque[tuple[int, int]] = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc in DIRECTIONS.values():
            nr, nc = r + dr, c + dc
            if 0 <= nr < size and 0 <= nc < size and (nr, nc) not in visited:
                visited.add((nr, nc))
                queue.append((nr, nc))
    return visited


def generate_map(
    rng: random.Random, size: int, hazard_density: float = HAZARD_DENSITY
) -> dict[str, Any]:
    """ランダムなマップを1つ生成して返す。

    Args:
        size: 盤面の一辺のマス数。
        hazard_density: 1マスが障害物になる確率。難易度で変わる。

    戻り値: {"size", "grid", "start", "gem", "exit"}
    grid[r][c] は CELL_* のいずれか。start/gem/exit は必ず到達可能。
    """
    for _attempt in range(50):
        border_cells = [
            (r, c)
            for r in range(size)
            for c in range(size)
            if r in (0, size - 1) or c in (0, size - 1)
        ]
        start = rng.choice(border_cells)

        far_cells = [cell for cell in border_cells if _manhattan(cell, start) >= size]
        if not far_cells:
            far_cells = [cell for cell in border_cells if cell != start]
        exit_pos = rng.choice(far_cells)

        reserved = {start, exit_pos}
        interior_candidates = [
            (r, c) for r in range(size) for c in range(size) if (r, c) not in reserved
        ]
        gem_candidates = [
            cell
            for cell in interior_candidates
            if _manhattan(cell, start) >= 2 and _manhattan(cell, exit_pos) >= 2
        ]
        if not gem_candidates:
            gem_candidates = interior_candidates
        gem = rng.choice(gem_candidates)
        reserved.add(gem)

        grid = [[CELL_EMPTY for _ in range(size)] for _ in range(size)]
        grid[exit_pos[0]][exit_pos[1]] = CELL_EXIT
        grid[gem[0]][gem[1]] = CELL_GEM

        hazard_types = [CELL_LASER, CELL_CAMERA, CELL_GUARD]
        for r in range(size):
            for c in range(size):
                if (r, c) in reserved:
                    continue
                if rng.random() < hazard_density:
                    grid[r][c] = rng.choice(hazard_types)

        reachable = reachable_cells(size, start)
        if gem in reachable and exit_pos in reachable:
            return {"size": size, "grid": grid, "start": start, "gem": gem, "exit": exit_pos}

    # 実質的に到達しないフォールバック（全結線グリッドのため理論上不要）。
    raise RuntimeError("到達可能なマップの生成に失敗しました。")


def _default_state() -> dict[str, Any]:
    return {
        "phase": "trait_select",  # trait_select -> playing -> ended
        "difficulty": DEFAULT_DIFFICULTY,
        "trait": None,
        "trait_charges": 0,
        "items": dict(ITEM_START_COUNTS),
        "max_turns": MAX_TURNS,
        "rng_seed": random.randrange(1_000_000_000),
        "map": None,
        "pos": None,
        "has_gem": False,
        "alert": 0,
        "turns_used": 0,
        "revealed": set(),
        "revealed_all": False,
        "log": [],
        "win": None,
        "end_reason": "",
    }


def _reveal_around(gs: dict[str, Any], pos: tuple[int, int]) -> None:
    r, c = pos
    size = gs["map"]["size"]
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            nr, nc = r + dr, c + dc
            if 0 <= nr < size and 0 <= nc < size:
                gs["revealed"].add((nr, nc))


def set_difficulty(gs: dict[str, Any], key: str) -> None:
    """潜入前に難易度を選ぶ。開始後は変えられない。"""
    if gs["phase"] == "trait_select" and key in DIFFICULTIES:
        gs["difficulty"] = key


def reroll_map(gs: dict[str, Any]) -> None:
    """同じ難易度のまま、別の館（別のマップ）を引き直す。"""
    if gs["phase"] == "trait_select":
        gs["rng_seed"] = random.randrange(1_000_000_000)


def select_trait(gs: dict[str, Any], trait_key: str) -> None:
    """特性を確定し、選んだ難易度でマップを生成してプレイ開始状態にする。"""
    if gs["phase"] != "trait_select" or trait_key not in TRAITS:
        return
    diff = DIFFICULTIES[gs.get("difficulty", DEFAULT_DIFFICULTY)]

    gs["trait"] = trait_key
    gs["trait_charges"] = diff["charges"]
    gs["items"] = dict(diff["items"])
    gs["max_turns"] = diff["turns"]

    rng = random.Random(gs["rng_seed"])
    gmap = generate_map(rng, diff["size"], diff["density"])
    gs["map"] = gmap
    gs["pos"] = gmap["start"]
    gs["has_gem"] = False
    gs["alert"] = 0
    gs["turns_used"] = 0
    gs["revealed"] = set()
    gs["revealed_all"] = False
    gs["log"] = ["潜入開始。物音を立てないように進もう。"]
    gs["phase"] = "playing"
    _reveal_around(gs, gmap["start"])


def use_trait(gs: dict[str, Any], hazard_type: str) -> bool:
    """選択中の特性で hazard_type を無力化できればチャージを消費して True を返す。"""
    trait = gs.get("trait")
    if trait and TRAITS[trait]["counters"] == hazard_type and gs["trait_charges"] > 0:
        gs["trait_charges"] -= 1
        return True
    return False


def use_item(gs: dict[str, Any], item_key: str) -> bool:
    """アイテムを1つ消費できれば True を返す（在庫が無ければ False）。"""
    if gs["items"].get(item_key, 0) > 0:
        gs["items"][item_key] -= 1
        return True
    return False


def use_drone(gs: dict[str, Any]) -> dict[str, Any]:
    """ドローンを使ってマップ全体を可視化する（ターンは消費しない）。"""
    if gs["phase"] != "playing":
        return {"ok": False, "message": "今は使用できない。"}
    if gs["revealed_all"]:
        return {"ok": False, "message": "すでにマップ全体は偵察済みだ。"}
    if not use_item(gs, "drone"):
        return {"ok": False, "message": "ドローンの手持ちが無い。"}
    gs["revealed_all"] = True
    msg = "🛸 ドローンを飛ばし、館内全体を偵察した！"
    gs["log"].append(msg)
    gs["log"] = gs["log"][-6:]
    return {"ok": True, "message": msg}


def _resolve_hazard(gs: dict[str, Any], r: int, c: int, hazard_type: str, result: dict[str, Any]) -> None:
    grid = gs["map"]["grid"]

    if use_trait(gs, hazard_type):
        grid[r][c] = CELL_EMPTY
        trait_info = TRAITS[gs["trait"]]
        result["message"] = (
            f"{trait_info['emoji']} {trait_info['label']}の能力で"
            f"{HAZARD_LABELS[hazard_type]}を突破した！"
        )
        result["event"] = "trait_used"
        return

    item_key = ITEM_FOR_HAZARD.get(hazard_type)
    if item_key and use_item(gs, item_key):
        grid[r][c] = CELL_EMPTY
        item_info = ITEMS[item_key]
        result["message"] = (
            f"{item_info['emoji']} {item_info['label']}を使って"
            f"{HAZARD_LABELS[hazard_type]}を突破した！"
        )
        result["event"] = "item_used"
        return

    # 無防備での接触：発覚する。
    grid[r][c] = CELL_EMPTY  # 発覚済みマスとして扱い、以後は再発生させない
    if hazard_type == CELL_GUARD:
        gs["phase"] = "ended"
        gs["win"] = False
        gs["end_reason"] = "警備員に発見され、取り押さえられた。"
        result["message"] = "💂 警備員に見つかった！万事休す。"
        result["event"] = "caught"
    else:
        gain = ALERT_GAIN[hazard_type]
        gs["alert"] = min(ALERT_MAX, gs["alert"] + gain)
        result["message"] = f"{CELL_ICON[hazard_type]} {HAZARD_LABELS[hazard_type]}に触れた！警戒度+{gain}"
        result["event"] = "alert"


def try_move(gs: dict[str, Any], direction: str) -> dict[str, Any]:
    """direction ("up"/"down"/"left"/"right") へ1マス移動を試みる。

    gs を直接更新し、今回の移動結果を表す dict を返す。
    """
    if gs["phase"] != "playing":
        return {"ok": False, "message": "ゲームは進行中ではない。", "event": "noop"}
    if direction not in DIRECTIONS:
        return {"ok": False, "message": "不明な方向。", "event": "noop"}

    dr, dc = DIRECTIONS[direction]
    r, c = gs["pos"]
    nr, nc = r + dr, c + dc
    size = gs["map"]["size"]

    if not (0 <= nr < size and 0 <= nc < size):
        return {"ok": False, "message": "その先は壁だ。進めない。", "event": "blocked"}

    gs["pos"] = (nr, nc)
    gs["turns_used"] += 1
    _reveal_around(gs, (nr, nc))

    result: dict[str, Any] = {"ok": True, "message": "", "event": "move"}
    cell = gs["map"]["grid"][nr][nc]

    if cell == CELL_GEM:
        gs["has_gem"] = True
        gs["map"]["grid"][nr][nc] = CELL_EMPTY
        result["message"] = "💎 宝石を手に入れた！"
        result["event"] = "gem"
    elif cell == CELL_EXIT:
        if gs["has_gem"]:
            gs["phase"] = "ended"
            gs["win"] = True
            gs["end_reason"] = "宝石を持って脱出に成功した！"
            result["message"] = "🚪 脱出成功！"
            result["event"] = "win"
        else:
            result["message"] = "🚪 出口だが、まだ宝石を手にしていない。"
    elif cell in (CELL_LASER, CELL_CAMERA, CELL_GUARD):
        _resolve_hazard(gs, nr, nc, cell, result)
    else:
        result["message"] = "静かな通路を進んだ。"

    if result["message"]:
        gs["log"].append(result["message"])
        gs["log"] = gs["log"][-6:]

    if gs["phase"] == "playing":
        if gs["alert"] >= ALERT_MAX:
            gs["phase"] = "ended"
            gs["win"] = False
            gs["end_reason"] = "警戒度が限界に達し、警備員に包囲された。"
        elif gs["turns_used"] >= gs.get("max_turns", MAX_TURNS):
            gs["phase"] = "ended"
            gs["win"] = False
            gs["end_reason"] = "閉館時刻になり、脱出できなかった。"

    return result


# ---------------------------------------------------------------------------
# UI 描画
# ---------------------------------------------------------------------------


def _render_trait_select(gs: dict[str, Any]) -> None:
    current = gs.get("difficulty", DEFAULT_DIFFICULTY)

    st.subheader("① 潜入する館を選ぶ")
    chosen = st.radio(
        "難易度",
        options=DIFFICULTY_ORDER,
        index=DIFFICULTY_ORDER.index(current),
        format_func=lambda k: f"{DIFFICULTIES[k]['emoji']} {DIFFICULTIES[k]['label']}",
        horizontal=True,
        key="mus_difficulty",
    )
    if chosen != current:
        set_difficulty(gs, chosen)
        st.rerun()

    diff = DIFFICULTIES[current]
    st.caption(diff["desc"])
    ui.metric_row(
        [
            ("広さ", f"{diff['size']}×{diff['size']}"),
            ("持ち時間", f"{diff['turns']}手"),
            ("特性の使用回数", f"{diff['charges']}回"),
            ("道具の総数", sum(diff["items"].values())),
        ]
    )

    seed_col, _ = st.columns([1, 2])
    with seed_col:
        if st.button("🎲 別の館にする", key="mus_reroll", use_container_width=True):
            reroll_map(gs)
            st.rerun()
    st.caption(f"館の見取り図: #{gs['rng_seed']}　同じ番号なら同じ間取りになる。")

    st.divider()
    st.subheader("② 特性を1つ選ぶ（選ぶと潜入開始）")

    cols = st.columns(4)
    for col, key in zip(cols, TRAIT_ORDER):
        info = TRAITS[key]
        with col:
            with st.container(border=True):
                st.markdown(f"### {info['emoji']} {info['label']}")
                st.write(info["desc"])
                st.caption(f"この難易度では {diff['charges']} 回まで使える。")
                if st.button("この特性で潜入する", key=f"mus_trait_{key}", use_container_width=True):
                    select_trait(gs, key)
                    st.rerun()


def _render_grid(gs: dict[str, Any]) -> None:
    gmap = gs["map"]
    size = gmap["size"]
    grid = gmap["grid"]
    pos = tuple(gs["pos"])
    revealed = gs["revealed"]
    revealed_all = gs["revealed_all"]

    for r in range(size):
        cols = st.columns(size, gap="small")
        for c in range(size):
            with cols[c]:
                if (r, c) == pos:
                    symbol = PLAYER_ICON
                elif revealed_all or (r, c) in revealed:
                    symbol = CELL_ICON[grid[r][c]]
                else:
                    symbol = FOG_ICON
                st.markdown(
                    "<div style='text-align:center;font-size:26px;line-height:1.4;"
                    "border:1px solid rgba(128,128,128,0.25);border-radius:6px;"
                    f"padding:2px 0;'>{symbol}</div>",
                    unsafe_allow_html=True,
                )


def _render_playing(gs: dict[str, Any]) -> None:
    trait_info = TRAITS[gs["trait"]]
    diff = DIFFICULTIES[gs.get("difficulty", DEFAULT_DIFFICULTY)]
    max_turns = gs.get("max_turns", MAX_TURNS)

    ui.metric_row(
        [
            ("難易度", f"{diff['emoji']} {diff['label']}"),
            ("警戒度", f"{gs['alert']}% / {ALERT_MAX}%"),
            ("ターン", f"{gs['turns_used']}/{max_turns}"),
            ("宝石", "💎所持" if gs["has_gem"] else "未回収"),
            (f"{trait_info['emoji']} {trait_info['label']} 残り", gs["trait_charges"]),
        ]
    )
    st.progress(min(1.0, gs["alert"] / ALERT_MAX), text="警戒度メーター（100%で包囲される）")

    st.write("")
    _render_grid(gs)
    st.caption(f"{PLAYER_ICON} あなた　・通路　🔴レーザー　📷カメラ　💂警備員　💎宝石　🚪出口　{FOG_ICON}未偵察")

    st.write("#### 移動")
    st.caption("⌨️ キーボードの十字キー（← ↑ ↓ →）でも移動できます。")

    move_cols = st.columns([1, 1, 1])
    with move_cols[1]:
        if st.button("⬆️", key="mus_move_up", use_container_width=True):
            try_move(gs, "up")
            st.rerun()
    mid = st.columns([1, 1, 1])
    with mid[0]:
        if st.button("⬅️", key="mus_move_left", use_container_width=True):
            try_move(gs, "left")
            st.rerun()
    with mid[2]:
        if st.button("➡️", key="mus_move_right", use_container_width=True):
            try_move(gs, "right")
            st.rerun()
    bottom = st.columns([1, 1, 1])
    with bottom[1]:
        if st.button("⬇️", key="mus_move_down", use_container_width=True):
            try_move(gs, "down")
            st.rerun()

    # 十字キーを上の移動ボタンに割り当てる（ボタンを押すのと同じ経路を通る）。
    ui.bind_keys(
        {
            "ArrowUp": "mus_move_up",
            "ArrowDown": "mus_move_down",
            "ArrowLeft": "mus_move_left",
            "ArrowRight": "mus_move_right",
        }
    )

    st.write("#### 所持アイテム")
    item_cols = st.columns(4)
    for col, key in zip(item_cols, ITEM_ORDER):
        info = ITEMS[key]
        with col:
            st.metric(f"{info['emoji']} {info['label']}", gs["items"][key])
    if st.button(
        "🛸 ドローン偵察を使う（マップ全体を可視化）",
        key="mus_use_drone",
        use_container_width=True,
        disabled=gs["items"]["drone"] <= 0 or gs["revealed_all"],
    ):
        use_drone(gs)
        st.rerun()

    if gs["log"]:
        st.write("#### 状況ログ")
        for msg in reversed(gs["log"]):
            st.caption(msg)


def _render_ended(gs: dict[str, Any]) -> None:
    win = bool(gs["win"])
    ui.result_banner(
        win,
        win_msg=gs.get("end_reason") or "宝石を持って脱出に成功した！",
        lose_msg=gs.get("end_reason") or "作戦は失敗に終わった。",
    )
    ui.metric_row(
        [
            ("結果", "成功" if win else "失敗"),
            ("使用ターン", f"{gs['turns_used']}/{MAX_TURNS}"),
            ("最終警戒度", f"{gs['alert']}%"),
            ("宝石", "回収済み" if gs["has_gem"] else "未回収"),
        ]
    )
    if st.button("🔁 もう一度挑戦する", key="mus_play_again", use_container_width=True):
        state.reset_game(NAME)
        st.rerun()


def render() -> None:
    ui.game_header("💎 博物館潜入", NAME, how_to_play=HOW_TO_PLAY)
    gs = state.game_state(NAME, _default_state)

    phase = gs["phase"]
    if phase == "trait_select":
        _render_trait_select(gs)
    elif phase == "playing":
        _render_playing(gs)
    elif phase == "ended":
        _render_ended(gs)
    else:
        st.error("不明な状態です。リセットしてください。")
