import os
import json
import html
import subprocess
import tempfile
from pathlib import Path

import requests
import feedparser


SOURCE_FEED = "https://api.rtve.es/api/adapter/programas/1000883/audios.rss"

RELEASE_TAG = "episodes"
MAX_EPISODES = 20

STATE_FILE = Path("state.json")
OUTPUT_FILE = Path("sapiensantes.xml")

REPO = os.environ["GITHUB_REPOSITORY"]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"episodes": {}}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False)
    )


def run(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def ensure_release():
    result = subprocess.run(
        ["gh", "release", "view", RELEASE_TAG],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        run([
            "gh", "release", "create", RELEASE_TAG,
            "--title", "Sapiensantes episodes",
            "--notes", "Audio files mirrored for Yoto playback."
        ])


def get_audio_url(entry):
    for enclosure in entry.get("enclosures", []):
        href = enclosure.get("href")
        if href:
            return href

    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]

    return None


def episode_id(entry, audio_url):
    import re

    # RTVE suele incluir el ID numérico del audio en la URL.
    # Ejemplo: https://www.rtve.es/a/17179109/.mp3
    candidates = [
        audio_url,
        str(entry.get("id", "")),
        str(entry.get("guid", "")),
    ]

    for value in candidates:
        matches = re.findall(r"\d{6,}", value)

        if matches:
            return matches[-1]

    # Fallback seguro si RTVE cambia el formato
    return str(abs(hash(audio_url)))


def download_audio(url, destination):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Safari/537.36"
        )
    }

    with requests.get(
        url,
        headers=headers,
        stream=True,
        allow_redirects=True,
        timeout=120,
    ) as r:
        r.raise_for_status()

        with open(destination, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def asset_exists(filename):
    result = subprocess.run(
        [
            "gh", "release", "view", RELEASE_TAG,
            "--json", "assets",
            "--jq", f'.assets[] | select(.name=="{filename}") | .name'
        ],
        capture_output=True,
        text=True
    )

    return filename in result.stdout


def upload_episode(asset_path):
    filename = asset_path.name

    if asset_exists(filename):
        print(f"Already uploaded: {filename}")
        return

    run([
        "gh", "release", "upload",
        RELEASE_TAG,
        str(asset_path)
    ])


def release_url(filename):
    return (
        f"https://github.com/{REPO}/releases/download/"
        f"{RELEASE_TAG}/{filename}"
    )


def xml_escape(value):
    return html.escape(value or "", quote=True)


def build_rss(episodes):
    items = []

    for ep in episodes[:MAX_EPISODES]:
        title = xml_escape(ep["title"])
        description = xml_escape(ep.get("description", ""))
        guid = xml_escape(ep["id"])
        audio_url = xml_escape(ep["mirror_url"])
        published = xml_escape(ep.get("published", ""))

        pubdate = (
            f"<pubDate>{published}</pubDate>"
            if published
            else ""
        )

        items.append(f"""
    <item>
      <title>{title}</title>
      <guid isPermaLink="false">{guid}</guid>
      <description>{description}</description>
      {pubdate}
      <enclosure
        url="{audio_url}"
        type="audio/mpeg" />
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Sapiensantes - Yoto</title>
    <link>https://www.rtve.es/play/audios/sapiensantes/</link>
    <description>
      Sapiensantes mirror optimizado para reproducción en Yoto Player.
    </description>
    <language>es</language>
    <itunes:author>RTVE</itunes:author>
    <itunes:image href="https://matigon-dev.github.io/sapiensantes-yoto/cover.jpg" />
    <image>
      <url>https://matigon-dev.github.io/sapiensantes-yoto/cover.jpg</url>
      <title>Sapiensantes</title>
      <link>https://www.rtve.es/play/audios/sapiensantes/</link>
    </image>
{"".join(items)}
  </channel>
</rss>
"""


def main():
    state = load_state()
    ensure_release()

    feed = feedparser.parse(SOURCE_FEED)

    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Could not parse RSS: {feed.bozo_exception}")

    processed = []

    for entry in feed.entries[:MAX_EPISODES]:
        audio_url = get_audio_url(entry)

        if not audio_url:
            print("Skipping episode without enclosure:", entry.get("title"))
            continue

        ep_id = episode_id(entry, audio_url)
        filename = f"{ep_id}.mp3"

        if ep_id not in state["episodes"]:
            print("Processing episode:", entry.get("title"))
        
            if asset_exists(filename):
                print(f"Already on GitHub: {filename}")
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    destination = Path(tmp) / filename
                    download_audio(audio_url, destination)
                    upload_episode(destination)
        
            state["episodes"][ep_id] = {
                "filename": filename,
                "mirror_url": release_url(filename)
            }

        mirror = state["episodes"][ep_id]["mirror_url"]

        processed.append({
            "id": ep_id,
            "title": entry.get("title", "Sapiensantes"),
            "description": entry.get("summary", ""),
            "published": entry.get("published", ""),
            "mirror_url": mirror
        })

    save_state(state)
    OUTPUT_FILE.write_text(
        build_rss(processed),
        encoding="utf-8"
    )

    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
