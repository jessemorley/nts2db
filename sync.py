import os
import requests
import dropbox
import yt_dlp
from dropbox.exceptions import ApiError

# Load secrets from environment
SC_USER_ID = os.getenv("SC_USER_ID")
SC_CLIENT_ID = os.getenv("SC_CLIENT_ID")
SC_OAUTH_TOKEN = os.getenv("SC_OAUTH_TOKEN")
DBX_KEY = os.getenv("DBX_APP_KEY")
DBX_SECRET = os.getenv("DBX_APP_SECRET")
DBX_REFRESH = os.getenv("DBX_REFRESH_TOKEN")

def get_likes():
    """Fetch recent likes from SoundCloud API v2."""
    # Using the modern /likes endpoint which is more reliable than /track_likes
    url = f"https://api-v2.soundcloud.com/users/{SC_USER_ID}/likes?client_id={SC_CLIENT_ID}&limit=24&offset=0&linked_partitioning=1"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://soundcloud.com",
        "Referer": "https://soundcloud.com/"
    }
    
    if SC_OAUTH_TOKEN:
        # Ensure the token is prefixed with 'OAuth ' correctly
        token = SC_OAUTH_TOKEN.strip()
        if not token.startswith("OAuth "):
            token = f"OAuth {token}"
        headers["Authorization"] = token
        
    try:
        print(f"Fetching likes for User ID: {SC_USER_ID}...")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 401:
            print("❌ SoundCloud Unauthorized (401).")
            print("Check: 1. Is SC_OAUTH_TOKEN correct in GitHub Secrets?")
            print("Check: 2. Does the token start with 'OAuth '?")
            return []
            
        response.raise_for_status()
        data = response.json()
        collection = data.get('collection', [])
        
        print(f"Found {len(collection)} items in the likes collection.")
        return collection
    except Exception as e:
        print(f"❌ Error fetching likes: {e}")
        return []

def file_exists(dbx, path):
    """Check if file already exists in Dropbox."""
    try:
        dbx.files_get_metadata(path)
        return True
    except ApiError:
        return False

def download_and_upload(track_url, title):
    """Downloads track via yt-dlp and uploads to Dropbox."""
    # Clean title for filename (remove invalid chars)
    clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
    dbx_path = f"/Music/Sync/{clean_title}.mp3"
    
    dbx = dropbox.Dropbox(
        app_key=DBX_KEY, 
        app_secret=DBX_SECRET, 
        oauth2_refresh_token=DBX_REFRESH
    )

    if file_exists(dbx, dbx_path):
        print(f"Skipping {title} - already synced.")
        return

    print(f"📥 Downloading: {title}...")
    
    ydl_opts = {
        'outtmpl': 'temp_track.mp3', 
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        # SoundCloud specific optimization for yt-dlp
        'referer': 'https://soundcloud.com/',
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([track_url])
        
        print(f"📤 Uploading to Dropbox...")
        with open("temp_track.mp3", "rb") as f:
            dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)
        
        print(f"✅ Success: {title}")
    except Exception as e:
        print(f"❌ Failed {title}: {e}")
    finally:
        if os.path.exists("temp_track.mp3"):
            os.remove("temp_track.mp3")

if __name__ == "__main__":
    if not all([SC_USER_ID, SC_CLIENT_ID, DBX_REFRESH]):
        print("❌ Error: Missing critical GitHub Secrets.")
    else:
        items = get_likes()
        for item in items:
            # Check for track object in different response formats
            track = item.get('track') or (item if 'permalink_url' in item else None)
            if track and 'permalink_url' in track:
                download_and_upload(track['permalink_url'], track['title'])