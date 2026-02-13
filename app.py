import os
import base64
from datetime import datetime, date, timezone, timedelta
import requests
from flask import Flask, redirect, request, session, render_template, url_for
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Slack App credentials
SLACK_CLIENT_ID = os.environ.get('SLACK_CLIENT_ID')
SLACK_CLIENT_SECRET = os.environ.get('SLACK_CLIENT_SECRET')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')

# HRMOS API credentials
HRMOS_COMPANY_URL = os.environ.get('HRMOS_COMPANY_URL')
HRMOS_API_SECRET = os.environ.get('HRMOS_API_SECRET')
HRMOS_API_BASE = f"https://ieyasu.co/api/{HRMOS_COMPANY_URL}/v1"

# Office IP addresses
OFFICE_IPS = {
    '39.110.215.6': {'name': '銀座オフィス', 'emoji': ':office:', 'status': '銀座オフィスで勤務中'},
    '143.189.212.172': {'name': '立川オフィス', 'emoji': ':cityscape:', 'status': '立川オフィスで勤務中'},
}

# Other work locations (for unknown IPs)
OTHER_LOCATIONS = {
    'remote': {'name': 'リモートワーク', 'emoji': ':heads-down:', 'status': 'リモートワーク中'},
    'site': {'name': '現場', 'emoji': ':building_construction:', 'status': '現場で勤務中'},
}

# Allowed email domain
ALLOWED_DOMAIN = 'altenergy.co.jp'

# Slack API URLs
SLACK_AUTH_URL = 'https://slack.com/oauth/v2/authorize'
SLACK_TOKEN_URL = 'https://slack.com/api/oauth.v2.access'
SLACK_USER_INFO_URL = 'https://slack.com/api/users.info'
SLACK_PROFILE_SET_URL = 'https://slack.com/api/users.profile.set'


# ============== HRMOS API Functions ==============

def get_hrmos_token():
    """HRMOS APIのトークンを取得"""
    try:
        response = requests.get(
            f"{HRMOS_API_BASE}/authentication/token",
            headers={
                'Authorization': f'Basic {HRMOS_API_SECRET}',
                'Content-Type': 'application/json'
            }
        )
        if response.status_code == 200:
            return response.json().get('token')
    except Exception as e:
        print(f"HRMOS token error: {e}")
    return None

def get_hrmos_users(token):
    """HRMOS のユーザー一覧を取得（修正版）"""
    try:
        users = []
        page = 1
        while True:
            response = requests.get(
                f"{HRMOS_API_BASE}/users",
                headers={
                    'Authorization': f'Token {token}',
                    'Content-Type': 'application/json'
                },
                params={'limit': 100, 'page': page}
            )
            if response.status_code == 200:
                data = response.json()
                # データが空、またはリストでない場合はループを抜ける
                if not data or len(data) == 0:
                    break
                
                users.extend(data)
                
                # 取得した件数が limit(100) より少なければ、それが最後のページ
                if len(data) < 100:
                    break
                
                # ちょうど100件の場合は次のページがある可能性があるので継続
                page += 1
            else:
                break
        return users
    except Exception as e:
        print(f"HRMOS users error: {e}")
    return []


def find_hrmos_user_by_email(token, email):
    """メールアドレスからHRMOSユーザーを検索"""
    users = get_hrmos_users(token)
    for user in users:
        if user.get('email') == email:
            return user
    return None


def get_today_work_output(token, user_id):
    """本日の勤怠データを取得"""
    try:
        # 日本時間（JST = UTC+9）で本日の日付を取得
        jst = timezone(timedelta(hours=9))
        today = datetime.now(jst).strftime('%Y-%m-%d')
        response = requests.get(
            f"{HRMOS_API_BASE}/work_outputs/daily/{today}",
            headers={
                'Authorization': f'Token {token}',
                'Content-Type': 'application/json'
            },
            params={'limit': 100}
        )
        if response.status_code == 200:
            data = response.json()
            for record in data:
                if record.get('user_id') == user_id:
                    return record
    except Exception as e:
        print(f"HRMOS work output error: {e}")
    return None


