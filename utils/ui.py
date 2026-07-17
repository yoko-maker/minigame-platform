"""ゲーム共通の UI 部品。

各ゲームは冒頭で `game_header(...)` を呼ぶことで、タイトル・戻る・リセットの
共通ヘッダを表示する。個別ゲームの見た目はこのヘッダ以下に自由に構築してよい。
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from . import state


def game_header(title: str, game_name: str, how_to_play: str | None = None) -> None:
    """ゲーム画面上部の共通ヘッダ。

    Args:
        title: 画面タイトル。
        game_name: `state.game_state` / `state.reset_game` で使うゲーム識別子。
        how_to_play: 指定するとタイトル下に「遊び方」の expander を表示する。
    """
    left, mid, right = st.columns([6, 1.5, 1.5])
    with left:
        st.title(title)
    with mid:
        st.write("")
        if st.button("🏠 戻る", key=f"back_{game_name}", use_container_width=True):
            state.go_home()
    with right:
        st.write("")
        if st.button("🔄 リセット", key=f"reset_{game_name}", use_container_width=True):
            state.reset_game(game_name)
            st.rerun()

    if how_to_play:
        with st.expander("❓ 遊び方"):
            st.markdown(how_to_play)

    st.divider()


def metric_row(items: list[tuple[str, object]]) -> None:
    """(ラベル, 値) のリストを横並びの metric として表示する簡易ヘルパー。"""
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)


def result_banner(win: bool, win_msg: str, lose_msg: str) -> None:
    """勝敗結果を目立つバナーで表示する。"""
    if win:
        st.success(f"🎉 {win_msg}")
        st.balloons()
    else:
        st.error(f"💀 {lose_msg}")
