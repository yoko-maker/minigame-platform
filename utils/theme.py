"""ゲームごとの視覚的アイデンティティ（配色・書体・質感）を定義して注入する。

各ゲームは独立した世界として作り分ける。共通するのは「トークンの構造」だけで、
値はゲームごとに異なる。ホームは4つの世界を並べるハブなので、自身の色を持たず
各ゲームのアクセント色を借りて光らせる。

使い方::

    from utils import theme
    theme.inject(state.CAT_CAFE)   # そのページのCSSを流し込む

Streamlit は1度に1ページしか描画しないため、CSSはページ単位で丸ごと差し替える。
セレクタをゲームごとにスコープする必要はない。
"""

from __future__ import annotations

import streamlit as st

# 日本語は環境依存を避けてOSのフォントにフォールバックさせる。
JP_GOTHIC = '"Yu Gothic UI", "Hiragino Kaku Gothic ProN", Meiryo, sans-serif'
JP_MINCHO = '"Shippori Mincho", "Yu Mincho", "Hiragino Mincho ProN", serif'
JP_ROUND = '"M PLUS Rounded 1c", "Hiragino Maru Gothic ProN", "Yu Gothic UI", sans-serif'

FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=M+PLUS+Rounded+1c:wght@400;700;800"
    "&family=Shippori+Mincho:wght@500;700;800"
    "&family=Share+Tech+Mono"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&display=swap');"
)

# 各テーマのトークン。
#   bg/panel/line/text/muted : 面と文字
#   accent                   : そのゲームを象徴する一色
#   alt                      : 補助の一色
#   font_ui / font_display   : 本文と見出し
#   radius                   : 角の丸み（世界観の硬さ／やわらかさ）
#   texture                  : 背景に敷く質感（そのゲーム固有の署名）
#   eyebrow                  : 遊び方画面の小見出し。飾りではなく、これから
#                              置かれる状況を伝える札として書く。
THEMES: dict[str, dict[str, str]] = {
    "home": {
        "bg": "#0A0B0F",
        "panel": "#14161D",
        "line": "#262A36",
        "text": "#E7E9EE",
        "muted": "#8A90A0",
        "accent": "#E7E9EE",
        "alt": "#8A90A0",
        "font_ui": JP_GOTHIC,
        "font_display": JP_GOTHIC,
        "radius": "14px",
        "texture": "radial-gradient(1200px 600px at 50% -10%, #1B1F2B 0%, #0A0B0F 60%)",
        "eyebrow": "",
    },
    "immigration": {
        # 冷たい審査ブース。蛍光灯とスチール、公文書のインク。
        "bg": "#0E1A2B",
        "panel": "#16263B",
        "line": "#2C4A6B",
        "text": "#E8EDF2",
        "muted": "#8FA6BF",
        "accent": "#4DD0E1",
        "alt": "#D7263D",
        "font_ui": JP_GOTHIC,
        "font_display": f'"Share Tech Mono", {JP_GOTHIC}',
        "radius": "2px",
        "texture": "linear-gradient(180deg, #12203400 0%, #0B1626 100%)",
        "eyebrow": "第4ゲート — 入国審査ブース",
    },
    "black_market": {
        # 闇市。蝋燭の灯りと金、血の色。
        "bg": "#0B0A08",
        "panel": "#171310",
        "line": "#3A2F1C",
        "text": "#E4DCC9",
        "muted": "#9C8F73",
        "accent": "#C9A227",
        "alt": "#7E1F26",
        "font_ui": JP_MINCHO,
        "font_display": JP_MINCHO,
        "radius": "3px",
        "texture": "radial-gradient(800px 400px at 50% 0%, #241B10 0%, #0B0A08 70%)",
        "eyebrow": "非公開競売 — 本日の出品",
    },
    "museum": {
        # 潜入計画。青写真の方眼と白い罫線、レーザーの赤。
        "bg": "#0D2137",
        "panel": "#102A45",
        "line": "#2E5B85",
        "text": "#DCE9F5",
        "muted": "#7FA3C4",
        "accent": "#28C7B7",
        "alt": "#FF4757",
        "font_ui": JP_GOTHIC,
        "font_display": f'"IBM Plex Mono", {JP_GOTHIC}',
        "radius": "2px",
        "texture": (
            "linear-gradient(#1D3A5C 1px, transparent 1px) 0 0 / 28px 28px,"
            "linear-gradient(90deg, #1D3A5C 1px, transparent 1px) 0 0 / 28px 28px,"
            "#0D2137"
        ),
        "eyebrow": "潜入計画書 — 取扱注意",
    },
    "cat_cafe": {
        # ミルクティーとクッション。やわらかく、角がない。
        "bg": "#FFF7EF",
        "panel": "#FFFFFF",
        "line": "#EADDCE",
        "text": "#5B4034",
        "muted": "#A08A7A",
        "accent": "#F2A0A0",
        "alt": "#9CBF9A",
        "font_ui": JP_ROUND,
        "font_display": JP_ROUND,
        "radius": "22px",
        "texture": "radial-gradient(900px 500px at 50% -10%, #FFFFFF 0%, #FFF1E2 70%)",
        "eyebrow": "開業から10日間 — 営業計画",
    },
}