def is_already_checked_in(token, hrmos_user_id):
    """本日既に出勤打刻済みかどうか確認"""
    work_output = get_today_work_output(token, hrmos_user_id)
    if work_output:
        # start_at が設定されていれば出勤済み
        return work_output.get('start_at') is not None or work_output.get('stamping_start_at') is not None
    return False


def is_already_checked_out(token, hrmos_user_id):
    """本日既に退勤打刻済みかどうか確認"""
    work_output = get_today_work_output(token, hrmos_user_id)
    if work_output:
        # end_at が設定されていれば退勤済み
        return work_output.get('end_at') is not None or work_output.get('stamping_end_at') is not None
    return False


def hrmos_stamp(token, user_id, stamp_type):
    """HRMOS に打刻を登録
    stamp_type: 1=出勤, 2=退勤
    """
    try:
        # 日本時間（JST = UTC+9）で現在時刻を取得
        jst = timezone(timedelta(hours=9))
        now = datetime.now(jst).strftime('%Y-%m-%dT%H:%M:%S+09:00')
        response = requests.post(
            f"{HRMOS_API_BASE}/stamp_logs",
            headers={
                'Authorization': f'Token {token}',
                'Content-Type': 'application/json'
            },
            json={
                'user_id': user_id,
                'stamp_type': stamp_type,
                'datetime': now
            }
        )
        return response.status_code == 200
    except Exception as e:
        print(f"HRMOS stamp error: {e}")
    return False


# ============== Helper Functions ==============

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


def get_hrmos_status():
    """現在のHRMOS打刻状態を取得"""
    if 'user' not in session:
        return {'checked_in': False, 'checked_out': False, 'hrmos_user_id': None}
    
    user = session['user']
    hrmos_user_id = user.get('hrmos_user_id')
    
    if not hrmos_user_id:
        return {'checked_in': False, 'checked_out': False, 'hrmos_user_id': None}
    
    token = get_hrmos_token()
    if not token:
        return {'checked_in': False, 'checked_out': False, 'hrmos_user_id': hrmos_user_id}
    
    checked_in = is_already_checked_in(token, hrmos_user_id)
    checked_out = is_already_checked_out(token, hrmos_user_id)
    
    return {
        'checked_in': checked_in,
        'checked_out': checked_out,
        'hrmos_user_id': hrmos_user_id
    }


# ============== Routes ==============

