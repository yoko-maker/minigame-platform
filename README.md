# 🎮 ミニゲーム統合プラットフォーム

1つの Streamlit アプリから4種類の短時間ゲーム（各5〜10分）を選んで遊べる統合プラットフォームです。
ゲーム間でのデータ共有（共通通貨・レベル・実績）はなく、各ゲームは単独で完結します。

## 収録ゲーム

| ゲーム | 概要 | 勝利条件 |
|---|---|---|
| 🛂 AI入国審査官 | 書類と会話からAIか人間かを見抜く推理ゲーム | 全5人中3人以上を正しく判定 |
| 💰 ブラックマーケット | 価値不明の商品をAIとの心理戦で競り落とす | 全5商品終了時の累計利益が +300 以上 |
| 💎 博物館潜入 | 特性とルートを活かし宝石を盗んで脱出 | 宝石を持って出口に到達 |
| 🐈 癒し猫カフェ | 10営業日で高評価を目指す経営シミュレーション | 10日終了時の総合評価が60点以上 |

## セットアップ

```bash
py -3 -m pip install -r requirements.txt
```

## 起動

```bash
py -3 -m streamlit run app.py
```

ブラウザが自動で開きます。ホーム画面から遊びたいゲームの「▶ プレイ」を押してください。

> **なぜ `py -3 -m streamlit` なのか**
> この環境では `streamlit.exe` のインストール先（`...\pythoncore-3.14-64\Scripts\`）が PATH に
> 含まれていないため、`streamlit run app.py` と打つと「認識されません」というエラーになります。
> `py -3 -m streamlit` は Python モジュールとして直接呼び出すため PATH に依存せず動作します。
> （`Scripts` ディレクトリを PATH に追加すれば `streamlit run app.py` も使えるようになります。）
各ゲーム画面の上部の **🏠 戻る** でホームへ、**🔄 リセット** でそのゲームを最初からやり直せます。

## 公開（Streamlit Community Cloud）

GitHub のリポジトリからそのまま常時公開できる。無料枠では **リポジトリを public に
する必要がある** 点に注意。

1. このリポジトリを GitHub（`yoko-maker/minigame-platform`）に push する。
2. [share.streamlit.io](https://share.streamlit.io) に GitHub アカウントでサインインする。
3. **Create app** →「Deploy a public app from GitHub」を選ぶ。
4. 次のとおり指定して Deploy を押す。

   | 項目 | 値 |
   |---|---|
   | Repository | `yoko-maker/minigame-platform` |
   | Branch | `main` |
   | Main file path | `app.py` |

以降は `main` に push するたびに自動で再デプロイされる。依存は `requirements.txt`
から、テーマ以外の設定は `.streamlit/config.toml` から読まれる。

> Community Cloud の無料枠はしばらくアクセスが無いとアプリがスリープし、次の
> アクセス時に自動で復帰する（初回表示が数十秒かかることがある）。

## アクセス数の計測（設定不要）

新規セッションを「訪問」、ゲーム開始を「プレイ」として、無認証・無料のヒットカウンター
（[Abacus](https://abacus.jasoncameron.dev)）に数えてもらいます。**アカウント登録も
認証情報も不要**で、デプロイすればそのまま動きます。ホーム画面の下部に累計アクセス数が
表示されます。

- 集計は URL をブラウザで開くだけで見られます（開いても数は増えません）:
  - 総アクセス: `https://abacus.jasoncameron.dev/get/<namespace>/visit`
  - ゲーム別: `https://abacus.jasoncameron.dev/get/<namespace>/play_museum`（`play_immigration` など）
- `<namespace>` は [utils/analytics.py](utils/analytics.py) の `NAMESPACE`。他の人の集計と
  混ざらないよう、好きな文字列に変えてかまいません（変えると数はリセットされます）。

> 計測は標準ライブラリの `urllib` だけで動くので追加の依存はありません。カウンターサービスが
> 停止・不通でも `utils/analytics.py` が記録を静かにスキップするだけで、ゲームは止まりません。
> 外部サービスへ通信させたくない場合は、`NAMESPACE` を変える代わりに `analytics.record_visit`
> / `record_play` の呼び出し（app.py・utils/ui.py）を外せば無効化できます。

## ディレクトリ構成

```
app.py              # エントリポイント（ホーム画面 + session_state による自前ルーティング）
games/
  immigration.py    # AI入国審査官
  black_market.py   # ブラックマーケット
  museum.py         # 博物館潜入
  cat_cafe.py       # 癒し猫カフェ
utils/
  state.py          # ページ遷移・ゲーム別の独立状態管理・自己ベスト
  ui.py             # 共通UI部品（ヘッダ・戻る/リセット・遊び方・勝敗バナー等）
  theme.py          # ゲームごとの配色・書体・質感
  analytics.py      # アクセス数の計測（任意・Googleスプレッドシート）
requirements.txt
```

## 設計メモ

- Streamlit 標準のマルチページ（`pages/` ディレクトリ）機能は使わず、`st.session_state.current_page`
  による自前ルーティングでホーム/各ゲームを切り替えています（戻る・リセット・遊び方の共通UI要件のため）。
- 各ゲームの状態は `utils.state.game_state("<game>")` が返す専用 dict に閉じ込めており、
  ゲーム間で状態が混ざりません（「ゲーム間データ共有なし」方針を構造で担保）。
- 各ゲームは外部APIを使わず、ルールベース＋乱数（`random.Random` にシードを保持）で
  「AI」挙動やイベントを再現しています。
- ゲームロジックは Streamlit 非依存の純粋関数として切り出されており、単体で検証可能です。

## 将来拡張（仕様書より）

難易度選択 / セーブ / ランダムイベント追加 / 新ゲーム追加 / アニメーション・演出強化。
