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
    "easy": {
        "label": "見習い",
        "emoji": "🔰",
        "size": 6,
        "turns": 40,
        "density": 0.20,
        "charges": 4,
        "patrols": 0,
        "item_count": 3,
        "loot": 1,
        "desc": "6×6の小さな館。警備員は持ち場を動かず、道具にも余裕がある。まずはここから。",
    },
    "normal": {
        "label": "一人前",
        "emoji": "🎯",
        "size": 7,
        "turns": 32,
        "density": 0.32,
        "charges": 3,
        "patrols": 1,
        "item_count": 2,
        "loot": 2,
        "desc": "7×7。巡回する警備員が1人。持ち場を離れて動き回る。",
    },
    "hard": {
        "label": "怪盗",
        "emoji": "🔥",
        "size": 8,
        "turns": 34,
        "density": 0.42,
        "charges": 2,
        "patrols": 2,
        "item_count": 2,
        "loot": 2,
        "desc": "8×8。巡回2人。警備が厚く道具も乏しい。通る道をよく選ぶこと。",
    },
    "expert": {
        "label": "伝説",
        "emoji": "💀",
        "size": 9,
        "turns": 36,
        "density": 0.46,
        "charges": 2,
        "patrols": 3,
        "item_count": 1,
        "loot": 3,
        "desc": "9×9。巡回3人。持ち込める道具は1つだけ。館の記憶と勘だけが頼り。",
    },
}
DEFAULT_DIFFICULTY = state.DEFAULT_LEVEL

CELL_EMPTY = "empty"
CELL_LASER = "laser"
CELL_CAMERA = "camera"
CELL_GUARD = "guard"
CELL_GEM = "gem"
CELL_EXIT = "exit"
CELL_LOOT = "loot"

CELL_ICON = {
    CELL_EMPTY: "・",
    CELL_LASER: "🔴",
    CELL_CAMERA: "📷",
    CELL_GUARD: "💂",
    CELL_GEM: "💎",
    CELL_EXIT: "🚪",
    CELL_LOOT: "🏺",
}

# 怪盗ランクの判定に使う点数配分・しきい値。
# 全回収・低警戒・残ターン多めなら S、ぎりぎり成功なら C になるよう重み付けしている。
RANK_LOOT_WEIGHT = 50
RANK_TURNS_WEIGHT = 30
RANK_ALERT_WEIGHT = 20
RANK_THRESHOLDS = [("S", 90), ("A", 70), ("B", 50)]
RANK_SCORE = {"S": 4, "A": 3, "B": 2, "C": 1}
PLAYER_ICON = "🥷"
FOG_ICON = "❔"
PATROL_ICON = "👮"

# 巡回する警備員。持ち場に立つ 💂 と違い、毎ターン1歩動く。
PATROL_DETECT = 1        # この距離まで近づかれると、こちらへ寄ってくる（隣接のみ）
PATROL_LOSE_CHANCE = 0.4  # 追跡中でもこの確率で見失い、その手番は巡回に戻る
PATROL_LURE_PUSH = 3      # 幻術師の幻影で引き離せる距離

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
        "desc": "幻影で気を引き、巡回中の警備員をまとめて遠ざける。鉢合わせもやり過ごせる。",
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
ITEM_FOR_HAZARD = {CELL_LASER: "mirror", CELL_CAMERA: "emp", CELL_GUARD: "smoke"}
DEFAULT_ITEM = "emp"


def empty_items() -> dict[str, int]:
    """全アイテム0個の所持表。潜入時に選んだ1種類だけ個数が入る。"""
    return {k: 0 for k in ITEM_ORDER}


def loadout(item_key: str, count: int) -> dict[str, int]:
    """選んだ1種類だけ count 個、他は0個の所持表を返す。"""
    items = empty_items()
    if item_key in items:
        items[item_key] = count
    return items