@app.route('/')
def index():
    """メインページ"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    hrmos_status = get_hrmos_status()
    
    return render_template('index.html', 
                         user=user, 
                         client_ip=client_ip,
                         office_info=office_info,
                         other_locations=OTHER_LOCATIONS,
                         hrmos_status=hrmos_status)


@app.route('/login')
def login():
    """Slack OAuth認証開始"""
    redirect_uri = url_for('slack_callback', _external=True)
    
    auth_url = (
        f"{SLACK_AUTH_URL}"
        f"?client_id={SLACK_CLIENT_ID}"
        f"&user_scope=users:read,users:read.email,users.profile:write"
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
    
    # ユーザー情報を取得
    authed_user = token_data.get('authed_user', {})
    access_token = authed_user.get('access_token')
    user_id = authed_user.get('id')
    
    # users.info APIでユーザー詳細を取得
    user_info_response = requests.get(
        f"{SLACK_USER_INFO_URL}?user={user_id}",
        headers={'Authorization': f'Bearer {access_token}'}
    )
    
    user_info = user_info_response.json()
    
    if not user_info.get('ok'):
        return f"ユーザー情報取得エラー: {user_info.get('error')}", 400
    
    user_data = user_info.get('user', {})
    profile = user_data.get('profile', {})
    
    name = user_data.get('real_name') or user_data.get('name', '')
    email = profile.get('email', '')
    
    # メールドメインを確認
    if email and not email.endswith(f'@{ALLOWED_DOMAIN}'):
        return f"このアプリは @{ALLOWED_DOMAIN} のメールアドレスを持つユーザーのみ利用できます", 403
    
    # HRMOSユーザーIDを取得
    hrmos_user_id = None
    hrmos_token = get_hrmos_token()
    if hrmos_token and email:
        hrmos_user = find_hrmos_user_by_email(hrmos_token, email)
        if hrmos_user:
            hrmos_user_id = hrmos_user.get('id')
    
    # セッションにユーザー情報を保存
    session['user'] = {
        'id': user_id,
        'name': name,
        'email': email,
        'access_token': access_token,
        'hrmos_user_id': hrmos_user_id
    }
    
    return redirect(url_for('index'))


@app.route('/checkin', methods=['POST'])
@login_required
def checkin():
    """出勤チェックイン（オフィスから）"""
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    hrmos_status = get_hrmos_status()
    
    if not office_info:
        return render_template('index.html',
                             user=user,
                             client_ip=client_ip,
                             office_info=None,
                             other_locations=OTHER_LOCATIONS,
                             hrmos_status=hrmos_status,
                             message='現在のIPアドレスは登録されたオフィスのものではありません。下のボタンから勤務場所を選択してください。',
                             message_type='error')
    
    # Slackステータスを更新
    slack_response = requests.post(SLACK_PROFILE_SET_URL, 
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
    
    slack_result = slack_response.json()
    
    # HRMOS打刻（未打刻の場合のみ）
    hrmos_message = ""
    if user.get('hrmos_user_id') and not hrmos_status['checked_in']:
        token = get_hrmos_token()
        if token:
            if hrmos_stamp(token, user['hrmos_user_id'], 1):
                hrmos_message = " / HRMOS出勤打刻完了"
            else:
                hrmos_message = " / HRMOS打刻エラー"
    elif hrmos_status['checked_in']:
        hrmos_message = " / 勤務地を更新しました"
    
    # 状態を再取得
    hrmos_status = get_hrmos_status()
    
    if slack_result.get('ok'):
        message = f"{office_info['name']}で出勤しました{hrmos_message}"
        message_type = 'success'
    else:
        message = f"ステータス更新エラー: {slack_result.get('error')}"
        message_type = 'error'
    
    return render_template('index.html',
                         user=user,
                         client_ip=client_ip,
                         office_info=office_info,
                         other_locations=OTHER_LOCATIONS,
                         hrmos_status=hrmos_status,
                         message=message,
                         message_type=message_type)


@app.route('/checkin_other', methods=['POST'])
@login_required
def checkin_other():
    """オフィス外からのチェックイン（リモート・現場）"""
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    hrmos_status = get_hrmos_status()
    
    location_type = request.form.get('location_type')
    
    if location_type not in OTHER_LOCATIONS:
        return render_template('index.html',
                             user=user,
                             client_ip=client_ip,
                             office_info=office_info,
                             other_locations=OTHER_LOCATIONS,
                             hrmos_status=hrmos_status,
                             message='無効な勤務場所が選択されました',
                             message_type='error')
    
    location_info = OTHER_LOCATIONS[location_type]
    
    # Slackステータスを更新
    slack_response = requests.post(SLACK_PROFILE_SET_URL, 
        headers={
            'Authorization': f'Bearer {user["access_token"]}',
            'Content-Type': 'application/json'
        },
        json={
            'profile': {
                'status_text': location_info['status'],
                'status_emoji': location_info['emoji'],
                'status_expiration': 0
            }
        }
    )
    
    slack_result = slack_response.json()
    
    # HRMOS打刻（未打刻の場合のみ）
    hrmos_message = ""
    if user.get('hrmos_user_id') and not hrmos_status['checked_in']:
        token = get_hrmos_token()
        if token:
            if hrmos_stamp(token, user['hrmos_user_id'], 1):
                hrmos_message = " / HRMOS出勤打刻完了"
            else:
                hrmos_message = " / HRMOS打刻エラー"
    elif hrmos_status['checked_in']:
        hrmos_message = " / 勤務地を更新しました"
    
    # 状態を再取得
    hrmos_status = get_hrmos_status()
    
    if slack_result.get('ok'):
        message = f"{location_info['name']}で出勤しました{hrmos_message}"
        message_type = 'success'
    else:
        message = f"ステータス更新エラー: {slack_result.get('error')}"
        message_type = 'error'
    
    return render_template('index.html',
                         user=user,
                         client_ip=client_ip,
                         office_info=office_info,
                         other_locations=OTHER_LOCATIONS,
                         hrmos_status=hrmos_status,
                         message=message,
                         message_type=message_type)


@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    """退勤チェックアウト"""
    user = session['user']
    client_ip = get_client_ip()
    office_info = get_office_info(client_ip)
    hrmos_status = get_hrmos_status()
    
    # Slackステータスをクリア
    slack_response = requests.post(SLACK_PROFILE_SET_URL,
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
    
    slack_result = slack_response.json()
    
    # HRMOS退勤打刻
    hrmos_message = ""
    if user.get('hrmos_user_id'):
        if hrmos_status['checked_out']:
            hrmos_message = " / 既に退勤打刻済みです"
        else:
            token = get_hrmos_token()
            if token:
                if hrmos_stamp(token, user['hrmos_user_id'], 2):
                    hrmos_message = " / HRMOS退勤打刻完了"
                else:
                    hrmos_message = " / HRMOS打刻エラー"
    
    # 状態を再取得
    hrmos_status = get_hrmos_status()
    
    if slack_result.get('ok'):
        message = f'退勤しました。お疲れ様でした！{hrmos_message}'
        message_type = 'success'
    else:
        message = f"ステータス更新エラー: {slack_result.get('error')}"
        message_type = 'error'
    
    return render_template('index.html',
                         user=user,
                         client_ip=client_ip,
                         office_info=office_info,
                         other_locations=OTHER_LOCATIONS,
                         hrmos_status=hrmos_status,
                         message=message,
                         message_type=message_type)


@app.route('/logout')
def logout():
    """ログアウト"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/debug')
