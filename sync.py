import os
import requests
import dropbox
import yt_dlp
from dropbox.exceptions import ApiError

# Load secrets from environment (GitHub handles this automatically)
SC_USER_ID = os.getenv("SC_USER_ID")
SC_CLIENT_ID = os.getenv("SC_CLIENT_ID")
DBX_KEY = os.getenv("DBX_APP_KEY")
DBX_SECRET = os.getenv("DBX_APP_SECRET")
DBX_REFRESH = os.getenv("DBX_REFRESH_TOKEN")

def get_likes():
    """Fetch recent likes from SoundCloud API v2."""
    url = f"https://api-v2.soundcloud.com/users/{SC_USER_ID}/track_likes?client_id={SC_CLIENT_ID}&limit=10"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json().get('collection', [])
    except Exception as e:
        print(f"Error fetching likes: {e}")
        return []

def file_exists(dbx, path):
    """Check if file already exists in Dropbox to avoid double-syncing."""
    try:
        dbx.files_get_metadata(path)
        return True
    except ApiError:
        return False

def download_and_upload(track_url, title):
    """Downloads track via yt-dlp and uploads to Dropbox."""
    # Clean title for a safe filename
    clean_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
    dbx_path = f"/Music/Sync/{clean_title}.mp3"
    
    dbx = dropbox.Dropbox(
        app_key=DBX_KEY, 
        app_secret=DBX_SECRET, 
        oauth2_refresh_token=DBX_REFRESH
    )

    if file_exists(dbx, dbx_path):
        print(f"Skipping {title} - already exists.")
        return

    print(f"Downloading: {title}...")
    
    ydl_opts = {
        'outtmpl': 'temp_track.mp3', 
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([track_url])
        
        print(f"Uploading {title} to Dropbox...")
        with open("temp_track.mp3", "rb") as f:
            dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)
        
        print(f"✅ Successfully synced: {title}")
    except Exception as e:
        print(f"❌ Failed to process {title}: {e}")
    finally:
        if os.path.exists("temp_track.mp3"):
            os.remove("temp_track.mp3")

if __name__ == "__main__":
    if not all([SC_USER_ID, SC_CLIENT_ID, DBX_REFRESH]):
        print("Missing environment variables. Make sure secrets are set in GitHub.")
    else:
        likes = get_likes()
        print(f"Found {len(likes)} recent likes.")
        for item in likes:
            track = item.get('track')
            if track:
                download_and_upload(track['permalink_url'], track['title'])