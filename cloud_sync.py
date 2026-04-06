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
    """Fetch the 10 most recent likes from SoundCloud."""
    # Use /me/ if we have an OAuth token, otherwise fallback to specific user ID
    if SC_OAUTH_TOKEN:
        base_url = "https://api-v2.soundcloud.com/me/likes"
        url = f"{base_url}?limit=10"
    else:
        base_url = f"https://api-v2.soundcloud.com/users/{SC_USER_ID}/likes"
        url = f"{base_url}?client_id={SC_CLIENT_ID}&limit=10"
    
    # Redacted URL for logging
    log_url = url.replace(SC_CLIENT_ID, "REDACTED") if SC_CLIENT_ID and SC_CLIENT_ID in url else url
    print(f"🔍 Fetching likes from: {log_url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://soundcloud.com/"
    }

    if SC_OAUTH_TOKEN:
        token = SC_OAUTH_TOKEN.strip()
        if not token.lower().startswith("oauth "):
            token = f"OAuth {token}"
        headers["Authorization"] = token
        print("🔍 Using OAuth token (switching to /me/likes).")

    try:
        resp = requests.get(url, headers=headers)
        print(f"🔍 SoundCloud Response Status: {resp.status_code}")
        
        if resp.status_code == 401:
            log_to_supabase("Auth Error", "SoundCloud API", "error")
            print("❌ SoundCloud Unauthorized (401). Check SC_OAUTH_TOKEN.")
            return None
        
        resp.raise_for_status()
        data = resp.json()
        collection = data.get('collection', [])
        print(f"🔍 Found {len(collection)} tracks in collection.")
        
        if len(collection) == 0:
            # Log a snippet of the raw response if empty
            print(f"🔍 Raw response snippet: {resp.text[:200]}")
            
        return collection
    except Exception as e:
        print(f"SoundCloud fetch error: {e}")
        return None

def sync_to_dropbox():
    dbx = get_dbx_client()
    items = fetch_liked_tracks()
    
    if items is None:
        return # Error already logged

    if not items:
        print("No tracks found. Logging heartbeat...")
        log_to_supabase("System Check", "No new likes found", "idle")
        return

    synced_any = False
    for item in items:
        track = item.get('track', {})
        if not track: continue
        
        title = track.get('title', 'Unknown Title')
        artist = track.get('user', {}).get('username', 'Unknown Artist')
        url = track.get('permalink_url')
        
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