"""アプリ全体の session_state 管理とルーティングのヘルパー。

各ゲームは独立して完結する（ゲーム間でデータ共有しない）という方針に従い、
ゲームごとの状態は `game_state(name)` が返す専用の dict に閉じ込める。
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

# ルーティングで使用するページ識別子
HOME = "home"
IMMIGRATION = "immigration"
BLACK_MARKET = "black_market"
MUSEUM = "museum"
CAT_CAFE = "cat_cafe"

# ホーム画面に表示するゲーム一覧（識別子, タイトル, 一言説明, 絵文字）
GAMES = [
    (IMMIGRATION, "AI入国審査官", "限られた情報からAIか人間かを見抜く推理ゲーム。", "🛂"),
    (BLACK_MARKET, "ブラックマーケット", "AIとの心理戦で商品を競り落とし利益を最大化。", "💰"),
    (MUSEUM, "博物館潜入", "特性とルートを活かして宝石を盗み脱出する。", "💎"),
    (CAT_CAFE, "癒し猫カフェ", "10営業日で高評価を目指す猫カフェ経営。", "🐈"),
]

GAME_TITLES = {gid: title for gid, title, _desc, _icon in GAMES}
GAME_META = {gid: (title, desc, icon) for gid, title, desc, icon in GAMES}

# 内部用の session_state キー
_SCROLL_KEY = "_scroll_to_top"
_STARTED_KEY = "_started_games"
_DIFFICULTY_KEY = "_difficulties"
_MEMO_KEY = "_memos"

# 難易度の段階。4ゲーム共通の目盛りにしておき、各ゲームは
# モジュール定数 DIFFICULTIES に、この4キーぶんの「呼び名」と「効き方」を書く。
# 呼び名はゲームごとに変えてよい（審査官の「新人」と怪盗の「見習い」は別物）。
LEVELS = ["easy", "normal", "hard", "expert"]
DEFAULT_LEVEL = "normal"


def init_state() -> None:
    """アプリ起動時に一度だけ呼ぶ。共通の session_state を初期化する。"""
    if "current_page" not in st.session_state:
        st.session_state.current_page = HOME


def go_to(page: str) -> None:
    """指定ページへ遷移して再描画する。

    遷移後は必ずページ最上部から読み始められるよう、スクロール要求を立てる。
    実際のスクロールは app.py が描画の最後に `ui.scroll_to_top()` で行う。
    """
    st.session_state.current_page = page
    st.session_state[_SCROLL_KEY] = True
    st.rerun()


def go_home() -> None:
    go_to(HOME)


def game_state(name: str, default_factory: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """ゲーム専用の状態 dict を返す。無ければ default_factory で初期化する。

    ゲーム側はこの dict にキーを保存することで、他ゲームと衝突しない
    名前空間を得られる。例::

        s = game_state("immigration", lambda: {"score": 0})
        s["score"] += 1
    """
    key = f"gs_{name}"
    if key not in st.session_state:
        st.session_state[key] = default_factory() if default_factory else {}
    return st.session_state[key]


def reset_game(name: str) -> None:
    """指定ゲームの状態を破棄する。次回 game_state() で再初期化される。

    「最初からやり直す」ため、開始済みフラグも下ろして遊び方画面まで戻す。
    """
    key = f"gs_{name}"
    if key in st.session_state:
        del st.session_state[key]
    started_games().discard(name)


# ---------------------------------------------------------------------------
# 開始済み管理: ゲームを選ぶとまず遊び方が出て、スタートを押すと本編に入る。
#
# 開始フラグはゲーム専用の状態 dict とは別に持つ。game_state() の初期化は
# 各ゲームが自前の default_factory で行うため、ここで先に dict を作ってしまうと
# ゲーム側の初期値が入らなくなるため。
# ---------------------------------------------------------------------------

def started_games() -> set[str]:
    if _STARTED_KEY not in st.session_state:
        st.session_state[_STARTED_KEY] = set()
    return st.session_state[_STARTED_KEY]


def is_started(name: str) -> bool:
    """そのゲームが「スタート」済みか（＝遊び方画面を抜けたか）。"""
    return name in started_games()


def mark_started(name: str) -> None:
    """遊び方画面のスタートボタンから呼ぶ。"""
    started_games().add(name)


# ---------------------------------------------------------------------------
# 難易度: 遊び方画面で選び、ゲーム本体は開始時に読むだけ。
#
# ゲーム専用の状態 dict とは別に持つ。リセットしても選んだ難易度は残るので、
# 同じ難易度で何度も挑戦するときに選び直さずに済む。
# ---------------------------------------------------------------------------

def difficulty(name: str) -> str:
    """そのゲームで選ばれている難易度キー。未選択なら DEFAULT_LEVEL。"""
    return st.session_state.get(_DIFFICULTY_KEY, {}).get(name, DEFAULT_LEVEL)


def set_difficulty(name: str, level: str) -> None:
    if level not in LEVELS:
        return
    if _DIFFICULTY_KEY not in st.session_state:
        st.session_state[_DIFFICULTY_KEY] = {}
    st.session_state[_DIFFICULTY_KEY][name] = level


# ---------------------------------------------------------------------------
# ゲーム内自己ベスト: reset を跨いで残る per-game の記録。
#
# 仕様書の「ゲーム間データ共有なし」を守るため、ここに保存する値は
#   - そのゲーム専用の名前空間 memo_<name> に閉じる
#   - 他ゲームから読み合う経路を作らない
#   - ホーム画面など横断的な場所には出さない（各ゲーム内でのみ表示する）
# ことを前提とする。難易度別に「大きいほど良い」数値1つで自己ベストを持つ。
# ---------------------------------------------------------------------------

def game_memo(name: str) -> dict[str, Any]:
    """reset を跨いで残る、そのゲーム専用の記憶 dict。他ゲームからは触らない。"""
    memos = st.session_state.setdefault(_MEMO_KEY, {})
    return memos.setdefault(name, {})


def get_best(name: str, level: str) -> dict[str, Any] | None:
    """その難易度の自己ベスト {"value": float, "label": str} または None。"""
    return game_memo(name).get("best", {}).get(level)


def record_best(name: str, level: str, value: float, label: str) -> bool:
    """自己ベストを更新する。更新したら True を返す。

    value は「大きいほど良い」比較値（正解数・利益・スコア・ランク点など）。
    label は表示用の文字列（"3 / 5 人" や "¥450" など、ゲームが決める）。
    """
    bests = game_memo(name).setdefault("best", {})
    cur = bests.get(level)
    if cur is None or value > cur["value"]:
        bests[level] = {"value": float(value), "label": label}
        return True
    return False


# ---------------------------------------------------------------------------
# スクロール要求
# ---------------------------------------------------------------------------

def take_scroll_request() -> bool:
    """スクロール要求が立っていれば True を返し、同時に下ろす（1回限り）。"""
    if st.session_state.get(_SCROLL_KEY):
        st.session_state[_SCROLL_KEY] = False
        return True
    return False


def request_scroll_top() -> None:
    """次の描画でページ最上部へスクロールさせる。"""
    st.session_state[_SCROLL_KEY] = True
