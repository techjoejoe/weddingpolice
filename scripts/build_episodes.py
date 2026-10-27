#!/usr/bin/env python3
"""Build the episode pages from the podcast RSS feed.

Run from anywhere: python3 scripts/build_episodes.py

Writes:
  episodes/<slug>.html     one page per episode
  episodes/index.html      every episode, newest first
  episodes/img/<slug>.jpg  600px artwork (downloaded once)
And refreshes the generated blocks in index.html, sitemap.xml and llms.txt.

scripts/episodes.json remembers each episode's slug, number and Spotify/Apple
links so URLs never change. You can hand-edit an entry's "title" or "number"
there; the next build picks it up.

Output is deterministic: running it twice on the same feed changes nothing.
Needs Pillow for the artwork (pip install pillow).
"""
import email.utils
import html
import io
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED_URL = 'https://anchor.fm/s/10dadcee8/podcast/rss'
APPLE_ID = '1875631808'
SITE = 'https://theweddingpolice.com/'
SPOTIFY_SHOW = 'https://open.spotify.com/show/00XbQ3oSRLm3CeETc8bwdl'
APPLE_SHOW = 'https://podcasts.apple.com/us/podcast/the-wedding-police/id1875631808'
MANIFEST = os.path.join(ROOT, 'scripts', 'episodes.json')
EP_DIR = os.path.join(ROOT, 'episodes')
IMG_DIR = os.path.join(EP_DIR, 'img')
HOME_COUNT = 6
NS = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
UA = {'User-Agent': 'Mozilla/5.0 (compatible; TheWeddingPoliceSiteBuilder/1.0)'}

GA = '''<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-2VL6QGTLXF"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-2VL6QGTLXF');
</script>'''

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900'
         '&family=Outfit:wght@300;400;500;600;700&family=Pacifico&display=swap" rel="stylesheet">')

NAV = ('<nav class="blog-nav"><a href="../" class="nav-logo">The Wedding Police</a><ul class="blog-nav-links">'
       '<li><a href="../">Home</a></li><li><a href="./">Episodes</a></li><li><a href="../blog/">Blog</a></li>'
       '<li><a href="../#listen" class="nav-cta">🎧 Listen</a></li></ul></nav>')

FOOTER = ('<footer class="blog-footer"><div class="footer-logo">The Wedding Police</div>'
          '<p>© 2025–2026 The Wedding Police Podcast. All rights reserved.</p></footer>')

