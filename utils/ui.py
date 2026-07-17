"""ゲーム共通の UI 部品。

各ゲームは冒頭で `game_header(...)` を呼ぶことで、タイトル・戻る・リセットの
共通ヘッダを表示する。個別ゲームの見た目はこのヘッダ以下に自由に構築してよい。
"""

from __future__ import annotations

import json
from typing import Callable

import streamlit as st
import streamlit.components.v1 as components

from . import state, theme


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
            state.request_scroll_top()
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


def scroll_to_top() -> None:
    """親ウィンドウをページ最上部までスクロールさせる。

    Streamlit は再実行してもスクロール位置を保つため、ページを切り替えると
    前のページの途中の高さのまま新しいページが表示されてしまう。遷移時にこれを
    呼んで最上部へ戻す。

    描画のたびに再実行させたいので、nonce を埋め込んで iframe の内容を毎回変える
    （内容が同一だとブラウザがスクリプトを再実行しないことがある）。
    """
    nonce = st.session_state.get("_scroll_nonce", 0) + 1
    st.session_state["_scroll_nonce"] = nonce
    components.html(
        f"""
        <script>
        /* nonce:{nonce} */
        (function () {{
          try {{
            var doc = window.parent.document;
            var selectors = [
              '[data-testid="stMain"]',
              'section.main',
              '[data-testid="stAppViewContainer"]',
              '.main'
            ];
            selectors.forEach(function (sel) {{
              var el = doc.querySelector(sel);
              if (el && typeof el.scrollTo === 'function') {{
                el.scrollTo({{ top: 0, behavior: 'auto' }});
              }}
            }});
            if (doc.documentElement) doc.documentElement.scrollTop = 0;
            if (doc.body) doc.body.scrollTop = 0;
            window.parent.scrollTo(0, 0);
          }} catch (e) {{
            /* 何らかの理由でDOMに触れなくても、表示自体は壊さない */
          }}
        }})();
        </script>
        """,
        height=0,
    )


def bind_keys(mapping: dict[str, str]) -> None:
    """キーボードのキーを、指定した key を持つボタンのクリックに割り当てる。

    Args:
        mapping: {KeyboardEvent.key: ボタンの key}。
                 例: {"ArrowUp": "mus_move_up", "ArrowDown": "mus_move_down"}

    Streamlit は key を持つウィジェットのラッパ要素に `st-key-<key>` という
    クラスを付けるので、それを手がかりに本物のボタンを押す。ボタンを押すだけなので
    通常のクリックと挙動は完全に同じになる（状態遷移を二重に実装しなくてよい）。

    入力欄にフォーカスがあるときは何もしない（文字入力を奪わないため）。
    再実行のたびに古いリスナを外してから付け直すので、多重登録は起きない。
    """
    js_map = json.dumps(mapping)
    components.html(
        f"""
        <script>
        (function () {{
          try {{
            var doc = window.parent.document;
            var map = {js_map};

            if (window.parent.__stKeyHandler) {{
              doc.removeEventListener('keydown', window.parent.__stKeyHandler, true);
            }}

            var handler = function (e) {{
              if (e.ctrlKey || e.metaKey || e.altKey) return;
              var tag = e.target && e.target.tagName;
              var editing = e.target && e.target.isContentEditable;
              if (editing || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

              var widgetKey = map[e.key];
              if (!widgetKey) return;

              var btn = doc.querySelector('.st-key-' + widgetKey + ' button');
              if (btn && !btn.disabled) {{
                e.preventDefault();
                btn.click();
              }}
            }};

            doc.addEventListener('keydown', handler, true);
            window.parent.__stKeyHandler = handler;
          }} catch (err) {{
            /* キー操作が使えなくても、ボタンでは遊べるので表示は壊さない */
          }}
        }})();
        </script>
        """,
        height=0,
    )


def briefing(game_key: str, how_to_play: str) -> None:
    """ゲーム本編に入る前の「遊び方」画面。

    ゲームを選ぶとまずこの画面が出る。スタートを押して初めて本編が始まる。
    そのゲームの世界観に最初に触れる場所なので、扉として作る。
    """
    title, desc, icon = state.GAME_META[game_key]
    eyebrow = theme.token(game_key, "eyebrow")

    st.markdown(
        f"""
        <div class="briefing-hero">
          <div class="briefing-icon">{icon}</div>
          <div class="briefing-eyebrow">{eyebrow}</div>
          <h1 class="briefing-title">{title}</h1>
          <p class="briefing-tagline">{desc}</p>
        </div>
        <div class="briefing-rule"></div>
        """,
        unsafe_allow_html=True,
    )

    left, mid, right = st.columns([1, 2.2, 1])
    with mid:
        st.markdown('<div class="briefing-body">', unsafe_allow_html=True)
        st.markdown(how_to_play)
        st.markdown("</div>", unsafe_allow_html=True)
        st.write("")
        if st.button("▶ ゲームを始める", key=f"start_{game_key}", type="primary", use_container_width=True):
            state.mark_started(game_key)
            state.request_scroll_top()
            st.rerun()
        if st.button("← ゲーム一覧に戻る", key=f"briefing_back_{game_key}", use_container_width=True):
            state.go_home()