HOW_TO_PLAY = """
**目的**: 博物館に忍び込み、💎宝石を手に入れて🚪出口から脱出しよう。

**進め方**
1. 潜入する館（難易度）を選ぶ。広さ・警備の厚さ・持ち時間・道具の数が変わる。
   「🎲 別の館にする」で同じ難易度のまま別の間取りを引き直せる。
2. **持ち込むアイテムを1種類だけ選ぶ**（EMP/煙幕/鏡/ドローンから1つ）。
   個数は難易度による。何を持って行くかが攻略の要になる。
3. 得意な特性を1つ選ぶ（対応する障害物を数回まで無効化できる）。
4. **キーボードの十字キー（← ↑ ↓ →）** か ⬆️⬇️⬅️➡️ ボタンで移動する
   （1手ごとにターンを消費）。
5. 💎宝石のマスに入ると自動的に回収する。
6. 宝石を持って🚪出口のマスに入れば脱出成功（勝利）。

**障害物**
- 🔴レーザー・📷監視カメラに無防備で踏み込むと警戒度が上昇する。警戒度が100%になると失敗。
- 💂警備員（動かない）に無防備で踏み込むと、その場で見つかり即座に失敗になる。
- 👮**警備員（巡回中）はあなたが1歩動くたびに1歩動きます。** 真隣に来たときだけ
  こちらへ寄ってきますが、追跡は完璧ではなく時々見失います。鉢合わせると即失敗。
  難易度が上がるほど人数が増えます。1マス空けて通り過ぎる、隙を見て振り切る——
  通り抜ける順番が勝負を分けます。

**特性（開始時に1つ選択。対応する障害物を無効化できる。回数は難易度による）**
- 💻ハッカー: 監視カメラを無効化
- 🔧エンジニア: レーザーを停止
- 🥷怪盗: 足音を消して警備員をやり過ごす
- 🎭幻術師: 鉢合わせをやり過ごせるうえ、**ボタンで近くの巡回をまとめて遠ざけられる**
  （巡回のいない館では出番がありません）

**アイテムは1種類だけ持ち込めます（潜入前に選択）**
- ⚡EMP: カメラを一時停止 / 💨煙幕: 警備員をやり過ごす / 🪞鏡: レーザーを反射
  （対応する障害物に無防備で入ったとき自動で消費される）
- 🛸ドローン: ボタンで手動使用。使うとマップ全体が見えるようになる
  （持ち込まなければ、歩いた周辺しか見えない）。

**その他**
- ターン数か警戒度が上限に達すると脱出失敗。
- 🏺美術品は寄り道して回収できる任意の獲物（取らなくても脱出はできる）。
  脱出成功時、回収数・残ターン・最終警戒度から怪盗ランク（S/A/B/C）が付く。
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
    rng: random.Random,
    size: int,
    hazard_density: float = HAZARD_DENSITY,
    loot_count: int = 0,
) -> dict[str, Any]:
    """ランダムなマップを1つ生成して返す。

    Args:
        size: 盤面の一辺のマス数。
        hazard_density: 1マスが障害物になる確率。難易度で変わる。
        loot_count: 任意回収の美術品（🏺）の個数。難易度の "loot" キーで決まる。

    戻り値: {"size", "grid", "start", "gem", "exit", "loot"}
    grid[r][c] は CELL_* のいずれか。start/gem/exit/loot はいずれも到達可能
    （このゲームのマップは移動を遮る壁を持たない全結線グリッドのため）。
    "loot" は美術品を置いたマス座標のリスト（要求数に届かない場合は入るだけ）。
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

        # 障害物配置の後、残った空きマスへ任意回収の美術品を置く。
        # reserved（start/exit/gem）の外・start から距離2以上・空きマスのみが対象。
        loot_candidates = [
            (r, c)
            for r in range(size)
            for c in range(size)
            if grid[r][c] == CELL_EMPTY
            and (r, c) not in reserved
            and _manhattan((r, c), start) >= 2
        ]
        rng.shuffle(loot_candidates)
        loot_positions = loot_candidates[: max(0, loot_count)]
        for lr, lc in loot_positions:
            grid[lr][lc] = CELL_LOOT

        reachable = reachable_cells(size, start)
        if gem in reachable and exit_pos in reachable:
            return {
                "size": size,
                "grid": grid,
                "start": start,
                "gem": gem,
                "exit": exit_pos,
                "loot": loot_positions,
            }

    # 実質的に到達しないフォールバック（全結線グリッドのため理論上不要）。
    raise RuntimeError("到達可能なマップの生成に失敗しました。")


