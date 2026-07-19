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

## アクセス数の計測（任意）

新規セッションを「訪問」、ゲーム開始を「プレイ」として Google スプレッドシートに
1行ずつ追記します。**設定しなくてもアプリは普通に動きます**（未設定なら記録を静かに
スキップします）。設定すると、シートの行数が総アクセス数、種別・ゲーム名で絞れば内訳になります。

セットアップ（Google 側の作業が必要です）:

1. [Google Cloud Console](https://console.cloud.google.com) でプロジェクトを作り、
   「Google Sheets API」を有効化する。
2. **サービスアカウント**を作成し、JSON キーをダウンロードする。
3. 記録用の **Google スプレッドシート**を新規作成し、URL のキー
   （`https://docs.google.com/spreadsheets/d/<ここがキー>/edit`）を控える。
   1行目に見出し `timestamp / event / game` を入れておくと読みやすい。
4. そのシートを、サービスアカウントのメールアドレス（`xxxx@xxxx.iam.gserviceaccount.com`）に
   **編集者**として共有する。
5. 認証情報を Streamlit の Secrets に登録する。
   - Community Cloud: アプリ設定 → **Secrets** に下記を貼り付け
   - ローカル: `.streamlit/secrets.toml`（このファイルは .gitignore 済み。コミットされません）

   ```toml
   [gcp_service_account]
   type = "service_account"
   project_id = "..."
   private_key_id = "..."
   private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
   client_email = "xxxx@xxxx.iam.gserviceaccount.com"
   client_id = "..."
   token_uri = "https://oauth2.googleapis.com/token"

   [analytics]
   sheet_key = "スプレッドシートのキー"
   ```

集計はスプレッドシート側で行います（例: `=COUNTIF(B:B,"play")` でプレイ回数、
`=COUNTIFS(B:B,"play",C:C,"museum")` で博物館のプレイ回数）。

> 依存パッケージ `gspread` / `google-auth` は requirements.txt に含まれます。
> Secrets 未設定でも `utils/analytics.py` が記録をスキップするだけなので、起動には影響しません。

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
