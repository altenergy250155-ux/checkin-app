# 勤務地チェックインシステム

社員がブラウザからワンクリックで勤務地をSlackステータスに登録できるシステムです。

## 機能

- Slack OAuthによる認証（@altenergy.co.jp のみ許可）
- IPアドレスによる勤務地自動判定
  - 銀座オフィス: 39.110.215.6
  - 立川オフィス: 143.189.212.172
- 出勤ボタン: Slackステータスを「○○オフィスで勤務中」に更新
- 退勤ボタン: Slackステータスをクリア

## セットアップ

### 1. Slack Appの設定

1. https://api.slack.com/apps でアプリを作成
2. OAuth & Permissions で以下のUser Token Scopesを追加:
   - `users.profile:write`
   - `users:read`
   - `users:read.email`
3. Redirect URLを設定: `https://your-app.onrender.com/slack/callback`

### 2. Renderへのデプロイ

1. GitHubにリポジトリを作成してコードをプッシュ
2. https://render.com でアカウント作成
3. New > Web Service を選択
4. GitHubリポジトリを接続
5. 環境変数を設定:
   - `SLACK_CLIENT_ID`: Slack AppのClient ID
   - `SLACK_CLIENT_SECRET`: Slack AppのClient Secret
   - `FLASK_SECRET_KEY`: 自動生成される

### 3. Slack AppのRedirect URL更新

デプロイ完了後、Render上のURLを確認し、Slack Appの設定でRedirect URLを更新:
```
https://your-app-name.onrender.com/slack/callback
```

## ローカル開発

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# 環境変数の設定
cp .env.example .env
# .envファイルを編集してSlack認証情報を設定

# アプリの起動
python app.py
```

## ファイル構成

```
checkin-app/
├── app.py              # メインアプリケーション
├── requirements.txt    # 依存パッケージ
├── render.yaml         # Render設定
├── templates/
│   └── index.html      # 画面テンプレート
├── .env.example        # 環境変数サンプル
└── README.md           # このファイル
```

## オフィスの追加・変更

`app.py` の `OFFICE_IPS` を編集してください:

```python
OFFICE_IPS = {
    '39.110.215.6': {'name': '銀座オフィス', 'emoji': ':office:', 'status': '銀座オフィスで勤務中'},
    '143.189.212.172': {'name': '立川オフィス', 'emoji': ':office:', 'status': '立川オフィスで勤務中'},
    # 新しいオフィスを追加
    'xxx.xxx.xxx.xxx': {'name': '新オフィス', 'emoji': ':office:', 'status': '新オフィスで勤務中'},
}
```
