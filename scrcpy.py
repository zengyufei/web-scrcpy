from threading import Thread
import subprocess
import socket
import time

ADB_PATH = "adb"
SCRCPY_SERVER_PATH = "scrcpy-server"
DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"
LOCAL_PORT = 5555

class Scrcpy:
    def __init__(self):
        self.video_socket = None
        self.audio_socket = None
        self.control_socket = None

        self.android_thread = None
        self.video_thread = None
        self.audio_thread = None
        self.control_thread = None
        self.android_process = None

    def push_server_to_device(self):
        print("Pushing scrcpy-server.jar to device...")
        result = subprocess.run([ADB_PATH, "push", SCRCPY_SERVER_PATH, DEVICE_SERVER_PATH], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error pushing server: {result.stderr}")
            return False
        return True

    def setup_adb_forward(self):
        print(f"Setting up ADB forward: tcp:{LOCAL_PORT} -> localabstract:scrcpy")
        subprocess.run([ADB_PATH, "forward", f"tcp:{LOCAL_PORT}", "localabstract:scrcpy"], check=True)

    def start_server(self):
        print("Starting scrcpy server in background...")
        cmd = [
            ADB_PATH, "shell",
            f"CLASSPATH={DEVICE_SERVER_PATH} app_process / com.genymobile.scrcpy.Server 3.1"
            f" tunnel_forward=true log_level=INFO"
            f" video_bit_rate={self.video_bit_rate}"
            f" video_codec=h264"
            f" audio=false"
            f" max_fps={self.max_fps}"
            + (f" max_size={self.max_size}" if self.max_size > 0 else "")
        ]
        self.android_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while not self.stop:
            stderr_line = self.android_process.stderr.readline().decode().strip()
            if not stderr_line:
                break
            if stderr_line:
                print(f"Server: {stderr_line}")
        self.android_process.wait()
        print("Server stopped")

    def receive_video_data(self):
        print("Receiving video data (H.264)...")
        self.video_socket.recv(1)
        while not self.stop:
            # 256KB 缓冲区：1080p I 帧通常 50-200KB，减少拆包次数
            data = self.video_socket.recv(262144)
            if not data:
                break
            self.video_callback(data)
        print("Video data reception stopped")

    def receive_audio_data(self):
        print("Receiving audio data...")
        self.audio_socket.recv(1)
        while not self.stop:
            data = self.audio_socket.recv(1024)
            if not data:
                break
        print("Audio data reception stopped")

    def handle_control_conn(self):
        print("Control connection established (idle)...")
        self.control_socket.recv(1)
        while not self.stop:
            data = self.control_socket.recv(1024)
            if not data:
                break
            print("Control Mesg:", data)
        print("Control connection stopped")

    def scrcpy_start(self, video_callback, video_bit_rate, max_fps=60, max_size=0):
        self.video_bit_rate = video_bit_rate
        self.max_fps = max_fps
        self.max_size = max_size  # 0 表示不限制，>0 表示采集端最大边长（px）
        self.video_callback = video_callback
        self.stop = False

        result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
        if "device" not in result.stdout:
            print("No device found. Please connect your Android device via USB.")
            return
        print(result.stdout)

        if not self.push_server_to_device():
            print("Failed to push server files to device.")
            return

        self.setup_adb_forward()
        self.android_thread = Thread(target=self.start_server, daemon=True)
        self.android_thread.start()
        time.sleep(1)

        # video connection
        self.video_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # TCP_NODELAY: 禁用 Nagle 算法，视频数据立即发送不等待累积
        self.video_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # SO_RCVBUF: 接收缓冲区对齐 recv() 读取大小（256KB）
        self.video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        self.video_socket.connect(('localhost', LOCAL_PORT))
        print("Video connection established")

        # audio=false: scrcpy server 只开放 video + control 两个连接，跳过 audio socket

        # control connection
        self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # TCP_NODELAY: 控制指令必须即时送达，禁用 Nagle 延迟
        self.control_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.control_socket.connect(('localhost', LOCAL_PORT))
        print("Control connection established")

        self.video_thread = Thread(target=self.receive_video_data, daemon=True)
        self.control_thread = Thread(target=self.handle_control_conn, daemon=True)
        self.video_thread.start()
        self.control_thread.start()
        print("Background tasks started")

    def scrcpy_stop(self):
        print("Stopping Scrcpy")
        self.stop = True
        # Close sockets defensively; they may already be closed if the client dropped
        sockets = [self.video_socket, self.audio_socket, self.control_socket]
        for sock in sockets:
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass

        # Wait for threads to exit, but don't block forever
        for thread in [self.video_thread, self.control_thread]:
            if thread:
                thread.join(timeout=0.5)

        if self.android_process:
            try:
                self.android_process.terminate()
            except Exception:
                pass
        if self.android_thread:
            self.android_thread.join(timeout=0.5)
        print("Scrcpy stopped")

    def scrcpy_send_control(self, data):
        try:
            self.control_socket.send(data)
        except Exception as e:
            print(f"scrcpy_send_control error: {e}")