@login_required
def debug():
    """デバッグ用：HRMOS連携状況を確認"""
    user = session['user']
    
    # 日本時間
    jst = timezone(timedelta(hours=9))
    jst_now = datetime.now(jst)
    today_jst = jst_now.strftime('%Y-%m-%d')
    
    debug_info = {
        'slack_email': user.get('email'),
        'hrmos_user_id_in_session': user.get('hrmos_user_id'),
        'today_jst': today_jst,
        'current_time_jst': jst_now.strftime('%Y-%m-%d %H:%M:%S'),
    }
    
    # HRMOSトークン取得
    token = get_hrmos_token()
    debug_info['hrmos_token_obtained'] = token is not None
    
    if token and user.get('hrmos_user_id'):
        hrmos_user_id = user.get('hrmos_user_id')
        
        # 本日の勤怠データを取得
        try:
            debug_info['api_request_url'] = f"{HRMOS_API_BASE}/work_outputs/daily/{today_jst}"
            response = requests.get(
                f"{HRMOS_API_BASE}/work_outputs/daily/{today_jst}",
                headers={
                    'Authorization': f'Token {token}',
                    'Content-Type': 'application/json'
                },
                params={'limit': 100}
            )
            debug_info['work_outputs_status'] = response.status_code
            
            if response.status_code == 200:
                data = response.json()
                debug_info['work_outputs_count'] = len(data)
                
                # 該当ユーザーのデータを探す
                user_record = None
                for record in data:
                    if record.get('user_id') == hrmos_user_id:
                        user_record = record
                        break
                
                if user_record:
                    debug_info['user_record_found'] = True
                    debug_info['user_record_full'] = user_record  # レコード全体を表示
                    debug_info['start_at'] = user_record.get('start_at')
                    debug_info['stamping_start_at'] = user_record.get('stamping_start_at')
                    debug_info['end_at'] = user_record.get('end_at')
                    debug_info['stamping_end_at'] = user_record.get('stamping_end_at')
                    
                    # 判定ロジック
                    is_checked_in = user_record.get('start_at') is not None or user_record.get('stamping_start_at') is not None
                    debug_info['is_checked_in'] = is_checked_in
                else:
                    debug_info['user_record_found'] = False
                    
                    # 全レコードのuser_idを表示（デバッグ用）
                    debug_info['all_user_ids_in_response'] = [r.get('user_id') for r in data[:10]]
        except Exception as e:
            debug_info['error'] = str(e)
    
    import json
    formatted = json.dumps(debug_info, indent=2, ensure_ascii=False)
    return f"<html><body><h1>Debug Info</h1><pre>{formatted}</pre></body></html>"
