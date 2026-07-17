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


def init_state() -> None:
    """アプリ起動時に一度だけ呼ぶ。共通の session_state を初期化する。"""
    if "current_page" not in st.session_state:
        st.session_state.current_page = HOME


def go_to(page: str) -> None:
    """指定ページへ遷移して再描画する。"""
    st.session_state.current_page = page
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
    """指定ゲームの状態を破棄する。次回 game_state() で再初期化される。"""
    key = f"gs_{name}"
    if key in st.session_state:
        del st.session_state[key]
