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
DBX_APP_KEY = os.getenv("DBX_APP_KEY")
DBX_APP_SECRET = os.getenv("DBX_APP_SECRET")
DBX_REFRESH_TOKEN = os.getenv("DBX_REFRESH_TOKEN")

# --- SUPABASE OPAQUE KEY CONFIG ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY") 

def log_to_supabase(title, artist, status="success", progress=None, record_id=None):
    """Logs or updates the sync event in Supabase."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY: return None
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    payload = {"title": title, "artist": artist, "status": status}
    if progress is not None: payload["progress"] = int(progress)

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

def update_dropbox_inventory(dbx):
    """Sync the Dropbox file list to Supabase inventory table."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY: return
    try:
        files = []
        res = dbx.files_list_folder("/Music/Sync")
        files.extend([f.name for f in res.entries if isinstance(f, dropbox.files.FileMetadata)])
        while res.has_more:
            res = dbx.files_list_folder_continue(res.cursor)
            files.extend([f.name for f in res.entries if isinstance(f, dropbox.files.FileMetadata)])
        
        headers = {"apikey": SUPABASE_SECRET_KEY, "Authorization": f"Bearer {SUPABASE_SECRET_KEY}", "Content-Type": "application/json"}
        requests.delete(f"{SUPABASE_URL}/rest/v1/dropbox_inventory?id=gt.0", headers=headers)
        payload = [{"name": f} for f in files[:100]]
        if payload: requests.post(f"{SUPABASE_URL}/rest/v1/dropbox_inventory", headers=headers, json=payload)
    except Exception as e: print(f"⚠️ Inventory update error: {e}")

def fetch_tracks():
    """Fetch track entries using yt-dlp."""
    target_url = SC_PLAYLIST_URL
    if not target_url or "REPLACE_WITH_YOUR" in target_url:
        target_url = f"https://soundcloud.com/users/{SC_USER_ID}/likes"

    print(f"🔍 Fetching latest tracks from: {target_url}")
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'playlist_items': '1-50', # Fixed range: 1 to 50
        'no_cache_dir': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(target_url, download=False)
            return result.get('entries', [])
    except Exception as e:
        print(f"❌ Discovery error: {e}")
        return None

def sync_to_dropbox():
    # 1. IMMEDIATE LOG to clear "Starting" state on dash
    rid = log_to_supabase("Sync Initialized", "Connecting to SoundCloud...", "info")
    
    dbx = dropbox.Dropbox(oauth2_refresh_token=DBX_REFRESH_TOKEN, app_key=DBX_APP_KEY, app_secret=DBX_APP_SECRET)
    items = fetch_tracks()

    if items is None or not items:
        log_to_supabase("Sync Complete", "No new tracks found", "idle", record_id=rid)
        update_dropbox_inventory(dbx)
        return

    log_to_supabase("Sync Active", f"Found {len(items)} tracks", "info", record_id=rid)

    for i, item in enumerate(items):
        url = item.get('url')
        if not url: continue
        if url.startswith('/'): url = f"https://soundcloud.com{url}"

        # Fetch metadata for clean naming
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'no_cache_dir': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', 'Unknown Title')
                artist = info.get('uploader', 'Unknown Artist')
        except:
            title = item.get('title', 'Unknown Title')
            artist = item.get('uploader', 'Unknown Artist')

        # Clean filename: Title - Artist
        clean_name = "".join([c for c in f"{title} - {artist}" if c.isalnum() or c in (' ', '-', '_')]).strip()
        dbx_path = f"/Music/Sync/{clean_name}.mp3"

        try:
            dbx.files_get_metadata(dbx_path)
            log_to_supabase(title, artist, "exists", progress=100, record_id=rid)
        except:
            log_to_supabase(title, artist, status="downloading", progress=0, record_id=rid)
            
            last_sent_p = 0
            def progress_hook(d):
                nonlocal last_sent_p
                if d['status'] == 'downloading':
                    p = 0
                    if d.get('total_bytes'): p = (d['downloaded_bytes'] / d['total_bytes']) * 100
                    elif d.get('total_bytes_estimate'): p = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                    else:
                        p_str = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '0%'))
                        try: p = float(p_str.replace('%','').strip())
                        except: pass
                    p = int(p)
                    if p >= last_sent_p + 10 or p == 100:
                        log_to_supabase(title, artist, "downloading", progress=p, record_id=rid)
                        last_sent_p = p

            ydl_opts = {'outtmpl': 'temp_track.mp3', 'format': 'bestaudio/best', 'quiet': True, 'no_warnings': True, 'progress_hooks': [progress_hook], 'no_cache_dir': True}
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
                log_to_supabase(title, artist, "uploading", progress=100, record_id=rid)
                with open("temp_track.mp3", "rb") as f:
                    dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)
            except Exception as e:
                log_to_supabase(title, artist, "error", record_id=rid)
            finally:
                if os.path.exists("temp_track.mp3"): os.remove("temp_track.mp3")

    log_to_supabase("Sync Complete", f"{len(items)} tracks processed", "success", progress=100, record_id=rid)
    update_dropbox_inventory(dbx)

if __name__ == "__main__":
    if not all([SC_CLIENT_ID, SC_USER_ID, DBX_REFRESH_TOKEN]): print("Missing ENV vars")
    else: sync_to_dropbox()