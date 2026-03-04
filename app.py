from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, send
from scrcpy import Scrcpy
import argparse
import queue
import os
# Force inclusion of simple_websocket for threading async_mode in bundled binary
import simple_websocket  # noqa: F401

scpy_ctx = None
client_sid = None        # 单连接模式使用
client_sids = set()      # 多连接模式使用
MULTI_VIEW = False       # 通过 --multi 开启
message_queue = queue.Queue()
video_bit_rate = "4000000"  # 4Mbps，2.4G WiFi 友好
MAX_FPS = 60
MAX_SIZE = 720  # 限制采集端最大边长（px），0 不限制
PASSWORD = ""  # 空字符串表示不启用认证
# 存储首个客户端解析后的视频流配置，发给晚入的客户端
stream_settings = None

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)  # 每次重启生成新密钥，旧 session 自动失效
# In a bundled binary we likely don't have eventlet/gevent installed; force threading.
socketio = SocketIO(
    app,
    async_mode="threading",
    # H.264 数据已压缩，禁用 socket.io 压缩避免浪费 CPU
    compression_threshold=10 * 1024 * 1024,  # 10MB 阈值 = 实际不压缩
    cors_allowed_origins="*",
    # 弱网环境适当加大心跳超时，避免频繁断连
    ping_timeout=60,
    ping_interval=25,
)



def is_authenticated():
    """判断当前请求是否已通过密码认证。不启用认证时始终返回 True。"""
    if not PASSWORD:
        return True
    return session.get('authenticated') is True


@app.route('/')
def index():
    if not is_authenticated():
        return redirect(url_for('login'))
    return render_template('index.html', auth_enabled=bool(PASSWORD))


@app.route('/login', methods=['GET'])
def login():
    if not PASSWORD:
        return redirect(url_for('index'))
    if is_authenticated():
        return redirect(url_for('index'))
    return render_template('login.html', error=None)


@app.route('/login', methods=['POST'])
def login_post():
    pwd = request.form.get('password', '')
    if pwd == PASSWORD:
        session['authenticated'] = True
        return redirect(url_for('index'))
    return render_template('login.html', error='密码错误，请重试')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


def video_send_task():
    """
    批量合并发送。单连接模式单播，多连接模式广播。
    """
    global client_sid, client_sids
    MAX_BATCH = 16
    while True:
        # 判断是否还有活跃客户端
        if MULTI_VIEW:
            if not client_sids:
                break
        else:
            if client_sid is None:
                break
        try:
            first = message_queue.get(timeout=0.01)
            chunks = [first]
            for _ in range(MAX_BATCH - 1):
                try:
                    chunks.append(message_queue.get_nowait())
                except queue.Empty:
                    break
            payload = b''.join(chunks) if len(chunks) > 1 else chunks[0]
            if MULTI_VIEW:
                # 广播给所有连接的客户端
                for sid in list(client_sids):
                    socketio.emit('video_data', payload, to=sid)
            else:
                socketio.emit('video_data', payload, to=client_sid)
        except queue.Empty:
            socketio.sleep(0.002)
        except Exception as e:
            print(f"Error sending data: {e}")
    print("video_send_task stopped")

def send_video_data(data):
    message_queue.put(data)

@socketio.on('connect')
def handle_connect(auth=None):
    global scpy_ctx, client_sid, client_sids, stream_settings
    print(f'Client connected: {request.sid} (multi={MULTI_VIEW})')

    if not is_authenticated():
        print('Reject unauthenticated WebSocket connection')
        return False

    if MULTI_VIEW:
        # 多连接模式：允许任意多人加入，首个客户端启动 scrcpy
        client_sids.add(request.sid)
        if scpy_ctx is None:
            # 第一个观看者连入，启动 scrcpy
            stream_settings = None
            scpy_ctx = Scrcpy()
            scpy_ctx.scrcpy_start(send_video_data, video_bit_rate, MAX_FPS, MAX_SIZE)
            socketio.start_background_task(video_send_task)
            print(f'scrcpy started (first viewer)')
        else:
            # 晚入者：直接发送纯配置数据
            if stream_settings:
                socketio.emit('init_data', stream_settings, to=request.sid)
                print(f'Sent JSON stream_settings to late viewer {request.sid}')
        print(f'Viewer joined, total={len(client_sids)}')
    else:
        # 单连接模式：已有连接则拒绝
        if scpy_ctx is not None:
            print(f'Reject: single-connection mode, already occupied')
            return False
        client_sid = request.sid
        scpy_ctx = Scrcpy()
        scpy_ctx.scrcpy_start(send_video_data, video_bit_rate, MAX_FPS, MAX_SIZE)
        socketio.start_background_task(video_send_task)
        print(f'Connected (single mode): {request.sid}')


@socketio.on('disconnect')
def handle_disconnect(reason=None):
    global scpy_ctx, client_sid, client_sids
    print(f'Client disconnected: {request.sid}, reason={reason}')

    if MULTI_VIEW:
        client_sids.discard(request.sid)
        print(f'Viewer left, remaining={len(client_sids)}')
        if not client_sids:
            # 最后一个要离开才停止 scrcpy
            if scpy_ctx is not None:
                try:
                    scpy_ctx.scrcpy_stop()
                except Exception as e:
                    print(f'scrcpy_stop failed: {e}')
                scpy_ctx = None
            print('scrcpy stopped (no viewers left)')
    else:
        client_sid = None
        if scpy_ctx is not None:
            try:
                scpy_ctx.scrcpy_stop()
            except Exception as e:
                print(f'scrcpy_stop failed: {e}')
            scpy_ctx = None
        print('scrcpy cleanup done')


@socketio.on('control_data')
def handle_control_data(data):
    global scpy_ctx
    if scpy_ctx is not None:
        scpy_ctx.scrcpy_send_control(data)

@socketio.on('set_init_data')
def handle_set_init_data(data):
    global stream_settings
    stream_settings = data
    print("Received and saved stream_settings from first viewer")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Web server for scrcpy')
    parser.add_argument('--video_bit_rate', default="4000000",
                        help='scrcpy 视频码率（bps），默认 4Mbps，2.4G WiFi 推荐 2000000-4000000')
    parser.add_argument('--max_fps', type=int, default=60, help='scrcpy 最大帧率，默认 60')
    parser.add_argument('--max_size', type=int, default=720,
                        help='scrcpy 采集端最大边长（px），0 不限制，默认 720（2.4G WiFi 节省带宽）')
    parser.add_argument('--port', type=int, default=5000, help='port to bind the web server to')
    parser.add_argument('--password', default="", help='访问密码（留空则不启用认证）')
    parser.add_argument('--multi', action='store_true',
                        help='启用多连接模式：允许多个浏览器同时观看同一台手机（默认关）')
    args = parser.parse_args()
    video_bit_rate = args.video_bit_rate
    MAX_FPS = args.max_fps
    MAX_SIZE = args.max_size
    PASSWORD = args.password
    MULTI_VIEW = args.multi
    if PASSWORD:
        print(f"[Auth] 密码保护已启用")
    else:
        print(f"[Auth] 未设置密码，认证已关闭")
    print(f"[Mode] {'multi-viewer (多连接)' if MULTI_VIEW else 'single-connection (单连接，默认)'}")
    
    # allow_unsafe_werkzeug=True 是为了允许新版 Flask-SocketIO 在生产环境IP下使用内置 threading 服务器
    socketio.run(app, host='0.0.0.0', port=args.port, allow_unsafe_werkzeug=True)