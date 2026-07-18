"""ミニゲーム統合プラットフォーム エントリポイント。

`py -3 -m streamlit run app.py` で起動する。ホーム画面から4種類のゲームを選んで遊ぶ。
Streamlit 標準のマルチページ機能（pages/ ディレクトリ）は使わず、
session_state による自前ルーティングでホーム/各ゲームを切り替える。

画面の流れ::

    ホーム ──[▶ プレイ]──> 遊び方 ──[▶ ゲームを始める]──> ゲーム本編
      ^                        │                            │
      └────────[🏠 戻る]───────┴──────[🏠 戻る]─────────────┘

遊び方画面は各ゲームの `HOW_TO_PLAY` を使って共通で組み立てるため、
ゲーム本体（games/*.py）は何も知らなくてよい。
"""

from __future__ import annotations

import streamlit as st

from utils import state, theme, ui
from games import immigration, black_market, museum, cat_cafe

# ページ識別子 -> ゲームモジュール（render() と HOW_TO_PLAY を持つ）
GAME_MODULES = {
    state.IMMIGRATION: immigration,
    state.BLACK_MARKET: black_market,
    state.MUSEUM: museum,
    state.CAT_CAFE: cat_cafe,
}

HOW_TO_PLAY = """
- 遊びたいゲームのカードにある **▶ プレイ** を押すと、まず遊び方が表示されます。
  内容を確認して **▶ ゲームを始める** を押すと本編が始まります。
- ゲーム画面の上部の **🏠 戻る** でここに戻れます。**🔄 リセット** でそのゲームを
  最初（遊び方画面）からやり直せます。
- 各ゲームは独立しており、通貨・レベル・実績の共有はありません。1回5〜10分で遊べます。
"""


def render_home() -> None:
    st.markdown(
        """
        <h1 class="hub-title">🎮 ミニゲーム統合プラットフォーム</h1>
        <div class="hub-rule"></div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("❓ 遊び方 / このアプリについて"):
        st.markdown(HOW_TO_PLAY)

    st.write("")

    cols = st.columns(2)
    for i, (gid, title, desc, icon) in enumerate(state.GAMES):
        accent = theme.token(gid, "accent")
        eyebrow = theme.token(gid, "eyebrow")
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(
                    f"""
                    <div style="height:3px;background:{accent};border-radius:2px;
                                margin:-.25rem 0 .9rem;box-shadow:0 0 14px {accent}88;"></div>
                    <div class="card-art">
                      <div class="card-icon">{icon}</div>
                      <div>
                        <div class="card-eyebrow" style="color:{accent}">{eyebrow}</div>
                        <div class="card-name">{title}</div>
                      </div>
                    </div>
                    <div class="card-desc">{desc}</div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("▶ プレイ", key=f"play_{gid}", use_container_width=True):
                    state.go_to(gid)


def main() -> None:
    st.set_page_config(
        page_title="ミニゲーム統合プラットフォーム",
        page_icon="🎮",
        layout="wide",
    )
    state.init_state()

    page = st.session_state.current_page
    if page not in GAME_MODULES:
        page = state.HOME

    # そのページの世界観を先に敷いてから中身を描く。
    theme.inject(page)

    if page == state.HOME:
        render_home()
    else:
        module = GAME_MODULES[page]
        if state.is_started(page):
            module.render()
        else:
            # 難易度を持つゲームなら、遊び方画面に選択欄が出る。
            ui.briefing(page, module.HOW_TO_PLAY, getattr(module, "DIFFICULTIES", None))

    # 遷移してきた直後なら、ページ最上部から読ませる。
    if state.take_scroll_request():
        ui.scroll_to_top()


if __name__ == "__main__":
    main()
