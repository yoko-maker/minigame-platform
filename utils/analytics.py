"""アクセス数の計測。Google スプレッドシートに追記する。

方針:
- 認証情報（st.secrets）が無い／ライブラリが未導入／ネットワーク失敗のいずれでも、
  **アプリは絶対に止めない**。記録は静かにスキップする（ゲームが最優先）。
- スプレッドシートには1行1イベントで追記する（タイムスタンプ, 種別, ゲーム名）。
  行数＝総アクセス、種別/ゲーム名で絞れば内訳が出る。集計はシート側で行う。

必要な secrets（Streamlit Cloud のアプリ設定 → Secrets、またはローカルの
.streamlit/secrets.toml）::

    [gcp_service_account]
    type = "service_account"
    project_id = "..."
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "xxxx@xxxx.iam.gserviceaccount.com"
    client_id = "..."
    token_uri = "https://oauth2.googleapis.com/token"

    [analytics]
    sheet_key = "スプレッドシートのURLに含まれるキー"

サービスアカウントのメールアドレスに、対象シートを「編集者」で共有しておくこと。
"""

from __future__ import annotations

import datetime

import streamlit as st

_VISIT_FLAG = "_analytics_visit_logged"


@st.cache_resource(show_spinner=False)
def _worksheet():
    """記録先のワークシートを返す。未設定・失敗時は None（以後キャッシュ）。

    gspread と google-auth は遅延 import する。未導入でもアプリが動くように。
    """
    try:
        if "gcp_service_account" not in st.secrets or "analytics" not in st.secrets:
            return None
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(st.secrets["analytics"]["sheet_key"])
        return sheet.sheet1
    except Exception:
        # 設定ミス・権限不足・ネットワーク不通などは記録を諦める（アプリは続行）。
        return None


def _append(event: str, game: str = "") -> None:
    ws = _worksheet()
    if ws is None:
        return
    try:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        ws.append_row([ts, event, game], value_input_option="RAW")
    except Exception:
        # 追記に失敗してもゲームは止めない。
        pass


def record_visit() -> None:
    """1セッションにつき1回だけ「訪問」を記録する。app.py の入口で呼ぶ。"""
    if st.session_state.get(_VISIT_FLAG):
        return
    st.session_state[_VISIT_FLAG] = True
    _append("visit")


def record_play(game: str) -> None:
    """ゲーム開始を「プレイ」として記録する。遊び方画面のスタート時に呼ぶ。"""
    _append("play", game)


def is_enabled() -> bool:
    """計測が有効に設定されているか（デバッグ表示用）。"""
    return _worksheet() is not None
