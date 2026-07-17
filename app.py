"""ミニゲーム統合プラットフォーム エントリポイント。

`streamlit run app.py` で起動する。ホーム画面から4種類のゲームを選択して遊ぶ。
Streamlit 標準のマルチページ機能（pages/ ディレクトリ）は使わず、
session_state による自前ルーティングでホーム/各ゲームを切り替える。
"""

from __future__ import annotations

import streamlit as st

from utils import state
from games import immigration, black_market, museum, cat_cafe

# ページ識別子 -> ゲームの render 関数
GAME_RENDERERS = {
    state.IMMIGRATION: immigration.render,
    state.BLACK_MARKET: black_market.render,
    state.MUSEUM: museum.render,
    state.CAT_CAFE: cat_cafe.render,
}

HOW_TO_PLAY = """
- 遊びたいゲームのカードにある **▶ プレイ** ボタンを押すと開始します。
- 各ゲーム画面の上部にある **🏠 戻る** でここに戻れます。
- **🔄 リセット** でそのゲームを最初からやり直せます。
- 各ゲームは独立しており、通貨・レベル・実績の共有はありません。1回5〜10分で遊べます。
"""


def render_home() -> None:
    st.title("🎮 ミニゲーム統合プラットフォーム")
    st.caption("4種類の短時間ゲームを1つのアプリで。好きなゲームを選んで遊ぼう。")

    with st.expander("❓ 遊び方 / このアプリについて"):
        st.markdown(HOW_TO_PLAY)

    st.divider()
    st.subheader("ゲーム一覧")

    cols = st.columns(2)
    for i, (gid, title, desc, icon) in enumerate(state.GAMES):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"### {icon} {title}")
                st.write(desc)
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
    if page == state.HOME:
        render_home()
    elif page in GAME_RENDERERS:
        GAME_RENDERERS[page]()
    else:
        # 未知のページ識別子はホームへフォールバック
        state.go_home()


if __name__ == "__main__":
    main()