def spawn_patrols(
    gmap: dict[str, Any], count: int, rng: random.Random
) -> list[dict[str, Any]]:
    """巡回する警備員を配置する。

    開始地点のすぐそばには置かない（開幕即詰みを避ける）。宝石・出口の上にも
    置かない（そこに立たれると回収できない時間が生まれてしまう）。
    """
    if count <= 0:
        return []
    size = gmap["size"]
    forbidden = {gmap["start"], gmap["gem"], gmap["exit"]}
    cells = [
        (r, c)
        for r in range(size)
        for c in range(size)
        if (r, c) not in forbidden and _manhattan((r, c), gmap["start"]) >= 3
    ]
    rng.shuffle(cells)
    return [
        {"pos": pos, "dir": rng.choice(list(DIRECTIONS))}
        for pos in cells[:count]
    ]


def _step_toward(src: tuple[int, int], dst: tuple[int, int]) -> tuple[int, int]:
    """src から dst へ1歩ぶんの差分。距離が縮む向きを1つ選ぶ。"""
    dr = dst[0] - src[0]
    dc = dst[1] - src[1]
    if abs(dr) >= abs(dc) and dr != 0:
        return (1 if dr > 0 else -1, 0)
    if dc != 0:
        return (0, 1 if dc > 0 else -1)
    return (0, 0)


def _in_bounds(pos: tuple[int, int], size: int) -> bool:
    return 0 <= pos[0] < size and 0 <= pos[1] < size


def move_patrols(
    gs: dict[str, Any], rng: random.Random
) -> list[tuple[int, int]]:
    """警備員を1歩ずつ動かし、動いた後の位置を返す。

    プレイヤーが PATROL_DETECT 以内（既定は隣接のみ）にいれば寄ってくる。ただし
    追跡中でも PATROL_LOSE_CHANCE の確率で見失い、その手番は巡回に戻る。完全な
    追跡だと同速では振り切れず理不尽になるため、あえて隙を作っている。
    検知圏の外では今の向きへ進み、壁に当たったら向きを変える。
    """
    if not gs.get("patrols"):
        return []
    size = gs["map"]["size"]
    player = tuple(gs["pos"])

    for p in gs["patrols"]:
        pos = tuple(p["pos"])
        chasing = _manhattan(pos, player) <= PATROL_DETECT and rng.random() >= PATROL_LOSE_CHANCE
        if chasing:
            step = _step_toward(pos, player)
        else:
            step = DIRECTIONS[p["dir"]]
            if not _in_bounds((pos[0] + step[0], pos[1] + step[1]), size):
                # 壁。別の進める向きへ切り替える。
                options = [
                    d for d, (dr, dc) in DIRECTIONS.items()
                    if _in_bounds((pos[0] + dr, pos[1] + dc), size)
                ]
                if options:
                    p["dir"] = rng.choice(options)
                    step = DIRECTIONS[p["dir"]]
                else:
                    step = (0, 0)

        nxt = (pos[0] + step[0], pos[1] + step[1])
        if _in_bounds(nxt, size):
            p["pos"] = nxt

    return [tuple(p["pos"]) for p in gs["patrols"]]


def patrol_on_player(gs: dict[str, Any]) -> bool:
    """警備員とプレイヤーが同じマスにいるか。"""
    player = tuple(gs["pos"])
    return any(tuple(p["pos"]) == player for p in gs.get("patrols", []))


