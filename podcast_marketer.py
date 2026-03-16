#!/usr/bin/env python3
"""
Podcast Marketing Suite
Generates LinkedIn post, show notes + email, and Substack post from a podcast
RSS feed episode — then optionally publishes directly after confirmation.

Setup:
  pip install anthropic requests python-dotenv
  Copy .env.example to .env and fill in your credentials.
"""

import os
import re
import sys
import json
import html
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
from datetime import datetime

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("Missing dependency: pip install anthropic")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env loading is optional; env vars may already be set


# ─── RSS ─────────────────────────────────────────────────────────────────────

def fetch_rss(url: str) -> ET.Element:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PodcastMarketer/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
        return ET.fromstring(content)
    except urllib.error.URLError as e:
        print(f"Error fetching RSS feed: {e}")
        sys.exit(1)
    except ET.ParseError as e:
        print(f"Error parsing RSS feed: {e}")
        sys.exit(1)


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_episodes(root: ET.Element) -> list[dict]:
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    channel = root.find("channel")
    if channel is None:
        print("No <channel> in RSS feed.")
        sys.exit(1)

    podcast_title = channel.findtext("title", "Unknown Podcast").strip()
    episodes = []

    for item in channel.findall("item"):
        title = item.findtext("title", "").strip()
        desc = strip_html(
            item.findtext("description", "")
            or item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "")
            or ""
        )
        episodes.append({
            "podcast": podcast_title,
            "title": title,
            "description": desc[:3000],
            "pub_date": item.findtext("pubDate", ""),
            "duration": item.findtext("itunes:duration", "", ns),
            "guest": (
                item.findtext("itunes:author", "", ns)
                or item.findtext("author", "")
            ),
            "episode_num": item.findtext("itunes:episode", "", ns),
        })

    return episodes


def pick_episode(episodes: list[dict]) -> dict:
    display = episodes[:20]
    print(f"\nFound {len(episodes)} episodes (showing latest {len(display)}):\n")
    for i, ep in enumerate(display, 1):
        num = f"Ep {ep['episode_num']} — " if ep["episode_num"] else ""
        date = f"  [{ep['pub_date'][:16]}]" if ep["pub_date"] else ""
        print(f"  [{i:2}] {num}{ep['title']}{date}")

    print()
    while True:
        try:
            raw = input(f"Pick an episode [1–{len(display)}]: ").strip()
            choice = int(raw)
            if 1 <= choice <= len(display):
                return display[choice - 1]
        except ValueError:
            pass
        except KeyboardInterrupt:
            print("\nExiting.")
            sys.exit(0)
        print(f"Please enter a number between 1 and {len(display)}.")


# ─── Content Generation ───────────────────────────────────────────────────────

def build_prompt(ep: dict) -> str:
    lines = [f"Podcast: {ep['podcast']}", f"Episode Title: {ep['title']}"]
    if ep["episode_num"]:
        lines.append(f"Episode Number: {ep['episode_num']}")
    if ep["guest"]:
        lines.append(f"Guest/Author: {ep['guest']}")
    if ep["duration"]:
        lines.append(f"Duration: {ep['duration']}")
    if ep["description"]:
        lines.append(f"\nDescription:\n{ep['description']}")

    context = "\n".join(lines)

    return f"""You are a podcast marketing expert. Using the episode information below, generate three pieces of marketing content.

---
{context}
---

Generate the following three pieces of content, each separated by the exact headers shown.

## LINKEDIN POST
Write a professional LinkedIn post (150–250 words) that:
- Opens with a compelling hook (insight, question, or bold statement from the episode)
- Highlights 2–3 key takeaways in natural prose (no bullet lists)
- Ends with a call to action to listen
- Sounds human and conversational, not corporate

---

## SHOW NOTES + EMAIL NEWSLETTER
Write polished show notes (300–400 words) for both the podcast host page and an email newsletter:
- 2-sentence episode summary at the top
- 3–5 key topics covered (bullet list)
- 2–3 memorable quotes or insights (paraphrased)
- Closing paragraph encouraging listeners to subscribe/follow

---

## SUBSTACK POST
Write a Substack essay post (400–500 words):
- First line: "Title: [your suggested title]"
- Works as a standalone piece for readers who haven't listened yet
- Weaves the episode's core idea with your own deeper commentary
- Ends with: [Listen to the full episode here]
- Reads like a newsletter essay, not a press release
"""


def generate_content(ep: dict) -> dict[str, str]:
    """Stream content from Claude and parse it into sections."""
    client = anthropic.Anthropic()

    print("\n" + "═" * 62)
    print(f"  Generating content for: \"{ep['title']}\"")
    print("═" * 62 + "\n")

    full_text = ""
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": build_prompt(ep)}],
    ) as stream:
        for chunk in stream.text_stream:
            print(chunk, end="", flush=True)
            full_text += chunk

    final = stream.get_final_message()
    usage = final.usage
    print(f"\n\n{'─' * 62}")
    print(f"Tokens — input: {usage.input_tokens}  output: {usage.output_tokens}\n")

    return parse_sections(full_text)


def parse_sections(text: str) -> dict[str, str]:
    """Split generated text into named sections."""
    sections = {}
    pattern = r"##\s+(LINKEDIN POST|SHOW NOTES \+ EMAIL NEWSLETTER|SUBSTACK POST)\s*\n"
    parts = re.split(pattern, text)

    i = 1
    while i < len(parts) - 1:
        key = parts[i].strip()
        body = parts[i + 1].strip()
        # Remove trailing separator lines
        body = re.sub(r"\n---\s*$", "", body).strip()
        sections[key] = body
        i += 2

    # Fallback: return full text under a single key
    if not sections:
        sections["FULL OUTPUT"] = text.strip()

    return sections


