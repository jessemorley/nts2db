import requests
import dropbox
import yt_dlp
import os
import json
import re
from datetime import datetime

# SoundCloud & Dropbox Configuration
SC_CLIENT_ID = os.getenv("SC_CLIENT_ID")
SC_USER_ID = os.getenv("SC_USER_ID")
SC_OAUTH_TOKEN = os.getenv("SC_OAUTH_TOKEN")
# --- HARDCODE YOUR PLAYLIST URL HERE ---
SC_PLAYLIST_URL = os.getenv("SC_PLAYLIST_URL") or "https://soundcloud.com/standarmorley/sets/apple-watch"
# ---------------------------------------
# NOTE: Ensure your Supabase table has a 'progress' column:
# ALTER TABLE sync_history ADD COLUMN progress INT DEFAULT 0;
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
        payload["progress"] = int(progress)

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
        else:
            print(f"🔍 Supabase Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"Supabase log exception: {e}")
    return None

def get_dbx_client():
    return dropbox.Dropbox(
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET
    )

def fetch_liked_tracks():
    """Fetch track entries using yt-dlp."""
    target_url = SC_PLAYLIST_URL
    if not target_url or "REPLACE_WITH_YOUR" in target_url:
        target_url = f"https://soundcloud.com/users/{SC_USER_ID}/likes"
    
    print(f"🔍 Discovering: {target_url}")
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
                return result['entries']
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

    # One log card per job: Start with activity and reuse this ID
    rid = log_to_supabase("Sync Activity", f"{len(items)} tracks found", "info")
    print(f"🔍 Found {len(items)} tracks. Using Record ID: {rid}")

    for i, item in enumerate(items):
        url = item.get('url')
        if not url: continue
        if url.startswith('/'): url = f"https://soundcloud.com{url}"

        # Fetch metadata for clean naming
        title = item.get('title', 'Unknown Title')
        artist = item.get('uploader', 'Unknown Artist')
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', title)
                artist = info.get('uploader', artist)
        except Exception as e:
            print(f"⚠️ Could not fetch full metadata: {e}")

        print(f"📥 Downloading ({i+1}/{len(items)}): {title}...")
        log_to_supabase(title, artist, status="downloading", progress=0, record_id=rid)
        
        last_sent_p = 0
        def progress_hook(d):
            nonlocal last_sent_p
            if d['status'] == 'downloading':
                # Extract percentage from yt-dlp dict
                p = 0
                if d.get('total_bytes'):
                    p = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif d.get('total_bytes_estimate'):
                    p = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    # Fallback to string parsing if bytes are missing
                    p_str = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '0%'))
                    try: p = float(p_str.replace('%','').strip())
                    except: pass
                
                p = int(p)
                if p >= last_sent_p + 10 or p == 100:
                    log_to_supabase(title, artist, "downloading", progress=p, record_id=rid)
                    last_sent_p = p

        ydl_opts = {
            'outtmpl': 'temp_track.mp3',
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [progress_hook],
        }

        try:
            clean_name = "".join([c for c in f"{title} - {artist}" if c.isalnum() or c in (' ', '-', '_')]).strip()
            dbx_path = f"/Music/Sync/{clean_name}.mp3"

            try:
                dbx.files_get_metadata(dbx_path)
                print(f"Skipping {title} - exists.")
            except:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                print(f"📤 Uploading: {title}...")
                log_to_supabase(title, artist, "uploading", progress=100, record_id=rid)
                
                with open("temp_track.mp3", "rb") as f:
                    dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)

                print(f"✅ Success: {title}")

        except Exception as e:
            print(f"❌ Failed to sync {title}: {e}")
            log_to_supabase(title, artist, "error", record_id=rid)
        finally:
            if os.path.exists("temp_track.mp3"):
                os.remove("temp_track.mp3")

    # Final mark as success for the entire job
    log_to_supabase(f"Sync Complete", f"{len(items)} tracks processed", "success", progress=100, record_id=rid)

if __name__ == "__main__":
    if not all([SC_CLIENT_ID, SC_USER_ID, DBX_REFRESH_TOKEN]):
        print("Missing critical environment variables.")
    else:
        sync_to_dropbox()