def _css(t: dict[str, str]) -> str:
    """トークンから1ページ分のCSSを組み立てる。"""
    return f"""
<style>
{FONT_IMPORT}

/* ---- 面と文字 ------------------------------------------------------- */
.stApp {{
    background: {t["texture"]};
    background-attachment: fixed;
    color: {t["text"]};
    font-family: {t["font_ui"]};
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMain"] .stMarkdown,
[data-testid="stMain"] p,
[data-testid="stMain"] li,
[data-testid="stMain"] label {{
    color: {t["text"]};
    font-family: {t["font_ui"]};
}}

/* Streamlit のアイコン（展開の▼など）は "Material Symbols Rounded" の合字で
   描かれる。本文フォントを継承させると合字が解決されず "arrow_drop_down" の
   ような名前が生のまま出てしまうため、アイコンだけは必ず元のフォントに戻す。 */
[data-testid="stIconMaterial"],
span[class*="material-symbols"],
span[class*="material-icons"] {{
    font-family: "Material Symbols Rounded" !important;
    font-feature-settings: "liga" !important;
    -webkit-font-feature-settings: "liga" !important;
    font-weight: normal !important;
    letter-spacing: normal !important;
    text-indent: 0 !important;
}}
[data-testid="stMain"] h1,
[data-testid="stMain"] h2,
[data-testid="stMain"] h3 {{
    color: {t["text"]};
    font-family: {t["font_display"]};
    letter-spacing: .02em;
}}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {{
    color: {t["muted"]} !important;
}}

/* ---- 枠つきコンテナ（ゲーム内のパネル） ------------------------------ */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {{
    border-radius: {t["radius"]};
}}
div[data-testid="stVerticalBlockBorderWrapper"][style*="border"] {{
    background: {t["panel"]};
    border: 1px solid {t["line"]} !important;
    border-radius: {t["radius"]};
}}

/* ---- ボタン --------------------------------------------------------- */
.stButton > button {{
    background: {t["panel"]};
    color: {t["text"]};
    border: 1px solid {t["line"]};
    border-radius: {t["radius"]};
    font-family: {t["font_ui"]};
    font-weight: 700;
    transition: border-color .15s ease, color .15s ease, transform .15s ease;
}}
.stButton > button:hover {{
    border-color: {t["accent"]};
    color: {t["accent"]};
}}
.stButton > button:active {{ transform: translateY(1px); }}
.stButton > button:focus-visible {{
    outline: 2px solid {t["accent"]};
    outline-offset: 2px;
}}
.stButton > button[kind="primary"] {{
    background: {t["accent"]};
    border-color: {t["accent"]};
    color: {t["bg"]};
}}
.stButton > button[kind="primary"]:hover {{
    filter: brightness(1.08);
    color: {t["bg"]};
}}
.stButton > button:disabled,
.stButton > button:disabled:hover {{
    opacity: .38;
    border-color: {t["line"]};
    color: {t["muted"]};
}}

/* ---- 数値表示・入力 ------------------------------------------------- */
[data-testid="stMetricValue"] {{
    color: {t["accent"]};
    font-family: {t["font_display"]};
}}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{ color: {t["muted"]} !important; }}
[data-testid="stExpander"] details {{
    background: {t["panel"]};
    border: 1px solid {t["line"]};
    border-radius: {t["radius"]};
}}
[data-testid="stExpander"] summary {{ color: {t["text"]}; font-family: {t["font_ui"]}; }}
[data-testid="stSlider"] [role="slider"] {{ background: {t["accent"]}; }}
hr, [data-testid="stDivider"] {{ border-color: {t["line"]}; }}

/* 入力ウィジェットの中身は Streamlit の基本テーマ由来の色が残るので、
   テーマのトークンで明示的に塗り直す（明るい猫カフェと暗い3ゲームの両立）。 */
[data-testid="stNumberInput"] input,
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {{
    background: {t["panel"]} !important;
    color: {t["text"]} !important;
    border-color: {t["line"]} !important;
    font-family: {t["font_ui"]};
}}
[data-testid="stNumberInput"] button {{
    background: {t["panel"]} !important;
    color: {t["text"]} !important;
    border-color: {t["line"]} !important;
}}
[data-baseweb="select"] > div {{
    background: {t["panel"]} !important;
    color: {t["text"]} !important;
    border-color: {t["line"]} !important;
}}
[data-testid="stCheckbox"] label span,
[data-testid="stRadio"] label span {{ color: {t["text"]}; }}

/* ---- 遊び方画面（ゲーム本編への扉） --------------------------------- */
.briefing-hero {{
    text-align: center;
    padding: 2.75rem 1rem 1.25rem;
}}
.briefing-icon {{
    font-size: 4.5rem;
    line-height: 1;
    filter: drop-shadow(0 6px 20px {t["accent"]}55);
}}
.briefing-eyebrow {{
    margin-top: 1rem;
    color: {t["accent"]};
    font-family: {t["font_display"]};
    font-size: .72rem;
    letter-spacing: .34em;
    text-indent: .34em;
}}
.briefing-title {{
    margin: .35rem 0 0;
    font-family: {t["font_display"]};
    font-size: clamp(1.9rem, 5vw, 3.1rem);
    font-weight: 800;
    color: {t["text"]};
}}
.briefing-tagline {{
    margin: .6rem auto 0;
    max-width: 34rem;
    color: {t["muted"]};
}}
.briefing-rule {{
    height: 1px;
    margin: 1.75rem auto 2rem;
    max-width: 40rem;
    background: linear-gradient(90deg, transparent, {t["accent"]}, transparent);
    opacity: .5;
}}
.briefing-body {{ color: {t["text"]}; }}

/* ---- ホームのゲームカード ------------------------------------------- */
.hub-title {{
    font-family: {t["font_display"]};
    font-size: clamp(1.8rem, 4.5vw, 2.8rem);
    font-weight: 800;
    margin: 0;
}}
.hub-rule {{
    height: 1px;
    margin: 1.5rem 0 2rem;
    background: {t["line"]};
}}
.card-art {{
    display: flex;
    align-items: center;
    gap: .9rem;
    padding: .2rem 0 .1rem;
}}
.card-icon {{ font-size: 2.4rem; line-height: 1; }}
.card-name {{ font-weight: 800; font-size: 1.15rem; }}
.card-eyebrow {{
    font-size: .66rem;
    letter-spacing: .28em;
    text-indent: .28em;
    margin-top: .15rem;
}}
.card-desc {{ color: {t["muted"]}; font-size: .9rem; margin: .55rem 0 .2rem; }}

/* ---- 動きが苦手な人への配慮 ----------------------------------------- */
@media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; animation: none !important; }}
}}
</style>
"""


def inject(theme_key: str) -> None:
    """指定テーマのCSSを現在のページに流し込む。"""
    t = THEMES.get(theme_key, THEMES["home"])
    st.markdown(_css(t), unsafe_allow_html=True)


def token(theme_key: str, name: str) -> str:
    """テーマのトークンを1つ取り出す（HTMLを組み立てる側から使う）。"""
    return THEMES.get(theme_key, THEMES["home"])[name]
