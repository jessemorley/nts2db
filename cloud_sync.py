import requests
import dropbox
import yt_dlp
import os
import json
from datetime import datetime

# SoundCloud & Dropbox Configuration
SC_CLIENT_ID = os.getenv("SC_CLIENT_ID")
SC_USER_ID = os.getenv("SC_USER_ID")
SC_OAUTH_TOKEN = os.getenv("SC_OAUTH_TOKEN")
# --- HARDCODE YOUR PLAYLIST URL HERE ---
SC_PLAYLIST_URL = os.getenv("SC_PLAYLIST_URL") or "https://soundcloud.com/standarmorley/sets/apple-watch"
# ---------------------------------------
DBX_APP_KEY = os.getenv("DBX_APP_KEY")
DBX_APP_SECRET = os.getenv("DBX_APP_SECRET")
DBX_REFRESH_TOKEN = os.getenv("DBX_REFRESH_TOKEN")

# --- SUPABASE OPAQUE KEY CONFIG ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY") 

def log_to_supabase(title, artist, status="success", progress=None, record_id=None):
    """Logs or updates the sync event in Supabase."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print("Supabase configuration missing.")
        return None

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    payload = {
        "title": title,
        "artist": artist,
        "status": status
    }
    if progress is not None:
        payload["progress"] = progress

    try:
        if record_id:
            url = f"{SUPABASE_URL}/rest/v1/sync_history?id=eq.{record_id}"
            response = requests.patch(url, headers=headers, json=payload)
        else:
            url = f"{SUPABASE_URL}/rest/v1/sync_history"
            response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code in [200, 201]:
            data = response.json()
            return data[0].get('id') if data else None
    except Exception as e:
        print(f"Supabase log error: {e}")
    return None

def get_dbx_client():
    return dropbox.Dropbox(
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET
    )

def fetch_liked_tracks():
    """Fetch the 10 most recent likes (or tracks from a playlist) using yt-dlp."""
    target_url = SC_PLAYLIST_URL
    if not target_url or "REPLACE_WITH_YOUR" in target_url:
        target_url = f"https://soundcloud.com/users/{SC_USER_ID}/likes"
        print(f"🔍 SC_PLAYLIST_URL not set. Falling back to likes: {target_url}")
    else:
        print(f"🔍 Targeting playlist: {target_url}")

    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'playlist_items': '1,10',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(target_url, download=False)
            if 'entries' in result:
                tracks = result['entries']
                print(f"🔍 Found {len(tracks)} tracks via yt-dlp.")
                log_to_supabase("Sync Activity", f"{len(tracks)} tracks found", "info")
                return tracks
            return []
    except Exception as e:
        print(f"❌ yt-dlp discovery error: {e}")
        return None

def sync_to_dropbox():
    dbx = get_dbx_client()
    items = fetch_liked_tracks()

    if items is None:
        return 

    if not items:
        print("No tracks found. Logging heartbeat...")
        log_to_supabase("System Check", "No new tracks found", "idle")
        return

    for track in items:
        title = track.get('title') or track.get('url', 'Unknown Title')
        artist = track.get('uploader') or 'Unknown Artist'
        url = track.get('url')
        if not url: continue

        if url.startswith('/'):
            url = f"https://soundcloud.com{url}"

        clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
        dbx_path = f"/Music/Sync/{clean_title}.mp3"

        try:
            dbx.files_get_metadata(dbx_path)
            print(f"Skipping {title} - exists.")
        except:
            print(f"📥 Downloading: {title}...")
            
            # Initial log for the track
            rid = log_to_supabase(title, artist, status="downloading", progress=0)
            
            last_p = 0
            def progress_hook(d):
                nonlocal last_p
                if d['status'] == 'downloading':
                    p_str = d.get('_percent_str', '0%').replace('%','').strip()
                    try:
                        p = int(float(p_str))
                        if p >= last_p + 10 or p == 100:
                            log_to_supabase(title, artist, "downloading", progress=p, record_id=rid)
                            last_p = p
                    except: pass

            ydl_opts = {
                'outtmpl': 'temp_track.mp3',
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [progress_hook],
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                print(f"📤 Uploading: {title}...")
                log_to_supabase(title, artist, "uploading", progress=100, record_id=rid)
                
                with open("temp_track.mp3", "rb") as f:
                    dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)

                log_to_supabase(title, artist, "success", record_id=rid)
                print(f"✅ Success: {title}")

            except Exception as e:
                print(f"❌ Failed to sync {title}: {e}")
                log_to_supabase(title, artist, "error", record_id=rid)
            finally:
                if os.path.exists("temp_track.mp3"):
                    os.remove("temp_track.mp3")

if __name__ == "__main__":
    if not all([SC_CLIENT_ID, SC_USER_ID, DBX_REFRESH_TOKEN]):
        print("Missing critical environment variables.")
    else:
        sync_to_dropbox()