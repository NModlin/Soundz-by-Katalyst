import subprocess
import json
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app) # Allow your UI to talk to this API

# Configuration
FIFO_PATH = "/tmp/snapfifo"
SNAPCAST_RPC_URL = "http://localhost:1705/jsonrpc"
STUDIO_ID = "soundz-studio-node"

# Internal State (In-memory for 2026 performance)
active_queue = []
current_track = None

def run_command(cmd):
    """Executes system commands for PipeWire/Snapcast"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception as e:
        return str(e)

@app.route('/api/search', methods=['GET'])
def search_youtube():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    # Use yt-dlp to get search results (Top 5)
    # 2026 Best Practice: JSON output for clean parsing
    cmd = f'yt-dlp "ytsearch5:{query}" --dump-json --flat-playlist --skip-download'
    output = run_command(cmd)
    
    results = []
    for line in output.split('\n'):
        if line:
            data = json.loads(line)
            results.append({
                "id": data.get("id"),
                "title": data.get("title"),
                "uploader": data.get("uploader"),
                "duration": data.get("duration_string"),
                "url": f"https://www.youtube.com/watch?v={data.get('id')}"
            })
    
    return jsonify(results)

@app.route('/api/queue', methods=['POST'])
def add_to_queue():
    data = request.json
    track = {
        "title": data.get('title'),
        "url": data.get('url'),
        "user": data.get('user'),
        "id": int(time.time())
    }
    active_queue.append(track)
    
    # If nothing is playing, trigger the broadcast engine
    if not current_track:
        play_next()
        
    return jsonify({"status": "queued", "track": track})

def play_next():
    global current_track
    if not active_queue:
        current_track = None
        return

    current_track = active_queue.pop(0)
    
    # Launch the stream pipe
    # This uses yt-dlp to stream audio-only directly into your Snapcast FIFO
    cmd = f'yt-dlp -f bestaudio -o - "{current_track["url"]}" > {FIFO_PATH} &'
    subprocess.Popen(cmd, shell=True)
    
    print(f"Now Broadcasting: {current_track['title']} for {current_track['user']}")

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        "now_playing": current_track,
        "queue": active_queue,
        "node": STUDIO_ID
    })

@app.route('/api/volume', methods=['POST'])
def set_volume():
    val = request.json.get('volume')
    # Use wpctl to set PipeWire volume on the master sink
    run_command(f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {val}%")
    return jsonify({"status": "updated", "volume": val})

if __name__ == '__main__':
    # Ensure FIFO exists before starting
    if not os.path.exists(FIFO_PATH):
        os.mkfifo(FIFO_PATH)
    
    # Run on port 8080 (distinct from Snapcast's 1780)
    app.run(host='0.0.0.0', port=8080)