import requests
import dropbox
import yt_dlp
import os
import json
from datetime import datetime

# SoundCloud & Dropbox Configuration
SC_CLIENT_ID = os.getenv("SC_CLIENT_ID")
SC_USER_ID = os.getenv("SC_USER_ID")
DBX_APP_KEY = os.getenv("DBX_APP_KEY")
DBX_APP_SECRET = os.getenv("DBX_APP_SECRET")
DBX_REFRESH_TOKEN = os.getenv("DBX_REFRESH_TOKEN")

# --- SUPABASE OPAQUE KEY CONFIG ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY") 

def log_to_supabase(title, artist):
    """Logs the sync event to Supabase using the new Opaque Key approach."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print("Supabase configuration missing (SUPABASE_URL or SUPABASE_SECRET_KEY).")
        return

    url = f"{SUPABASE_URL}/rest/v1/sync_history"
    
    # Header pattern for new Opaque Keys (sb_secret_...)
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    
    payload = {
        "title": title,
        "artist": artist,
        "status": "success"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            print(f"Supabase logging failed: {response.status_code} - {response.text}")
        else:
            print(f"Dashboard updated for: {title}")
    except Exception as e:
        print(f"Exception during Supabase log: {e}")

def get_dbx_client():
    return dropbox.Dropbox(
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET
    )

def fetch_liked_tracks():
    """Fetch the 10 most recent likes from SoundCloud."""
    url = f"https://api-v2.soundcloud.com/users/{SC_USER_ID}/likes?client_id={SC_CLIENT_ID}&limit=10"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.json().get('collection', [])
    except Exception as e:
        print(f"SoundCloud fetch error: {e}")
        return []

def sync_to_dropbox():
    dbx = get_dbx_client()
    items = fetch_liked_tracks()
    
    if not items:
        print("No tracks found to sync.")
        return

    for item in items:
        track = item.get('track', {})
        if not track: continue
        
        title = track.get('title', 'Unknown Title')
        artist = track.get('user', {}).get('username', 'Unknown Artist')
        url = track.get('permalink_url')
        
        # Clean title for filename
        clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
        dbx_path = f"/Music/Sync/{clean_title}.mp3"
        
        try:
            # 1. Check if file exists in Dropbox
            dbx.files_get_metadata(dbx_path)
            print(f"Skipping {title} - already in Dropbox.")
        except:
            print(f"📥 Downloading: {title}...")
            
            # 2. Download from SoundCloud using yt-dlp
            ydl_opts = {
                'outtmpl': 'temp_track.mp3',
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
            }
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                # 3. Upload to Dropbox
                print(f"📤 Uploading: {title}...")
                with open("temp_track.mp3", "rb") as f:
                    dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)
                
                # 4. Log to Supabase for the Dashboard
                log_to_supabase(title, artist)
                print(f"✅ Success: {title}")
                
            except Exception as e:
                print(f"❌ Failed to sync {title}: {e}")
            finally:
                if os.path.exists("temp_track.mp3"):
                    os.remove("temp_track.mp3")

if __name__ == "__main__":
    if not all([SC_CLIENT_ID, SC_USER_ID, DBX_REFRESH_TOKEN]):
        print("Missing critical environment variables (SoundCloud or Dropbox).")
    else:
        sync_to_dropbox()