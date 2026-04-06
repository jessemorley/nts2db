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
DBX_APP_KEY = os.getenv("DBX_APP_KEY")
DBX_APP_SECRET = os.getenv("DBX_APP_SECRET")
DBX_REFRESH_TOKEN = os.getenv("DBX_REFRESH_TOKEN")

# --- SUPABASE OPAQUE KEY CONFIG ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY") 

def log_to_supabase(title, artist, status="success"):
    """Logs the sync event or status to Supabase."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print("Supabase configuration missing (SUPABASE_URL or SUPABASE_SECRET_KEY).")
        return

    url = f"{SUPABASE_URL}/rest/v1/sync_history"
    
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    
    payload = {
        "title": title,
        "artist": artist,
        "status": status
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            print(f"Supabase logging failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception during Supabase log: {e}")

def get_dbx_client():
    return dropbox.Dropbox(
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET
    )

def fetch_liked_tracks():
    """Fetch the 10 most recent likes using yt-dlp for discovery."""
    likes_url = f"https://soundcloud.com/users/{SC_USER_ID}/likes"
    print(f"🔍 Discovering likes via yt-dlp: {likes_url}")
    
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'playlist_items': '1,10', # Get first 10
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(likes_url, download=False)
            if 'entries' in result:
                tracks = result['entries']
                print(f"🔍 Found {len(tracks)} tracks via yt-dlp.")
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
        log_to_supabase("System Check", "No new likes found", "idle")
        return

    synced_any = False
    for track in items:
        # yt-dlp returns slightly different keys than the raw API
        title = track.get('title') or track.get('url', 'Unknown Title')
        artist = track.get('uploader') or 'Unknown Artist'
        url = track.get('url')
        if not url: continue
        
        # Ensure URL is absolute
        if url.startswith('/'):
            url = f"https://soundcloud.com{url}"
        
        clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
        dbx_path = f"/Music/Sync/{clean_title}.mp3"
        
        try:
            dbx.files_get_metadata(dbx_path)
            print(f"Skipping {title} - exists.")
        except:
            print(f"📥 Downloading: {title}...")
            ydl_opts = {
                'outtmpl': 'temp_track.mp3',
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
            }
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                print(f"📤 Uploading: {title}...")
                with open("temp_track.mp3", "rb") as f:
                    dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)
                
                log_to_supabase(title, artist, "success")
                synced_any = True
                print(f"✅ Success: {title}")
                
            except Exception as e:
                print(f"❌ Failed to sync {title}: {e}")
            finally:
                if os.path.exists("temp_track.mp3"):
                    os.remove("temp_track.mp3")
    
    if not synced_any:
        log_to_supabase("Sync Complete", "All tracks already in Dropbox", "idle")

if __name__ == "__main__":
    if not all([SC_CLIENT_ID, SC_USER_ID, DBX_REFRESH_TOKEN]):
        print("Missing critical environment variables.")
    else:
        sync_to_dropbox()