def lure_patrols(gs: dict[str, Any]) -> dict[str, Any]:
    """幻術師の能力。幻影を出して近くの警備員を引き離す。

    持ち場の警備員（💂）をすり抜ける怪盗に対し、幻術師は「動く警備員を
    遠ざける」ことができる。巡回がいて初めて意味を持つ能力。
    """
    if gs["phase"] != "playing":
        return {"ok": False, "message": "今は使用できない。"}
    if gs.get("trait") != "illusionist":
        return {"ok": False, "message": "幻術師にしか使えない。"}
    if gs["trait_charges"] <= 0:
        return {"ok": False, "message": "幻影を出す力が残っていない。"}

    player = tuple(gs["pos"])
    size = gs["map"]["size"]
    near = [p for p in gs.get("patrols", []) if _manhattan(tuple(p["pos"]), player) <= PATROL_LURE_PUSH]
    if not near:
        return {"ok": False, "message": "近くに引きつけられる警備員がいない。"}

    gs["trait_charges"] -= 1
    for p in near:
        pos = tuple(p["pos"])
        away = _step_toward(player, pos)  # プレイヤーから見て警備員のいる向き＝遠ざける向き
        for _ in range(PATROL_LURE_PUSH):
            nxt = (pos[0] + away[0], pos[1] + away[1])
            if not _in_bounds(nxt, size):
                break
            pos = nxt
        p["pos"] = pos

    msg = f"🎭 幻影で警備員 {len(near)} 人の気を引き、遠ざけた！"
    gs["log"].append(msg)
    gs["log"] = gs["log"][-6:]
    return {"ok": True, "message": msg}


def mission_rank(
    turns_used: int, max_turns: int, alert: int, loot_taken: int, loot_total: int
) -> str:
    """脱出成功時の「うまさ」を S/A/B/C で表す。

    回収率・残ターンの余裕・最終警戒度の低さを加点し、しきい値で段階分けする。
    美術品が無い館（loot_total == 0）では回収率を満点扱いにする（無いものは
    採点対象にしない）。脱出成功時のみ意味を持つ加点表現であり、失敗時の
    呼び出しは想定していない（呼び出し側は win 判定後にのみ呼ぶこと）。
    """
    loot_ratio = (loot_taken / loot_total) if loot_total > 0 else 1.0
    turns_left_ratio = max(0.0, (max_turns - turns_used) / max_turns) if max_turns > 0 else 0.0
    alert_ratio = max(0.0, min(1.0, 1 - (alert / ALERT_MAX)))

    score = (
        RANK_LOOT_WEIGHT * loot_ratio
        + RANK_TURNS_WEIGHT * turns_left_ratio
        + RANK_ALERT_WEIGHT * alert_ratio
    )
    for label, threshold in RANK_THRESHOLDS:
        if score >= threshold:
            return label
    return "C"


def _default_state() -> dict[str, Any]:
    return {
        "phase": "trait_select",  # trait_select -> playing -> ended
        "difficulty": DEFAULT_DIFFICULTY,
        "patrols": [],
        "trait": None,
        "trait_charges": 0,
        "chosen_item": DEFAULT_ITEM,
        "items": empty_items(),
        "max_turns": MAX_TURNS,
        "rng_seed": random.randrange(1_000_000_000),
        "map": None,
        "pos": None,
        "has_gem": False,
        "alert": 0,
        "turns_used": 0,
        "loot_taken": 0,
        "loot_total": 0,
        "revealed": set(),
        "revealed_all": False,
        "log": [],
        "win": None,
        "end_reason": "",
        "best_recorded": False,
    }


def _reveal_around(gs: dict[str, Any], pos: tuple[int, int]) -> None:
    r, c = pos
    size = gs["map"]["size"]
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            nr, nc = r + dr, c + dc
            if 0 <= nr < size and 0 <= nc < size:
                gs["revealed"].add((nr, nc))


def reroll_map(gs: dict[str, Any]) -> None:
    """同じ難易度のまま、別の館（別のマップ）を引き直す。"""
    if gs["phase"] == "trait_select":
        gs["rng_seed"] = random.randrange(1_000_000_000)