# Blog posts and tools offered as "related reading", matched on episode title first, then show notes.
# Paths are relative to /episodes/.
RELATED = [
    (r'gown|dress shopping|bridal salon|alteration', '../blog/bridal-gown-shopping-guide.html', 'Bridal Gown Shopping Guide', 'When to start, what to budget and what to bring.'),
    (r'dress code|black tie|attire|what to wear|wears white', '../blog/wedding-guest-dress-code.html', 'Wedding Guest Dress Code', 'Can you wear black? Every guest rule, answered.'),
    (r'dress code|black tie|formal', '../blog/black-tie-vs-optional.html', 'Black Tie vs Black Tie Optional', 'What each dress code really means for guests.'),
    (r'speech|toast', '../blog/how-to-write-wedding-speech.html', 'Funny Wedding Speech Examples', 'Toast tips for the best man and maid of honor.'),
    (r'speech|toast', '../blog/sister-of-the-groom-speech-examples.html', 'Sister of the Groom Speech Examples', 'Funny, heartfelt and never cringe.'),
    (r'cocktail|drink|\bbar\b|dry wedding|alcohol', '../blog/signature-wedding-cocktails.html', 'Signature Wedding Cocktails', 'Crowd-pleasing drinks for every season.'),
    (r'\bdiy\b|favor|craft', '../blog/diy-wedding-ideas.html', 'DIY Wedding Ideas', 'Which DIY projects save money and which cause meltdowns.'),
    (r'\bpets?\b|\bdogs?\b', '../blog/including-dogs-in-wedding.html', 'Including Your Dog in Your Wedding', 'How to make your pup part of the day, stress-free.'),
    (r'budget|cost|money|financial|expensive|afford', '../budget-calculator.html', 'Free Wedding Budget Calculator', 'See exactly how to split your budget in seconds.'),
    (r'budget|cost|money|financial|expensive|afford', '../blog/wedding-budget-tips.html', 'Wedding Budget Tips', 'Real ways to plan your dream wedding for less.'),
    (r'mess|disaster|fail|chaos', '../blog/wedding-day-disasters.html', 'Surviving Wedding Day Disasters', 'How real couples handled rain, no-shows and more.'),
    (r'game|trivia|\bbets?\b|gameify', '../blog/wedding-bets-tiktok-trend.html', 'Wedding Bets', 'The TikTok reception game everyone is playing.'),
    (r'\bhot\b|heat|summer|outdoor', '../blog/summer-outdoor-wedding-attire.html', 'What to Wear to a Summer Outdoor Wedding', 'Fabrics, colors and shoes that survive the heat.'),
    (r'plan|timeline|expo|trend|rehearsal', '../wedding-checklist.html', 'Free 12-Month Wedding Checklist', 'Check off every task. Your progress saves automatically.'),
    (r'plan|timeline|expo|trend', '../blog/wedding-planning-timeline.html', 'Wedding Planning Timeline', 'A month-by-month guide from engagement to honeymoon.'),
]
RELATED_FALLBACK = [
    ('../wedding-checklist.html', 'Free 12-Month Wedding Checklist', 'Check off every task. Your progress saves automatically.'),
    ('../budget-calculator.html', 'Free Wedding Budget Calculator', 'See exactly how to split your budget in seconds.'),
    ('../blog/wedding-day-disasters.html', 'Surviving Wedding Day Disasters', 'How real couples handled rain, no-shows and more.'),
]

