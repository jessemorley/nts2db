import requests
import dropbox
import yt_dlp
import os
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

def upsert_track(url, title, artist, status, progress=None):
    """Upsert a track's sync state into playlist_tracks (keyed by URL)."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY: return
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    payload = {
        "url": url, "title": title, "artist": artist,
        "status": status, "updated_at": datetime.utcnow().isoformat()
    }
    if progress is not None: payload["progress"] = int(progress)
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/playlist_tracks?on_conflict=url", headers=headers, json=payload)
    except Exception as e:
        print(f"Supabase error: {e}")

def fetch_tracks():
    """Fetch track entries using yt-dlp flat extract."""
    target_url = SC_PLAYLIST_URL
    if not target_url or "REPLACE_WITH_YOUR" in target_url:
        target_url = f"https://soundcloud.com/users/{SC_USER_ID}/likes"

    print(f"🔍 Fetching latest tracks from: {target_url}")
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'playlist_items': '1-50',
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
    dbx = dropbox.Dropbox(oauth2_refresh_token=DBX_REFRESH_TOKEN, app_key=DBX_APP_KEY, app_secret=DBX_APP_SECRET)
    items = fetch_tracks()

    if items is None or not items:
        print("No tracks found.")
        return

    # --- Phase 1: Discover & Queue ---
    # SoundCloud flat extract omits title/uploader, so a full per-track metadata fetch is
    # required to build the correct filename for the Dropbox existence check.
    # Every track is upserted to playlist_tracks immediately (synced or queued),
    # so the dashboard shows the full playlist before downloads begin.
    print(f"📋 Scanning {len(items)} tracks against Dropbox...")
    tracks_to_download = []
    for item in items:
        url = item.get('url')
        if not url: continue
        if url.startswith('/'): url = f"https://soundcloud.com{url}"

        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'no_cache_dir': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', 'Unknown Title')
                artist = info.get('uploader', 'Unknown Artist')
        except:
            title = item.get('title', 'Unknown Title')
            artist = item.get('uploader', 'Unknown Artist')

        clean_name = "".join([c for c in f"{title} - {artist}" if c.isalnum() or c in (' ', '-', '_')]).strip()
        dbx_path = f"/Music/Sync/{clean_name}.mp3"

        try:
            dbx.files_get_metadata(dbx_path)
            upsert_track(url, title, artist, "synced", progress=100)
            print(f"  ✓ exists: {clean_name}")
        except:
            upsert_track(url, title, artist, "queued", progress=0)
            tracks_to_download.append({'title': title, 'artist': artist, 'url': url, 'dbx_path': dbx_path})
            print(f"  + queued: {clean_name}")

    if not tracks_to_download:
        print("All tracks up to date.")
        return

    print(f"\n⬇️  Downloading {len(tracks_to_download)} new tracks...\n")

    # --- Phase 2: Download & Upload ---
    for track in tracks_to_download:
        title = track['title']
        artist = track['artist']
        url = track['url']
        dbx_path = track['dbx_path']

        upsert_track(url, title, artist, "downloading", progress=0)

        last_sent_p = 0
        def progress_hook(d, _url=url, _title=title, _artist=artist):
            nonlocal last_sent_p
            if d['status'] == 'downloading':
                p = 0
                if d.get('total_bytes'): p = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif d.get('total_bytes_estimate'): p = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    p_str = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '0%'))
                    try: p = float(p_str.replace('%', '').strip())
                    except: pass
                p = int(p)
                if p >= last_sent_p + 10 or p == 100:
                    upsert_track(_url, _title, _artist, "downloading", progress=p)
                    last_sent_p = p

        ydl_opts = {
            'outtmpl': 'temp_track',
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [progress_hook],
            'no_cache_dir': True,
            'writethumbnail': True,
            'postprocessors': [
                {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
                {'key': 'FFmpegMetadata', 'add_metadata': True},
                {'key': 'EmbedThumbnail', 'already_have_thumbnail': False},
            ]
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
            upsert_track(url, title, artist, "uploading", progress=100)
            with open("temp_track.mp3", "rb") as f:
                dbx.files_upload(f.read(), dbx_path, mode=dropbox.files.WriteMode.overwrite)
            upsert_track(url, title, artist, "synced", progress=100)
        except Exception as e:
            print(f"❌ Error on {title}: {e}")
            upsert_track(url, title, artist, "error")
        finally:
            for ext in ['mp3', 'jpg', 'jpeg', 'png', 'webp']:
                tmp = f"temp_track.{ext}"
                if os.path.exists(tmp): os.remove(tmp)

if __name__ == "__main__":
    if not all([SC_CLIENT_ID, SC_USER_ID, DBX_REFRESH_TOKEN]): print("Missing ENV vars")
    else: sync_to_dropbox()