def select_trait(
    gs: dict[str, Any],
    trait_key: str,
    difficulty_key: str | None = None,
    item_key: str | None = None,
) -> None:
    """特性を確定し、選んだ難易度でマップを生成してプレイ開始状態にする。

    difficulty_key は遊び方画面で選ばれた難易度。省略時は state に入っている値。
    item_key は持ち込むアイテム1種類。省略時は gs["chosen_item"]（既定 EMP）。
    """
    if gs["phase"] != "trait_select" or trait_key not in TRAITS:
        return
    if difficulty_key in DIFFICULTIES:
        gs["difficulty"] = difficulty_key
    diff = DIFFICULTIES[gs.get("difficulty", DEFAULT_DIFFICULTY)]

    chosen = item_key if item_key in ITEM_ORDER else gs.get("chosen_item", DEFAULT_ITEM)
    gs["trait"] = trait_key
    gs["trait_charges"] = diff["charges"]
    gs["chosen_item"] = chosen
    gs["items"] = loadout(chosen, diff["item_count"])
    gs["max_turns"] = diff["turns"]

    rng = random.Random(gs["rng_seed"])
    gmap = generate_map(rng, diff["size"], diff["density"], diff.get("loot", 0))
    gs["map"] = gmap
    gs["patrols"] = spawn_patrols(gmap, diff["patrols"], rng)
    gs["pos"] = gmap["start"]
    gs["has_gem"] = False
    gs["alert"] = 0
    gs["turns_used"] = 0
    gs["loot_taken"] = 0
    gs["loot_total"] = len(gmap.get("loot", []))
    gs["revealed"] = set()
    gs["revealed_all"] = False
    gs["log"] = ["潜入開始。物音を立てないように進もう。"]
    gs["phase"] = "playing"
    gs["best_recorded"] = False
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


def _remove_patrol_at(gs: dict[str, Any], pos: tuple[int, int]) -> None:
    """やり過ごした警備員をその場から外す（追い払った扱い）。"""
    gs["patrols"] = [p for p in gs["patrols"] if tuple(p["pos"]) != tuple(pos)]


