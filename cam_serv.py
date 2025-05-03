from flask import Flask, Response, abort
import threading
import time
import cv2

app = Flask(__name__)

# Hardcoded MJPEG camera URLs and their corresponding ports
CAMERA_CONFIG = {
    "hallcam": {"url": "http://192.168.1.154:8085", "stream_port": 4999,},
    "kitchencam": {"url": "http://192.168.1.98:8085", "stream_port": 4999},
    "livingroom": {"url": "http://192.168.1.81:8085", "stream_port": 4999},
}

# Global dictionary to manage camera streams
camera_streams = {}

class MJPEGStream:
    def __init__(self, camera_url):
        self.camera_url = camera_url
        self.frame = None
        self.connected = False
        self.lock = threading.Lock()
        self.running = False
        self.clients = 0  # Track the number of connected clients
        self.thread = None  # Thread will be started dynamically
        self.fps = 0
        self.last_frame_time = time.perf_counter()
        
        print(f"[INFO] MJPEGStream initialized for camera: {self.camera_url}")

    def _update_stream(self):
        while self.running:
            try:
                print(f"[DEBUG] Attempting to connect to camera: {self.camera_url}")
                cap = cv2.VideoCapture(self.camera_url)
                retry = 0
                if not cap.isOpened():
                    if retry < 15:
                        retry += 1
                        time.sleep(0.2)
                    continue
                if cap.isOpened():
                    print(f"[INFO] Camera connected: {self.camera_url}")
                    
                else:
                    print(f"[ERROR] Camera not connected: {self.camera_url}")
                    self.connected = False
                    self.isStreaming = False
                    break
                self.connected = True
                noclientsThrottle = 25
                while self.running:
                    ret, frame = cap.read()
                    if not ret:
                        print(f"[ERROR] Failed to read frame from camera: {self.camera_url}")
                        self.connected = False
                        break

                    with self.lock:
                        _, encoded_frame = cv2.imencode('.jpg', frame)
                        self.frame = encoded_frame.tobytes()
                        self.last_frame_time = time.perf_counter()
                    if self.clients == 0:
                        noclientsThrottle -= 1
                        if noclientsThrottle <= 0:
                            print(f"[INFO] No clients connected, stopping stream for camera: {self.camera_url}")
                            self.connected = False
                            self.isStreaming = False
                            self.running = False
                            break

                cap.release()
                print(f"[INFO] Disconnected from camera: {self.camera_url} , client count: {self.clients}")
                self.connected = False

            except Exception as e:
                print(f"[ERROR] Exception in camera stream {self.camera_url}: {e} clients count {self.clients}")
                self.connected = False

    def start_stream(self):
        if self.thread is None or not self.thread.is_alive():
            print(f"[INFO] Starting stream for camera: {self.camera_url}")
            self.running = True
            self.thread = threading.Thread(target=self._update_stream, daemon=True)
            self.thread.start()

    def stop_stream(self):
        print(f"[INFO] Stopping stream for camera: {self.camera_url}")
        self.running = False
        if self.thread is not None:
            self.thread.join()
            self.thread = None

    def get_frame(self):
        with self.lock:
            return self.frame

# Route to handle the MJPEG stream
@app.route('/camera/<camera_id>')
def camera_stream(camera_id):
    camera_id = camera_id.lower()
    if camera_id not in CAMERA_CONFIG:
        print(f"[ERROR] Camera ID {camera_id} not found in config")
        abort(404, f"Camera  {camera_id}  not found in config")

    if camera_id not in camera_streams:
        print(f"[INFO] Initializing stream for camera ID: {camera_id}")
        camera_streams[camera_id] = MJPEGStream(CAMERA_CONFIG[camera_id]["url"])

    camera = camera_streams[camera_id]
    camera.clients += 1
    if not camera.running:
        camera.start_stream()
        print(f"[INFO] Starting stream for camera: {camera_id} for snapshot.......")

    def generate():
        retry = 0
        try:
            while True:
                if not camera.connected:
                    print(f"[ERROR] Camera ID {camera_id} not connected")
                    if retry < 15:
                        retry += 1
                        time.sleep(0.2)
                    else:
                        abort(404, "Camera not connected")
                frame = camera.get_frame()
                if frame is not None:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    # calculate fps
                if camera.frame is not None:
                    camera.fps = 1 / (time.perf_counter() - camera.last_frame_time)
                    camera.last_frame_time = time.perf_counter()
                #print (f"[INFO] FPS: {camera.fps:.2f}") 
                time.sleep(0.06)  # Adjust the sleep time as needed
        finally:
            camera.clients -= 1
            print(f"[INFO] Client disconnected from camera ID: {camera_id}. Total clients: {camera.clients}")
            if camera.clients == 0:
                camera.stop_stream()

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# Route to handle snapshot requests
@app.route('/snapshot/<camera_id>')
def snapshot(camera_id):
    print ("Snapshot request")
    camera_id = camera_id.lower()
    if camera_id not in CAMERA_CONFIG:
        print(f"[ERROR] Camera ID {camera_id} not found in config for snapshots")
        abort(404 , "Camera not found in config for snapshots")

    if camera_id not in camera_streams:
        # Initialize the camera stream if not already started
        print(f"[INFO] Initializing stream for camera ID: {camera_id} for snapshot")
        camera_streams[camera_id] = MJPEGStream(CAMERA_CONFIG[camera_id]["url"])

    camera = camera_streams[camera_id]
    if not camera.running:
        print(f"[INFO] Starting stream for camera: {camera_id} for snapshot.......")
        camera.start_stream()
        time.sleep(0.2)
    print(f"handling snapshot")
    print (CAMERA_CONFIG[camera_id]["url"])
    while not camera.connected:
        retry = 0
        if retry < 15:
            retry += 1
            time.sleep(0.2)
        else:
            print(f"[ERROR] Camera ID {camera_id} not connected for snapshot")
            abort(404, "Camera not connected")
            break
    print ("Camera connected")
    frame = camera.get_frame()
    if frame is None:
        print(f"[ERROR] Failed to get frame from camera ID: {camera_id} for snapshot")
        abort(404, "Failed to get frame")

    return Response(frame, mimetype='image/jpeg')

if __name__ == '__main__':
    # Start Flask servers for each camera on its assigned ports
    threads = []
    for camera_id, config in CAMERA_CONFIG.items():
        def run_server(camera_id, _port):
            app_stream = Flask(__name__)

            @app_stream.route('/camera/<camera_id>')
            def camera_stream_route(camera_id=camera_id):
                return camera_stream(camera_id)
            @app_stream.route('/snapshot/<camera_id>')
            def snapshot_route(camera_id=camera_id):
                return snapshot(camera_id)
            print ("Stream added at port ", _port)
            print("Snapshot added at camera_id  ", camera_id)
            app_stream.run(host='0.0.0.0', port=_port, threaded=True, use_reloader=False)
            
        thread = threading.Thread(target=run_server, args=(camera_id, config["stream_port"]), daemon=True)
        threads.append(thread)
        thread.start()
        

    # Keep the main thread alive
    for thread in threads:
        thread.join()
