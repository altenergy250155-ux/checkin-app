import os
import requests
from flask import Flask, redirect, request, session, render_template, url_for
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Slack App credentials
SLACK_CLIENT_ID = os.environ.get('SLACK_CLIENT_ID')
SLACK_CLIENT_SECRET = os.environ.get('SLACK_CLIENT_SECRET')

# Office IP addresses
OFFICE_IPS = {
    '39.110.215.6': {'name': '銀座オフィス', 'emoji': ':office:', 'status': '銀座オフィスで勤務中'},
    '143.189.212.172': {'name': '立川オフィス', 'emoji': ':office:', 'status': '立川オフィスで勤務中'},
}

# Allowed email domain
ALLOWED_DOMAIN = 'altenergy.co.jp'

# Slack OAuth URLs
SLACK_AUTH_URL = 'https://slack.com/oauth/v2/authorize'
SLACK_TOKEN_URL = 'https://slack.com/api/oauth.v2.access'
SLACK_PROFILE_SET_URL = 'https://slack.com/api/users.profile.set'


def get_client_ip():
    """クライアントのIPアドレスを取得"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def get_office_info(ip_address):
    """IPアドレスからオフィス情報を取得"""
    return OFFICE_IPS.get(ip_address)


def login_required(f):
    """ログイン必須デコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
def index():
    """メインページ"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    
    return render_template('index.html', 
                         user=user, 
                         client_ip=client_ip,
                         office_info=office_info)


@app.route('/login')
def login():
    """Slack OAuth認証開始"""
    redirect_uri = url_for('slack_callback', _external=True)
    
    # Sign in with Slack (OpenID Connect) を使用
    auth_url = (
        f"{SLACK_AUTH_URL}"
        f"?client_id={SLACK_CLIENT_ID}"
        f"&user_scope=openid,profile,email,users.profile:write"
        f"&redirect_uri={redirect_uri}"
    )
    
    return redirect(auth_url)


@app.route('/slack/callback')
def slack_callback():
    """Slack OAuth コールバック"""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return f"認証エラー: {error}", 400
    
    if not code:
        return "認証コードがありません", 400
    
    redirect_uri = url_for('slack_callback', _external=True)
    
    # アクセストークンを取得
    response = requests.post(SLACK_TOKEN_URL, data={
        'client_id': SLACK_CLIENT_ID,
        'client_secret': SLACK_CLIENT_SECRET,
        'code': code,
        'redirect_uri': redirect_uri
    })
    
    token_data = response.json()
    
    if not token_data.get('ok'):
        return f"トークン取得エラー: {token_data.get('error')}", 400
    
    # ユーザー情報を取得（トークンレスポンスに含まれる）
    authed_user = token_data.get('authed_user', {})
    access_token = authed_user.get('access_token')
    user_id = authed_user.get('id')
    
    # OpenID Connect の場合、ユーザー情報を userinfo エンドポイントから取得
    userinfo_response = requests.get(
        'https://slack.com/api/openid.connect.userInfo',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    
    userinfo = userinfo_response.json()
    
    if not userinfo.get('ok'):
        return f"ユーザー情報取得エラー: {userinfo.get('error')}", 400
    
    email = userinfo.get('email', '')
    name = userinfo.get('name', '')
    
    # メールドメインを確認
    if not email.endswith(f'@{ALLOWED_DOMAIN}'):
        return f"このアプリは @{ALLOWED_DOMAIN} のメールアドレスを持つユーザーのみ利用できます", 403
    
    # セッションにユーザー情報を保存
    session['user'] = {
        'id': user_id,
        'name': name,
        'email': email,
        'access_token': access_token
    }
    
    return redirect(url_for('index'))


@app.route('/checkin', methods=['POST'])
@login_required
def checkin():
    """出勤チェックイン"""
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    
    if not office_info:
        return render_template('index.html',
                             user=user,
                             client_ip=client_ip,
                             office_info=None,
                             message='現在のIPアドレスは登録されたオフィスのものではありません',
                             message_type='error')
    
    # Slackステータスを更新
    response = requests.post(SLACK_PROFILE_SET_URL, 
        headers={
            'Authorization': f'Bearer {user["access_token"]}',
            'Content-Type': 'application/json'
        },
        json={
            'profile': {
                'status_text': office_info['status'],
                'status_emoji': office_info['emoji'],
                'status_expiration': 0
            }
        }
    )
    
    result = response.json()
    
    if result.get('ok'):
        message = f"{office_info['name']}で出勤しました"
        message_type = 'success'
    else:
        message = f"ステータス更新エラー: {result.get('error')}"
        message_type = 'error'
    
    return render_template('index.html',
                         user=user,
                         client_ip=client_ip,
                         office_info=office_info,
                         message=message,
                         message_type=message_type)


@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    """退勤チェックアウト"""
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    
    # Slackステータスをクリア
    response = requests.post(SLACK_PROFILE_SET_URL,
        headers={
            'Authorization': f'Bearer {user["access_token"]}',
            'Content-Type': 'application/json'
        },
        json={
            'profile': {
                'status_text': '',
                'status_emoji': '',
                'status_expiration': 0
            }
        }
    )
    
    result = response.json()
    
    if result.get('ok'):
        message = '退勤しました。お疲れ様でした！'
        message_type = 'success'
    else:
        message = f"ステータス更新エラー: {result.get('error')}"
        message_type = 'error'
    
    return render_template('index.html',
                         user=user,
                         client_ip=client_ip,
                         office_info=office_info,
                         message=message,
                         message_type=message_type)


@app.route('/logout')
def logout():
    """ログアウト"""
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)