def _resolve_patrol_contact(
    gs: dict[str, Any], result: dict[str, Any], approached: bool = False
) -> None:
    """巡回中の警備員と鉢合わせたときの解決。

    持ち場の警備員（💂）と同じく、まず特性、次に煙幕でやり過ごす。どちらも
    無ければ捕まる。approached=True は「向こうから寄ってきた」場合。
    """
    player = tuple(gs["pos"])

    if use_trait(gs, CELL_GUARD):
        _remove_patrol_at(gs, player)
        info = TRAITS[gs["trait"]]
        result["message"] = f"{info['emoji']} {info['label']}の能力で巡回中の警備員をやり過ごした！"
        result["event"] = "trait_used"
        return

    if use_item(gs, "smoke"):
        _remove_patrol_at(gs, player)
        info = ITEMS["smoke"]
        result["message"] = f"{info['emoji']} {info['label']}で巡回中の警備員の視界を遮った！"
        result["event"] = "item_used"
        return

    gs["phase"] = "ended"
    gs["win"] = False
    gs["end_reason"] = (
        "巡回中の警備員に見つかり、取り押さえられた。"
        if approached
        else "巡回中の警備員に正面から鉢合わせた。"
    )
    result["message"] = f"{PATROL_ICON} 巡回中の警備員に見つかった！"
    result["event"] = "caught"


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
    elif cell == CELL_LOOT:
        gs["loot_taken"] += 1
        gs["map"]["grid"][nr][nc] = CELL_EMPTY
        result["message"] = f"🏺 美術品を回収した！（{gs['loot_taken']}/{gs['loot_total']}）"
        result["event"] = "loot"
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

    # 踏み込んだ先に巡回中の警備員がいた場合。
    if gs["phase"] == "playing" and patrol_on_player(gs):
        _resolve_patrol_contact(gs, result)

    # プレイヤーが動いたぶん、警備員も動く。
    if gs["phase"] == "playing" and gs.get("patrols"):
        rng = random.Random(gs["rng_seed"] * 7919 + gs["turns_used"])
        move_patrols(gs, rng)
        if patrol_on_player(gs):
            _resolve_patrol_contact(gs, result, approached=True)

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
    # 難易度は遊び方画面で選ばれている。ここではその内訳を見せるだけ。
    level = state.difficulty(NAME)
    if level not in DIFFICULTIES:
        level = DEFAULT_DIFFICULTY
    gs["difficulty"] = level
    diff = DIFFICULTIES[level]

    item_count = diff["item_count"]
    st.subheader(f"潜入する館：{diff['emoji']} {diff['label']}")
    st.caption(diff["desc"])
    ui.metric_row(
        [
            ("広さ", f"{diff['size']}×{diff['size']}"),
            ("持ち時間", f"{diff['turns']}手"),
            ("巡回する警備員", f"{diff['patrols']}人"),
            ("特性の使用回数", f"{diff['charges']}回"),
            ("道具の個数", f"{item_count}個"),
        ]
    )

    seed_col, _ = st.columns([1, 2])
    with seed_col:
        if st.button("🎲 別の館にする", key="mus_reroll", use_container_width=True):
            reroll_map(gs)
            st.rerun()
    st.caption(f"館の見取り図: #{gs['rng_seed']}　同じ番号なら同じ間取りになる。")

    st.divider()
    st.subheader("① 持ち込むアイテムを1種類選ぶ")
    st.caption(f"1種類だけ、{item_count}個 持ち込めます。障害物に無防備で入ったとき自動で使われます（ドローンは手動）。")
    chosen = st.radio(
        "アイテム",
        options=ITEM_ORDER,
        index=ITEM_ORDER.index(gs.get("chosen_item", DEFAULT_ITEM)),
        format_func=lambda k: f"{ITEMS[k]['emoji']} {ITEMS[k]['label']}",
        horizontal=True,
        label_visibility="collapsed",
        key="mus_item",
    )
    gs["chosen_item"] = chosen
    st.caption(f"　{ITEMS[chosen]['emoji']} {ITEMS[chosen]['label']}: {ITEMS[chosen]['desc']}")

    st.divider()
    st.subheader("② 特性を1つ選ぶ（選ぶと潜入開始）")

    cols = st.columns(4)
    for col, key in zip(cols, TRAIT_ORDER):
        info = TRAITS[key]
        with col:
            with st.container(border=True):
                st.markdown(f"### {info['emoji']} {info['label']}")
                st.write(info["desc"])
                st.caption(f"この館では {diff['charges']} 回まで使える。")
                if key == "illusionist" and diff["patrols"] == 0:
                    st.caption("⚠️ この館に巡回はいない。幻影を出す相手がいない。")
                if st.button("この特性で潜入する", key=f"mus_trait_{key}", use_container_width=True):
                    select_trait(gs, key, level, chosen)
                    st.rerun()