SMALL_WORDS = {'a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'in', 'of', 'on', 'or', 'the', 'to', 'vs', 'with'}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def esc(s):
    return html.escape(s, quote=True)


# ---------- text helpers ----------

def clean_title(raw):
    t = re.sub(r'\s+', ' ', raw).strip()
    t = re.sub(r'(?i)^ep(?:isode|siode)?\s*\d+\s*[-–:]?\s*', '', t)
    t = re.sub(r'\s+([!?:,])', r'\1', t)
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        words = t.lower().split(' ')
        t = ' '.join(w if (i and w in SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return t


def title_number(raw):
    m = re.match(r'(?i)^ep(?:isode|siode)?\s*(\d+)', raw.strip())
    return int(m.group(1)) if m else None


def slugify(t):
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()
    t = t.replace('&', ' and ').lower()
    t = re.sub(r"['’]", '', t)
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')


class Sanitizer(HTMLParser):
    """Keep simple formatting from the show notes; drop everything else."""
    KEEP = {'p', 'br', 'em', 'i', 'strong', 'b', 'ul', 'ol', 'li', 'a'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.KEEP:
            return
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            if not re.match(r'^https?://', href):
                self.out.append('<a>')
                return
            self.out.append(f'<a href="{esc(href)}" target="_blank" rel="noopener nofollow">')
        else:
            self.out.append(f'<{tag}>')

    def handle_endtag(self, tag):
        if tag in self.KEEP and tag != 'br':
            self.out.append(f'</{tag}>')

    def handle_data(self, data):
        self.out.append(html.escape(data.replace('\xa0', ' '), quote=False))


def sanitize(desc):
    s = Sanitizer()
    s.feed(desc)
    s.close()
    out = ''.join(s.out)
    out = re.sub(r'<a>(.*?)</a>', r'\1', out, flags=re.S)
    out = re.sub(r'<p>\s*</p>', '', out)
    if '<p>' not in out:
        out = ''.join(f'<p>{p.strip()}</p>' for p in re.split(r'(?:<br>\s*){2,}|\n{2,}', out) if p.strip())
    return out.strip()


def plain(desc):
    t = re.sub(r'<[^>]+>', ' ', desc)
    t = html.unescape(t).replace('\xa0', ' ')
    t = re.sub(r'\s+', ' ', t).strip()
    return re.sub(r'\s+([.,!?;:])', r'\1', t)


def clip(text, n):
    if len(text) <= n:
        return text
    cut = text[:n - 1].rsplit(' ', 1)[0].rstrip(' ,;:–-')
    return cut + '…'


def iso_duration(d):
    parts = [int(p) for p in (d or '0').split(':')]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


# ---------- external links ----------

def apple_links():
    try:
        data = json.loads(fetch(f'https://itunes.apple.com/lookup?id={APPLE_ID}&entity=podcastEpisode&limit=200'))
    except Exception as e:
        print('Apple lookup failed:', e, file=sys.stderr)
        return {}
    out = {}
    for r in data.get('results', []):
        if r.get('wrapperType') == 'podcastEpisode' and r.get('episodeGuid') and r.get('trackViewUrl'):
            out[r['episodeGuid']] = re.sub(r'&uo=\d+', '', r['trackViewUrl'])
    return out


def norm(s):
    return re.sub(r'[^a-z0-9]', '', html.unescape(s).lower())


def spotify_pairs(page_url):
    """Spotify's creators page lists recent episodes as JSON: anchor id -> open.spotify.com URL."""
    try:
        s = fetch(page_url).decode('utf-8', 'replace')
    except Exception as e:
        print('Spotify page failed:', e, file=sys.stderr)
        return {}
    out = {}
    for m in re.finditer(r'"spotifyUrl":"(https:\\u002F\\u002Fopen\.spotify\.com\\u002Fepisode\\u002F[A-Za-z0-9]{22})"', s):
        before = s[max(0, m.start() - 3000):m.start()]
        paths = re.findall(r'"shareLinkPath":"([^"]+)"', before)
        if paths:
            anchor_id = paths[-1].replace('\\u002F', '/').rsplit('-', 1)[-1]
            out[anchor_id] = m.group(1).replace('\\u002F', '/')
    return out


def spotify_title_ok(url, title):
    try:
        o = json.loads(fetch('https://open.spotify.com/oembed?url=' + urllib.parse.quote(url, safe='')))
    except Exception:
        return False
    return norm(o.get('title', '')) == norm(title)


# ---------- artwork ----------

def ensure_art(slug, url):
    path = os.path.join(IMG_DIR, slug + '.jpg')
    if os.path.exists(path) or not url:
        return os.path.exists(path)
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(fetch(url, timeout=60))).convert('RGB')
        im = ImageOps.fit(im, (600, 600), Image.LANCZOS)
        os.makedirs(IMG_DIR, exist_ok=True)
        im.save(path, 'JPEG', quality=80, optimize=True, progressive=True)
        return True
    except Exception as e:
        print(f'Artwork failed for {slug}:', e, file=sys.stderr)
        return False


# ---------- feed ----------

def load_episodes():
    root = ET.fromstring(fetch(FEED_URL))
    items = root.findall('./channel/item')
    if not items:
        sys.exit('Feed has no episodes; leaving the site untouched.')
    show_art = root.find('./channel/itunes:image', NS).get('href')
    manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}

    eps = []
    for it in items:
        img = it.find('itunes:image', NS)
        enc = it.find('enclosure')
        eps.append({
            'guid': it.findtext('guid').strip(),
            'raw_title': it.findtext('title').strip(),
            'desc': it.findtext('description') or '',
            'link': (it.findtext('link') or '').strip(),
            'date': email.utils.parsedate_to_datetime(it.findtext('pubDate')),
            'audio': enc.get('url') if enc is not None else '',
            'seconds': iso_duration(it.findtext('itunes:duration', namespaces=NS)),
            'art_url': img.get('href') if img is not None else show_art,
        })
    eps.sort(key=lambda e: e['date'])

    titled = {title_number(e['raw_title']) for e in eps} - {None}
    used_slugs = {v['slug'] for v in manifest.values()}
    for i, e in enumerate(eps, 1):
        entry = manifest.get(e['guid'])
        if entry is None:
            title = clean_title(e['raw_title'])
            number = title_number(e['raw_title'])
            if number is None and i not in titled:
                number = i
            slug = base = slugify(title) or f'episode-{number or i}'
            n = 2
            while slug in used_slugs:
                slug = f'{base}-{n}'
                n += 1
            used_slugs.add(slug)
            entry = manifest[e['guid']] = {'slug': slug, 'number': number, 'title': title}
        e.update(slug=entry['slug'], number=entry.get('number'), title=entry['title'], entry=entry)

    # Episode-specific listen links, filled in once and then remembered
    apple = None
    spotify_cache = {}
    for e in eps:
        entry = e['entry']
        if 'apple' not in entry:
            if apple is None:
                apple = apple_links()
            if e['guid'] in apple:
                entry['apple'] = apple[e['guid']]
        if 'spotify' not in entry and e['link']:
            anchor_id = e['link'].rsplit('-', 1)[-1]
            if anchor_id not in spotify_cache:
                spotify_cache.update(spotify_pairs(e['link']))
            url = spotify_cache.get(anchor_id)
            if url and spotify_title_ok(url, e['raw_title']):
                entry['spotify'] = url
        e['apple'] = entry.get('apple', APPLE_SHOW)
        e['spotify'] = entry.get('spotify', SPOTIFY_SHOW)
        e['has_art'] = ensure_art(e['slug'], e['art_url'])

    with open(MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write('\n')

    eps.reverse()  # newest first
    for e in eps:
        e['notes'] = sanitize(e['desc'])
        e['plain'] = plain(e['desc'])
        e['url'] = f"{SITE}episodes/{e['slug']}.html"
        e['label'] = f"Episode {e['number']}" if e['number'] else 'Bonus episode'
        e['short'] = f"Ep {e['number']}" if e['number'] else 'Bonus'
        e['date_str'] = e['date'].strftime('%b %-d, %Y')
        e['iso_date'] = e['date'].strftime('%Y-%m-%d')
        e['mins'] = max(1, round(e['seconds'] / 60))
    return eps


# ---------- rendering ----------

def art_src(e, prefix):
    return f"{prefix}img/{e['slug']}.jpg" if e['has_art'] else e['art_url']


def card(e, prefix, heading='h2', teaser=True, search=False):
    data = f' data-search="{esc((e["title"] + " " + e["plain"]).lower())}"' if search else ''
    p = f'<p>{esc(clip(e["plain"], 120))}</p>' if teaser else ''
    return (f'<a class="ep-card" href="{prefix}{e["slug"]}.html"{data}>'
            f'<img src="{art_src(e, prefix)}" width="600" height="600" loading="lazy" alt="{esc(e["title"])} episode artwork">'
            f'<div class="ep-card-body"><span class="ep-card-meta">{e["short"]} · {e["date_str"]} · {e["mins"]} min</span>'
            f'<{heading}>{esc(e["title"])}</{heading}>{p}</div></a>')


def related_for(e):
    picks = []
    for text in (e['title'], e['plain']):
        for pattern, href, title, blurb in RELATED:
            if len(picks) < 3 and re.search(pattern, text, re.I) and href not in [p[0] for p in picks]:
                picks.append((href, title, blurb))
    for item in RELATED_FALLBACK:
        if len(picks) < 3 and item[0] not in [p[0] for p in picks]:
            picks.append(item)
    return picks


def page_title(e):
    for t in (f"{e['title']} | The Wedding Police Podcast {e['short']}",
              f"{e['title']} | Wedding Police Podcast {e['short']}",
              f"{e['title']} | Wedding Police {e['short']}"):
        if len(t) <= 60:
            return t
    return f"{e['title']} | Wedding Police"


def jsonld(obj):
    return '<script type="application/ld+json">\n' + json.dumps(obj, ensure_ascii=False) + '\n</script>'


def head(title, desc, canonical, image, og_type, extra):
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
{GA}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{canonical}">
<link rel="icon" type="image/png" href="../police.png">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="The Wedding Police">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{image}">
<meta name="twitter:card" content="summary">
{extra}
{FONTS}
<link rel="stylesheet" href="../blog/blog.css">
<link rel="stylesheet" href="episodes.css">
</head>
<body>

<div class="disco-light"></div>
<div class="disco-light"></div>
<div class="disco-light"></div>

{NAV}
'''


TRACKING = '''<script>
  // Count plays and listen-button clicks in Google Analytics
  document.querySelectorAll('[data-ep]').forEach(el => {
    const ev = el.tagName === 'AUDIO' ? 'play' : 'click';
    el.addEventListener(ev, () => {
      if (typeof gtag === 'function') gtag('event', el.dataset.ev, { episode: el.dataset.ep, platform: el.dataset.platform || 'website' });
    }, { once: el.tagName === 'AUDIO' });
  });
</script>'''


def render_episode(e, newer, older):
    image = SITE + f"episodes/img/{e['slug']}.jpg" if e['has_art'] else e['art_url']
    desc = clip(e['plain'], 155)
    ld_episode = {
        '@context': 'https://schema.org', '@type': 'PodcastEpisode',
        'name': e['title'], 'url': e['url'], 'datePublished': e['iso_date'],
        'description': clip(e['plain'], 500), 'image': image,
        'timeRequired': f"PT{e['seconds'] // 60}M{e['seconds'] % 60}S",
        'associatedMedia': {'@type': 'MediaObject', 'contentUrl': e['audio']},
        'partOfSeries': {'@type': 'PodcastSeries', 'name': 'The Wedding Police', 'url': SITE},
        'sameAs': [u for u in (e['spotify'], e['apple']) if u not in (SPOTIFY_SHOW, APPLE_SHOW)],
    }
    if e['number']:
        ld_episode['episodeNumber'] = e['number']
    if not ld_episode['sameAs']:
        del ld_episode['sameAs']
    ld_crumbs = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': SITE},
        {'@type': 'ListItem', 'position': 2, 'name': 'Episodes', 'item': SITE + 'episodes/'},
        {'@type': 'ListItem', 'position': 3, 'name': e['title'], 'item': e['url']},
    ]}
    ep_key = esc(f"{e['short']} {e['title']}")
    related = ''.join(f'\n    <a href="{h}" class="related-card"><h4>{esc(t)}</h4><p>{esc(b)}</p></a>' for h, t, b in related_for(e))
    nav = ''
    if older:
        nav += f'<a class="prev" href="{older["slug"]}.html"><span>← Previous</span>{esc(older["title"])}</a>'
    if newer:
        nav += f'<a class="next" href="{newer["slug"]}.html"><span>Next →</span>{esc(newer["title"])}</a>'

    return head(page_title(e), desc, e['url'], image, 'article',
                jsonld(ld_episode) + '\n' + jsonld(ld_crumbs)) + f'''
<article class="blog-article ep-article">
  <div class="blog-breadcrumb"><a href="../">Home</a> / <a href="./">Episodes</a> / {esc(e['short'])}</div>

  <div class="ep-hero">
    <img class="ep-art" src="{art_src(e, '')}" width="600" height="600" fetchpriority="high" alt="{esc(e['title'])} episode artwork">
    <div>
      <div class="ep-eyebrow">{e['label']} · {e['date_str']} · {e['mins']} min</div>
      <h1>{esc(e['title'])}</h1>
      <p class="ep-teaser">A Wedding Police case file with Penny &amp; Jim.</p>
    </div>
  </div>

  <div class="ep-player">
    <div class="ep-player-label">▶ Play this episode</div>
    <audio controls preload="none" src="{esc(e['audio'])}" data-ep="{ep_key}" data-ev="episode_play"></audio>
    <div class="ep-listen">
      <a href="{e['spotify']}" target="_blank" rel="noopener noreferrer" class="cta-btn spotify" data-ep="{ep_key}" data-ev="listen_click" data-platform="spotify">🎵 Listen on Spotify</a>
      <a href="{e['apple']}" target="_blank" rel="noopener noreferrer" class="cta-btn apple" data-ep="{ep_key}" data-ev="listen_click" data-platform="apple">🎙 Apple Podcasts</a>
    </div>
  </div>

  <h2>Show Notes</h2>
  <div class="ep-notes">
  {e['notes']}
  </div>

  <div class="related-posts"><h3>Related Wedding Tips</h3><div class="related-grid">{related}
  </div></div>

  <div class="ep-prevnext">{nav}</div>

  <div class="podcast-cta ep-follow">
    <h3>🚨 Never Miss a Case</h3>
    <p>New episodes every week. Follow The Wedding Police so the next one lands in your feed.</p>
    <div class="cta-buttons">
      <a href="{SPOTIFY_SHOW}" target="_blank" rel="noopener noreferrer" class="cta-btn spotify" data-ep="{ep_key}" data-ev="follow_click" data-platform="spotify">🎵 Follow on Spotify</a>
      <a href="{APPLE_SHOW}" target="_blank" rel="noopener noreferrer" class="cta-btn apple" data-ep="{ep_key}" data-ev="follow_click" data-platform="apple">🎙 Follow on Apple</a>
    </div>
    <p class="ep-rate"><span aria-hidden="true">⭐⭐⭐⭐⭐</span> Enjoying it? A quick rating on <a href="{APPLE_SHOW}" target="_blank" rel="noopener noreferrer" data-ep="{ep_key}" data-ev="rate_click" data-platform="apple">Apple Podcasts</a> or <a href="{SPOTIFY_SHOW}" target="_blank" rel="noopener noreferrer" data-ep="{ep_key}" data-ev="rate_click" data-platform="spotify">Spotify</a> helps new couples find the show.</p>
  </div>
</article>

{FOOTER}

{TRACKING}

</body>
</html>
'''


def render_index(eps):
    canonical = SITE + 'episodes/'
    title = 'All Episodes | The Wedding Police Podcast'
    desc = (f'Listen to all {len(eps)} episodes of The Wedding Police, the comedy wedding podcast from Penny & Jim: '
            'etiquette, dress codes, toasts, budgets and wedding fails.')
    ld_series = {'@context': 'https://schema.org', '@type': 'PodcastSeries', 'name': 'The Wedding Police',
                 'url': SITE, 'webFeed': FEED_URL, 'image': SITE + 'IMG_7503.jpeg',
                 'author': [{'@type': 'Person', 'name': 'Penny'}, {'@type': 'Person', 'name': 'Jim'}]}
    ld_crumbs = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': SITE},
        {'@type': 'ListItem', 'position': 2, 'name': 'Episodes', 'item': canonical}]}
    cards = '\n    '.join(card(e, '', search=True) for e in eps)
    return head(title, desc, canonical, SITE + 'IMG_7503.jpeg', 'website',
                jsonld(ld_series) + '\n' + jsonld(ld_crumbs)) + f'''
<header class="blog-hero">
  <h1>Every Episode of The Wedding Police</h1>
  <p>{len(eps)} episodes of wedding etiquette, dress code crimes and reception chaos with Penny &amp; Jim. New episodes every week.</p>
</header>

<main class="ep-index">
  <div class="ep-search">
    <input type="search" id="epSearch" placeholder="Search episodes: toasts, dress codes, cake…" aria-label="Search episodes">
    <span class="ep-count" id="epCount" aria-live="polite">{len(eps)} episodes</span>
  </div>
  <div class="ep-grid">
    {cards}
  </div>
</main>

{FOOTER}

<script>
  const search = document.getElementById('epSearch');
  const count = document.getElementById('epCount');
  const cards = [...document.querySelectorAll('.ep-card')];
  search.addEventListener('input', () => {{
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    cards.forEach(c => {{ c.hidden = !!q && !c.dataset.search.includes(q); if (!c.hidden) shown++; }});
    count.textContent = shown + (shown === 1 ? ' episode' : ' episodes');
  }});
</script>

</body>
</html>
'''


def replace_block(path, start, end, body):
    s = open(path, encoding='utf-8').read()
    pattern = re.compile(re.escape(start) + r'.*?' + re.escape(end), re.S)
    if not pattern.search(s):
        sys.exit(f'Markers {start} … {end} not found in {path}')
    s = pattern.sub(lambda m: start + body + end, s, count=1)
    open(path, 'w', encoding='utf-8').write(s)


def update_home(eps):
    latest = eps[:HOME_COUNT]
    cards = ''.join(f'\n        {card(e, "episodes/", heading="h3", teaser=False)}' for e in latest)
    replace_block(os.path.join(ROOT, 'index.html'), '<!-- LATEST-EPISODES:START -->', '<!-- LATEST-EPISODES:END -->', f'''
      <div class="latest-eps">
        <div class="latest-eps-head">
          <h3>Latest Episodes</h3>
          <a href="episodes/">See all {len(eps)} episodes →</a>
        </div>
        <div class="ep-grid">{cards}
        </div>
      </div>
      ''')
    e = eps[0]
    total = f"{e['seconds'] // 60}:{e['seconds'] % 60:02d}"
    replace_block(os.path.join(ROOT, 'index.html'), '<!-- HERO-LATEST:START -->', '<!-- HERO-LATEST:END -->', f'''
        <div class="hero-latest" data-ep="{esc(e['short'] + ' ' + e['title'])}">
          <img class="hl-art" src="{art_src(e, 'episodes/')}" width="600" height="600" alt="">
          <div class="hl-info">
            <span class="hl-meta">Latest episode · {e['short']} · {e['mins']} min</span>
            <a class="hl-title" href="episodes/{e['slug']}.html">{esc(e['title'])}</a>
            <div class="hl-seekrow">
              <input class="hl-seek" type="range" min="0" max="1000" value="0" step="1" aria-label="Seek within {esc(e['title'])}">
              <span class="hl-time">0:00 / {total}</span>
            </div>
          </div>
          <button class="hl-play" type="button" aria-label="Play the latest episode: {esc(e['title'])}"><span class="hl-icon-play">▶</span><span class="hl-icon-pause">❚❚</span></button>
          <audio preload="none" src="{esc(e['audio'])}"></audio>
        </div>
        ''')
    parts = ',\n'.join('    ' + json.dumps({'@type': 'PodcastEpisode', 'name': e['title'], 'url': e['url'],
                                             'datePublished': e['iso_date']}, ensure_ascii=False) for e in latest)
    replace_block(os.path.join(ROOT, 'index.html'), '"hasPart": [', '\n  ]', '\n' + parts)


def update_sitemap(eps):
    urls = [(SITE + 'episodes/', eps[0]['iso_date'], '0.8')] + [(e['url'], e['iso_date'], '0.7') for e in eps]
    body = ''.join(f'\n  <url>\n    <loc>{u}</loc>\n    <lastmod>{d}</lastmod>\n    <priority>{p}</priority>\n  </url>'
                   for u, d, p in urls)
    replace_block(os.path.join(ROOT, 'sitemap.xml'), '<!-- EPISODES:START -->', '<!-- EPISODES:END -->', body + '\n  ')


def update_llms(eps):
    body = ''.join(f"- {e['short']}: {e['title']}: {e['url']}\n" for e in eps)
    replace_block(os.path.join(ROOT, 'llms.txt'), '## Episodes\n\n', '\n## ', body)


def main():
    eps = load_episodes()
    os.makedirs(EP_DIR, exist_ok=True)
    for i, e in enumerate(eps):
        newer = eps[i - 1] if i > 0 else None
        older = eps[i + 1] if i + 1 < len(eps) else None
        with open(os.path.join(EP_DIR, e['slug'] + '.html'), 'w', encoding='utf-8') as f:
            f.write(render_episode(e, newer, older))
    with open(os.path.join(EP_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(render_index(eps))
    update_home(eps)
    update_sitemap(eps)
    update_llms(eps)
    print(f'Built {len(eps)} episode pages. Newest: {eps[0]["short"]} {eps[0]["title"]}')


if __name__ == '__main__':
    main()