# ─── LinkedIn ─────────────────────────────────────────────────────────────────

def post_to_linkedin(text: str) -> bool:
    """Post a text share to LinkedIn via the UGC Posts API."""
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    person_id = os.environ.get("LINKEDIN_PERSON_ID")

    if not token or not person_id:
        print("\n  ⚠  LinkedIn credentials not set.")
        print("     Set LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_ID in .env")
        return False

    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    payload = {
        "author": f"urn:li:person:{person_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        post_id = resp.headers.get("X-RestLi-Id", "unknown")
        print(f"  ✓  Posted to LinkedIn (ID: {post_id})")
        return True
    else:
        print(f"  ✗  LinkedIn error {resp.status_code}: {resp.text[:200]}")
        return False


# ─── Substack ─────────────────────────────────────────────────────────────────

def substack_login(pub_url: str, email: str, password: str) -> requests.Session | None:
    """Log in to Substack and return an authenticated session."""
    session = requests.Session()
    session.headers.update({"User-Agent": "PodcastMarketer/1.0"})

    login_url = f"{pub_url.rstrip('/')}/api/v1/email-login"
    resp = session.post(
        login_url,
        json={"email": email, "password": password, "redirect": "/"},
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"  ✗  Substack login failed ({resp.status_code}): {resp.text[:200]}")
        return None

    return session


def post_to_substack(text: str, ep: dict) -> bool:
    """Create a Substack draft."""
    pub_url = os.environ.get("SUBSTACK_URL")
    email = os.environ.get("SUBSTACK_EMAIL")
    password = os.environ.get("SUBSTACK_PASSWORD")

    if not pub_url or not email or not password:
        print("\n  ⚠  Substack credentials not set.")
        print("     Set SUBSTACK_URL, SUBSTACK_EMAIL, SUBSTACK_PASSWORD in .env")
        return False

    print("  Logging in to Substack...")
    session = substack_login(pub_url, email, password)
    if session is None:
        return False

    # Extract suggested title if present
    title_match = re.match(r"Title:\s*(.+)", text)
    title = title_match.group(1).strip() if title_match else ep["title"]
    body = re.sub(r"^Title:\s*.+\n", "", text).strip()

    # Substack draft API expects HTML body
    body_html = "<br>".join(f"<p>{line}</p>" if line.strip() else "" for line in body.split("\n"))

    draft_url = f"{pub_url.rstrip('/')}/api/v1/drafts"
    payload = {
        "draft_title": title,
        "draft_subtitle": f"From the {ep['podcast']} podcast",
        "draft_body": body_html,
        "section_chosen": False,
        "type": "newsletter",
    }

    resp = session.post(draft_url, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        try:
            data = resp.json()
            draft_id = data.get("id", "unknown")
        except Exception:
            draft_id = "unknown"
        print(f"  ✓  Substack draft created (ID: {draft_id})")
        print(f"     Edit at: {pub_url.rstrip('/')}/publish/post/{draft_id}")
        return True
    else:
        print(f"  ✗  Substack error {resp.status_code}: {resp.text[:200]}")
        return False


# ─── Confirmation + Publishing ────────────────────────────────────────────────

def confirm(prompt: str) -> bool:
    try:
        answer = input(prompt + " [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except KeyboardInterrupt:
        return False


def publish_with_confirmation(sections: dict[str, str], ep: dict) -> None:
    print("\n" + "═" * 62)
    print("  PUBLISH OPTIONS")
    print("═" * 62)

    # LinkedIn
    linkedin_text = sections.get("LINKEDIN POST", "")
    if linkedin_text:
        print("\n[ LinkedIn ]")
        if confirm("  Post this to LinkedIn?"):
            post_to_linkedin(linkedin_text)
        else:
            print("  Skipped LinkedIn.")

    # Substack
    substack_text = sections.get("SUBSTACK POST", "")
    if substack_text:
        print("\n[ Substack ]")
        if confirm("  Create this as a Substack draft?"):
            post_to_substack(substack_text, ep)
        else:
            print("  Skipped Substack.")

    # Show notes: offer to save as markdown
    shownotes_text = sections.get("SHOW NOTES + EMAIL NEWSLETTER", "")
    if shownotes_text:
        print("\n[ Show Notes / Email ]")
        if confirm("  Save show notes to a markdown file?"):
            safe = re.sub(r"[^a-zA-Z0-9_\- ]", "", ep["title"])[:50].strip().replace(" ", "_")
            filename = f"shownotes_{safe}.md"
            with open(filename, "w") as f:
                f.write(f"# {ep['title']}\n\n")
                f.write(f"_{ep['podcast']} — {ep['pub_date'][:16]}_\n\n")
                f.write(shownotes_text)
            print(f"  Saved to {filename}")
        else:
            print("  Skipped saving show notes.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python podcast_marketer.py <rss_feed_url>")
        print()
        print("Credentials (in .env or environment):")
        print("  ANTHROPIC_API_KEY      — required")
        print("  LINKEDIN_ACCESS_TOKEN  — for LinkedIn posting")
        print("  LINKEDIN_PERSON_ID     — for LinkedIn posting")
        print("  SUBSTACK_URL           — e.g. https://yourpub.substack.com")
        print("  SUBSTACK_EMAIL         — your Substack login email")
        print("  SUBSTACK_PASSWORD      — your Substack login password")
        sys.exit(0)

    rss_url = sys.argv[1]

    print(f"\nFetching RSS: {rss_url}")
    root = fetch_rss(rss_url)
    episodes = parse_episodes(root)

    if not episodes:
        print("No episodes found in feed.")
        sys.exit(1)

    ep = pick_episode(episodes)
    sections = generate_content(ep)
    publish_with_confirmation(sections, ep)

    print("\nDone.\n")


if __name__ == "__main__":
    main()