def _render_grid(gs: dict[str, Any]) -> None:
    gmap = gs["map"]
    size = gmap["size"]
    grid = gmap["grid"]
    pos = tuple(gs["pos"])
    revealed = gs["revealed"]
    revealed_all = gs["revealed_all"]
    patrol_at = {tuple(p["pos"]) for p in gs.get("patrols", [])}

    for r in range(size):
        cols = st.columns(size, gap="small")
        for c in range(size):
            with cols[c]:
                if (r, c) == pos:
                    symbol = PLAYER_ICON
                elif not (revealed_all or (r, c) in revealed):
                    symbol = FOG_ICON
                elif (r, c) in patrol_at:
                    # 巡回は持ち場の警備員より優先して見せる（動くので危険）
                    symbol = PATROL_ICON
                else:
                    symbol = CELL_ICON[grid[r][c]]
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
            ("🏺回収", f"{gs['loot_taken']}/{gs['loot_total']}"),
            ("巡回中", f"{len(gs.get('patrols', []))}人"),
            (f"{trait_info['emoji']} {trait_info['label']} 残り", gs["trait_charges"]),
        ]
    )
    st.progress(min(1.0, gs["alert"] / ALERT_MAX), text="警戒度メーター（100%で包囲される）")

    st.write("")
    _render_grid(gs)
    st.caption(
        f"{PLAYER_ICON} あなた　・通路　🔴レーザー　📷カメラ　💂警備員（動かない）　"
        f"{PATROL_ICON}警備員（巡回中・毎ターン動く）　💎宝石　🏺美術品（任意回収）　"
        f"🚪出口　{FOG_ICON}未偵察"
    )
    if gs.get("patrols"):
        st.caption(
            f"{PATROL_ICON} 巡回はあなたが動くたびに1歩動きます。"
            "真隣に来たときだけ寄ってきますが、時々見失います。1マス空ければ通り過ぎられます。"
        )

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

    # 幻術師だけが持つ能動的な能力。巡回がいて初めて意味を持つ。
    if gs["trait"] == "illusionist":
        near = [
            p for p in gs.get("patrols", [])
            if _manhattan(tuple(p["pos"]), tuple(gs["pos"])) <= PATROL_LURE_PUSH
        ]
        if st.button(
            f"🎭 幻影で警備員を誘導する（近くに {len(near)} 人 / 残り {gs['trait_charges']} 回）",
            key="mus_lure",
            use_container_width=True,
            disabled=not near or gs["trait_charges"] <= 0,
        ):
            lure_patrols(gs)
            st.rerun()

    st.write("#### 所持アイテム")
    carried = gs.get("chosen_item", DEFAULT_ITEM)
    info = ITEMS[carried]
    st.metric(f"{info['emoji']} {info['label']}", gs["items"].get(carried, 0))
    if carried == "drone":
        st.caption("🛸 ドローンは下のボタンで手動使用。")
        if st.button(
            "🛸 ドローン偵察を使う（マップ全体を可視化）",
            key="mus_use_drone",
            use_container_width=True,
            disabled=gs["items"].get("drone", 0) <= 0 or gs["revealed_all"],
        ):
            use_drone(gs)
            st.rerun()
    else:
        st.caption(f"{info['emoji']} {info['label']}は、対応する障害物に無防備で入ると自動で使われます。")

    if gs["log"]:
        st.write("#### 状況ログ")
        for msg in reversed(gs["log"]):
            st.caption(msg)


def _render_ended(gs: dict[str, Any]) -> None:
    win = bool(gs["win"])
    max_turns = gs.get("max_turns", MAX_TURNS)
    ui.result_banner(
        win,
        win_msg=gs.get("end_reason") or "宝石を持って脱出に成功した！",
        lose_msg=gs.get("end_reason") or "作戦は失敗に終わった。",
    )

    metrics = [
        ("結果", "成功" if win else "失敗"),
        ("使用ターン", f"{gs['turns_used']}/{max_turns}"),
        ("最終警戒度", f"{gs['alert']}%"),
        ("🏺回収した美術品", f"{gs['loot_taken']}/{gs['loot_total']}"),
        ("宝石", "回収済み" if gs["has_gem"] else "未回収"),
    ]

    rank = None
    if win:
        rank = mission_rank(gs["turns_used"], max_turns, gs["alert"], gs["loot_taken"], gs["loot_total"])
        metrics.append(("怪盗ランク", rank))
    ui.metric_row(metrics)

    if win and rank is not None:
        turns_left = max_turns - gs["turns_used"]
        value = RANK_SCORE[rank] * 100 + turns_left
        label = f"{rank}（回収{gs['loot_taken']}/{gs['loot_total']}・残{turns_left}手）"
        if not gs.get("best_recorded"):
            ui.record_and_show_best(NAME, gs["difficulty"], value, label)
            gs["best_recorded"] = True
        else:
            ui.personal_best_line(NAME, gs["difficulty"])
    else:
        ui.personal_best_line(NAME, gs["difficulty"])

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
