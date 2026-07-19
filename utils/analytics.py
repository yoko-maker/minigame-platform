"""アクセス数の計測（設定不要の簡易版）。

無認証・無料のヒットカウンター（Abacus）に数を数えてもらうだけの実装。
アカウント登録も認証情報も不要で、そのまま動く。

方針:
- ネットワーク失敗・サービス停止のいずれでも **アプリは絶対に止めない**。
  記録は静かにスキップする（ゲームが最優先）。標準ライブラリだけで動く。
- 新しいセッションを1「訪問」、ゲーム開始を1「プレイ」として数える。
  ゲーム別のプレイ数は play_<game> という別カウンターで持つ。

集計の見方（ブラウザで直接開ける。増えないので確認用に使える）:
- 総アクセス:  https://abacus.jasoncameron.dev/get/<namespace>/visit
- 博物館プレイ: https://abacus.jasoncameron.dev/get/<namespace>/play_museum

<namespace> は下の NAMESPACE（他の人の集計と混ざらないよう任意の文字列に変えてよい。
st.secrets["analytics"]["namespace"] を設定すればそちらが優先される）。
"""

from __future__ import annotations

import json
import urllib.request

import streamlit as st

_BASE = "https://abacus.jasoncameron.dev"
# 他デプロイと数が混ざらないための名前空間。好きな文字列に変えてよい
# （変えると数はリセットされる）。
NAMESPACE = "yoko-minigames-live-2q7x"
_TIMEOUT = 2.0  # 秒。計測でゲームを待たせないよう短くする。

_VISIT_FLAG = "_analytics_visit_logged"
_VISIT_TOTAL = "_analytics_visit_total"


def _namespace() -> str:
    try:
        ns = st.secrets["analytics"]["namespace"]
        if ns:
            return str(ns)
    except Exception:
        pass
    return NAMESPACE


def _call(action: str, key: str) -> int | None:
    """action は "hit"（+1して返す）か "get"（読むだけ）。失敗時は None。"""
    url = f"{_BASE}/{action}/{_namespace()}/{key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "streamlit-minigames"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return int(data["value"])
    except Exception:
        # サービス停止・通信不能などは記録を諦める（アプリは続行）。
        return None


def record_visit() -> None:
    """1セッションにつき1回だけ「訪問」を数える。app.py の入口で呼ぶ。"""
    if st.session_state.get(_VISIT_FLAG):
        return
    st.session_state[_VISIT_FLAG] = True
    total = _call("hit", "visit")
    if total is not None:
        st.session_state[_VISIT_TOTAL] = total


def record_play(game: str) -> None:
    """ゲーム開始を「プレイ」として数える。遊び方画面のスタート時に呼ぶ。"""
    _call("hit", f"play_{game}")


def total_visits() -> int | None:
    """このセッション開始時に取得した総アクセス数（表示用）。無ければ None。

    表示のたびに数えに行くとカウンターに毎回アクセスしてしまうので、
    セッション開始時に一度だけ取った値を使い回す。
    """
    return st.session_state.get(_VISIT_TOTAL)