@app.route('/status_list')
@login_required
def status_list():
    """全ユーザーのSlackステータス一覧"""
    
    # Bot Tokenで全ユーザーを取得
    users_response = requests.get(
        'https://slack.com/api/users.list',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'}
    )
    
    users_data = users_response.json()
    
    if not users_data.get('ok'):
        return f"ユーザー一覧取得エラー: {users_data.get('error')}", 400
    
    # ユーザー情報を整理
    user_statuses = []
    for member in users_data.get('members', []):
        # Botや削除済みユーザーは除外
        if member.get('is_bot') or member.get('deleted'):
            continue
        
        profile = member.get('profile', {})
        
        # @altenergy.co.jp のユーザーのみ表示
        email = profile.get('email', '')
        if not email.endswith('@altenergy.co.jp'):
            continue
        
        status_text = profile.get('status_text', '')
        status_emoji = profile.get('status_emoji', '')
        
        # ステータスから勤務地を判定
        location = '未設定'
        location_class = 'unknown'
        if '銀座' in status_text:
            location = '銀座'
            location_class = 'ginza'
        elif '立川' in status_text:
            location = '立川'
            location_class = 'tachikawa'
        elif 'リモート' in status_text:
            location = 'リモートワーク'
            location_class = 'remote'
        elif '現場' in status_text:
            location = '現場'
            location_class = 'site'
        elif status_text:
            location = '未設定'
            location_class = 'unknown'
        
        user_statuses.append({
            'slack_id': member.get('id'),
            'name': member.get('real_name') or member.get('name', ''),
            'email': email,
            'status_text': status_text,
            'status_emoji': status_emoji,
            'location': location,
            'location_class': location_class,
            'image': profile.get('image_48', ''),
        })
    
    # 名前順でソート
    user_statuses.sort(key=lambda x: x['name'])
    
    return render_template('status_list.html', users=user_statuses)
    @app.route('/send_dm', methods=['POST'])
@login_required
def send_dm():
    """指定したユーザーにDMを送信"""
    sender = session['user']
    target_user_id = request.form.get('target_user_id')
    
    if not target_user_id:
        return {'success': False, 'error': '送信先が指定されていません'}, 400
    
    # DMチャンネルを開く
    open_response = requests.post(
        'https://slack.com/api/conversations.open',
        headers={
            'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
            'Content-Type': 'application/json'
        },
        json={'users': target_user_id}
    )
    
    open_data = open_response.json()
    
    if not open_data.get('ok'):
        return {'success': False, 'error': open_data.get('error')}, 400
    
    channel_id = open_data['channel']['id']
    
    # メッセージを送信
    sender_name = sender.get('name', '誰か')
    message = f"{sender_name}より、呼び出しがありました。"
    
    msg_response = requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={
            'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
            'Content-Type': 'application/json'
        },
        json={
            'channel': channel_id,
            'text': message
        }
    )
    
    msg_data = msg_response.json()
    
    if msg_data.get('ok'):
        return {'success': True}
    else:
        return {'success': False, 'error': msg_data.get('error')}, 400
if __name__ == '__main__':
    app.run(debug=True, port=5000)
