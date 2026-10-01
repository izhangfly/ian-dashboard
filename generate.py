#!/usr/bin/env python3
"""
Ian's Dashboard — auto-discovering service dashboard generator.

READ-ONLY with respect to every other service on this host: it only reads
/proc, runs `ss`, parses the Caddyfile, and issues harmless GET requests to
detect page <title>s. The *only* file it writes is its own index.html.

Run it periodically (ian-dashboard-scan.timer) so that any newly created
service — anything that starts listening on a new TCP port — shows up as a
new icon on the next scan, with no manual step.

Stdlib only. Python 3.
"""

import hashlib
import os
import re
import subprocess
from datetime import datetime

SERVER_IP = "YOUR_SERVER_IP"
DASH_PORT = 8080
WWW_DIR = "/opt/ian-dashboard/www"
INDEX_PATH = os.path.join(WWW_DIR, "index.html")
CADDYFILE = "/etc/caddy/Caddyfile"

# ---------------------------------------------------------------------------
# Curated metadata for the services that already exist. These give stable,
# friendly names/icons/colours. Anything NOT listed here is auto-detected.
# ---------------------------------------------------------------------------
KNOWN = {
    8000: ("Alumni Cards", "🎓", "#6366f1", "Alumni Flashcard Web App"),
    8520: ("EPQ GP Explorer", "✈️", "#0ea5e9", "EPQ H2-Solar Aircraft GP Explorer"),
    8640: ("Infographic Maker", "🛩️", "#f59e0b", "IZhang Aviation · Mission Control"),
    8700: ("Coffee Chats", "☕", "#a16207", "Coffee Chat Tracker"),
    8760: ("WeChat Exporter", "📄", "#10b981", "WeChat Article Exporter"),
    8780: ("FIA Flashcards", "🃏", "#ef4444", "FIA2026 Exhibitor Flashcards"),
    8830: ("Ivy Swim", "🏊", "#14b8a6", "Ivy Swim Analytics"),
    8850: ("ACT Training", "🤖", "#8b5cf6", "PiPER · ACT Policy Training"),
    8800: ("Home Library", "📚", "#f97316", "家庭图书馆 · Home Library"),
    8555: ("RAX Panel", "🔐", "#64748b", "RAX3000M VPN rules panel"),
}

# Infrastructure / non-browsable ports — shown in the "system" section, not
# as clickable app icons. New entries can be added here as needed.
INFRA = {
    22:    ("SSH", "🔑"),
    8022:  ("SSH (alt)", "🔑"),
    80:    ("Caddy (HTTP)", "🌐"),
    443:   ("Caddy (HTTPS)", "🌐"),
    2019:  ("Caddy admin API", "🔧"),
    5432:  ("PostgreSQL 16", "🐘"),
    8443:  ("Xray VLESS+Reality", "🛡️"),
    8444:  ("Syncthing Discovery", "🔄"),
    22067: ("Syncthing Relay", "🔄"),
    22070: ("Syncthing Relay (TLS)", "🔄"),
    9765:  ("OCR Watch proxy", "👁️"),
    8642:  ("WeCom Bot webhook", "💬"),
}

# Ports to ignore entirely (DNS resolver, etc.)
IGNORE = {53}

PALETTE = [
    "#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
    "#ec4899", "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#d946ef",
]

KEYWORD_EMOJI = [
    (r"swim|ivy|sport", "🏊"),
    (r"chat|coffee|meet", "☕"),
    (r"library|book|图书|书籍", "📚"),
    (r"aviation|aircraft|mission|izhang|infographic|livery", "🛩️"),
    (r"flashcard|fia|exhibitor|quiz|study", "🃏"),
    (r"epq|gp explorer|solar|h2|energy|hydrogen", "✈️"),
    (r"act|robot|training|piper|policy", "🤖"),
    (r"wechat|export|article|doc|nuxt", "📄"),
    (r"alumni|graduate|card", "🎓"),
    (r"vpn|proxy|rax|panel|rules|wireguard", "🔐"),
    (r"bot|wecom|callback|webhook", "💬"),
    (r"ocr|watch|pipeline", "👁️"),
    (r"poster|render|brew|image", "🎨"),
    (r"analytics|dashboard|data|stats", "📊"),
    (r"mail|email|inbox", "✉️"),
    (r"\bai\b|llm|gpt|deepseek|model", "🧠"),
    (r"photo|gallery", "🖼️"),
    (r"music|audio|song", "🎵"),
    (r"video|stream", "🎬"),
    (r"map|geo|gps", "🗺️"),
    (r"calendar|schedule|plan", "📅"),
    (r"finance|money|bank|stock", "💰"),
    (r"weather", "⛅"),
    (r"game|arcade", "🎮"),
    (r"home|smart|iot", "🏠"),
]

# Titles that are too generic to use as a human-facing name.
GENERIC_TITLES = {"", "streamlit", "next.js", "404 not found", "dash", "none"}


def sh(args, timeout=6):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        ).stdout
    except Exception:
        return ""


def get_listeners():
    """Return {port: {"bind": str, "pid": str}} for all TCP listeners."""
    out = sh(["ss", "-tlnp"])
    result = {}
    for line in out.splitlines():
        m = re.search(r"pid=(\d+)", line)
        if not m:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]                      # e.g. 0.0.0.0:8000, [::]:80, *:22070
        pm = re.search(r":(\d+)\s*$", local)
        if not pm:
            continue
        port = int(pm.group(1))
        addr = local[: pm.start(1) - 1]
        if addr.startswith("["):
            addr = addr.strip("[]")
        if addr == "*":
            addr = "0.0.0.0"
        result.setdefault(port, {"bind": addr, "pid": m.group(1)})
    return result


def get_unit(pid):
    try:
        cg = open(f"/proc/{pid}/cgroup").read()
    except Exception:
        return None
    m = re.search(r"/([^/\n]+\.service)\s*$", cg, re.M)
    return m.group(1) if m else None


def get_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except Exception:
        return None


def get_title(port):
    out = sh(["curl", "-s", "-m", "3", "-L", f"http://127.0.0.1:{port}/"])
    m = re.search(r"<title[^>]*>(.*?)</title>", out, re.I | re.S)
    if not m:
        return None
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t or None


def http_status(port):
    out = sh(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-m", "3", f"http://127.0.0.1:{port}/"]
    )
    code = out.strip()
    return code if re.fullmatch(r"\d{3}", code) else None


def get_caddy_fronts():
    """Map a reverse-proxied localhost port to its public Caddy hostname."""
    fronts = {}
    try:
        txt = open(CADDYFILE).read()
    except Exception:
        return fronts
    site = None
    for line in txt.splitlines():
        s = line.strip()
        if s.endswith("{") and not s.startswith(("#", "//")) and len(s) > 1:
            site = s[:-1].strip().split()[0]
        elif site and "reverse_proxy" in s:
            m = re.search(r"127\.0\.0\.1:(\d+)", s)
            if m:
                fronts[int(m.group(1))] = site
        elif s == "}":
            site = None
    return fronts


def prettify(s):
    s = re.sub(r"\.service$", "", s or "")
    s = s.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in s.split()) or s


def pick_icon(text):
    t = (text or "").lower()
    for pat, emoji in KEYWORD_EMOJI:
        if re.search(pat, t):
            return emoji
    return "🌐"


def pick_color(port):
    return PALETTE[int(hashlib.md5(str(port).encode()).hexdigest(), 16) % len(PALETTE)]


def resolve_meta(port, pid):
    """Return (name, icon, color, desc) — from KNOWN or auto-detected."""
    if port in KNOWN:
        return KNOWN[port]
    unit = get_unit(pid)
    cwd = get_cwd(pid)
    title = get_title(port)
    if title and title.lower() not in GENERIC_TITLES:
        name = title
    elif unit:
        name = prettify(unit)
    elif cwd:
        name = prettify(os.path.basename(cwd))
    else:
        name = f"Port {port}"
    haystack = " ".join(x for x in (name, title, unit, cwd) if x)
    icon = pick_icon(haystack)
    color = pick_color(port)
    desc = prettify(unit) if unit else (cwd or "auto-detected service")
    return name, icon, color, desc


# ---------------------------------------------------------------------------
# HTML / CSS / JS (plain strings — no .format(), so CSS braces are literal).
# ---------------------------------------------------------------------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ian's Dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #e5e9f0;
    background:
      radial-gradient(1200px 600px at 15% -10%, rgba(99,102,241,.18), transparent 60%),
      radial-gradient(900px 500px at 110% 10%, rgba(14,165,233,.14), transparent 55%),
      #0a0e1a;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 40px 24px 60px; }
  header { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 14px; }
  h1 {
    margin: 0; font-size: 30px; font-weight: 700; letter-spacing: -.02em;
    background: linear-gradient(90deg, #fff, #a5b4fc 60%, #67e8f9);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .sub { color: #94a3b8; font-size: 13.5px; margin-top: 6px; }
  .status { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: #94a3b8; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #34d399;
         box-shadow: 0 0 0 0 rgba(52,211,153,.5); animation: pulse 2s infinite; }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(52,211,153,.5);} 70% { box-shadow: 0 0 0 7px rgba(52,211,153,0);} 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0);} }
  .pill { background: rgba(52,211,153,.12); border: 1px solid rgba(52,211,153,.35);
          color: #6ee7b7; padding: 3px 9px; border-radius: 999px; font-weight: 600; }
  .search { width: 100%; max-width: 380px; margin: 22px 0 8px; padding: 11px 16px;
            border-radius: 12px; border: 1px solid rgba(255,255,255,.08);
            background: rgba(255,255,255,.04); color: #e5e9f0; font-size: 14px; outline: none; }
  .search:focus { border-color: rgba(129,140,248,.55); background: rgba(255,255,255,.06); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); gap: 15px; margin-top: 18px; }
  a.tile {
    text-decoration: none; color: inherit; position: relative;
    background: rgba(255,255,255,.032); border: 1px solid rgba(255,255,255,.07);
    border-radius: 18px; padding: 20px 14px 16px; display: flex; flex-direction: column;
    align-items: center; text-align: center; gap: 9px; transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease;
  }
  a.tile:hover { transform: translateY(-4px); border-color: color-mix(in srgb, var(--c) 55%, transparent);
                 box-shadow: 0 14px 30px -12px color-mix(in srgb, var(--c) 70%, transparent); }
  .icon { width: 60px; height: 60px; border-radius: 17px; font-size: 30px; line-height: 60px;
          background: color-mix(in srgb, var(--c) 20%, transparent);
          border: 1px solid color-mix(in srgb, var(--c) 38%, transparent); }
  .tname { font-weight: 650; font-size: 14.5px; line-height: 1.2; }
  .tdesc { font-size: 11.5px; color: #8b94a7; line-height: 1.35;
           display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 30px; }
  .tmeta { font-size: 10.5px; color: #6b7488; margin-top: auto; }
  .tmeta b { color: #a5b4fc; font-weight: 600; }
  .dim { opacity: .55; }
  h2 { font-size: 15px; color: #a5b4fc; font-weight: 650; margin: 38px 0 12px; letter-spacing: .01em; }
  .sys { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: #cbd5e1;
          background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.07);
          padding: 6px 11px; border-radius: 999px; }
  .chip .e { font-size: 14px; }
  .chip .p { color: #6b7488; }
  footer { margin-top: 44px; padding-top: 18px; border-top: 1px solid rgba(255,255,255,.06);
           font-size: 12px; color: #6b7488; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Ian's Dashboard</h1>
      <div class="sub">Every service on <span class="mono">__SERVER__</span> · auto-discovers new ones</div>
    </div>
    <div class="status"><span class="dot"></span><span class="pill">LIVE</span><span id="ago">updated just now</span></div>
  </header>
  <input class="search" id="q" type="search" placeholder="Filter services…" autocomplete="off">
  <div class="grid" id="grid">
__TILES__
  </div>
  <h2>System &amp; internal</h2>
  <div class="sys">
__SYSTEM__
  </div>
  <footer>
    __COUNT__ services detected · list rebuilds every 60&nbsp;s and this page auto-refreshes ·
    <span class="mono">__TIME__</span>
  </footer>
</div>
<script>
  var SCAN_TS = __EPOCH__;
  function ago() {
    var s = Math.floor((Date.now()/1000) - SCAN_TS);
    var el = document.getElementById('ago');
    if (s < 5) el.textContent = 'updated just now';
    else if (s < 60) el.textContent = 'updated ' + s + 's ago';
    else el.textContent = 'updated ' + Math.floor(s/60) + 'm ' + (s%60) + 's ago';
  }
  setInterval(ago, 1000); ago();
  document.getElementById('q').addEventListener('input', function(e){
    var v = e.target.value.toLowerCase();
    document.querySelectorAll('#grid .tile').forEach(function(t){
      t.style.display = (t.dataset.f || '').indexOf(v) === -1 ? 'none' : '';
    });
  });
  setTimeout(function(){ location.reload(); }, 60000);
</script>
</body>
</html>
"""


def esc(s):
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def build_page(apps, system):
    tiles = []
    for a in apps:
        color = a["color"]
        name = esc(a["name"])
        icon = a["icon"]
        desc = esc(a["desc"])
        meta = f"port <b>{a['port']}</b>"
        if a.get("note"):
            meta += " · " + esc(a["note"])
        if a["url"]:
            tiles.append(
                f'<a class="tile" style="--c:{color}" href="{esc(a["url"])}" '
                f'target="_blank" rel="noopener" '
                f'data-f="{esc(a["name"] + " " + a["desc"] + " " + str(a["port"])).lower()}">'
                f'<div class="icon">{icon}</div><div class="tname">{name}</div>'
                f'<div class="tdesc">{desc}</div><div class="tmeta">{meta}</div></a>'
            )
        else:
            tiles.append(
                f'<div class="tile dim" style="--c:{color}" '
                f'data-f="{esc(a["name"] + " " + a["desc"] + " " + str(a["port"])).lower()}">'
                f'<div class="icon">{icon}</div><div class="tname">{name}</div>'
                f'<div class="tdesc">{desc}</div><div class="tmeta">{meta}</div></div>'
            )

    chips = []
    for s in system:
        chips.append(
            f'<span class="chip"><span class="e">{s["icon"]}</span>'
            f'{esc(s["name"])}<span class="p">:{s["port"]}</span></span>'
        )

    now = datetime.now()
    page = (
        HTML_HEAD.replace("__SERVER__", SERVER_IP)
        .replace("__TILES__", "\n".join(tiles))
        .replace("__SYSTEM__", "\n".join(chips) if chips else "&nbsp;")
        .replace("__COUNT__", str(len(apps)))
        .replace("__TIME__", now.strftime("%Y-%m-%d %H:%M:%S"))
        .replace("__EPOCH__", str(int(now.timestamp())))
    )
    return page


def main():
    listeners = get_listeners()
    fronts = get_caddy_fronts()

    apps = []
    system = []

    for port in sorted(listeners):
        info = listeners[port]
        if port in IGNORE or port == DASH_PORT:
            continue
        if port in INFRA:
            system.append({"name": INFRA[port][0], "icon": INFRA[port][1], "port": port})
            continue

        name, icon, color, desc = resolve_meta(port, info["pid"])

        # Reachability: prefer a public Caddy host, then the public IP, then
        # mark VPN-only / internal-only. Non-HTTP ports go to the system list.
        front = fronts.get(port)
        bind = info["bind"]
        url = None
        note = None
        if front:
            url = f"https://{front}"
            note = f"{front}"
        elif bind in ("127.0.0.1", "::1"):
            note = "internal only"
        elif bind == "10.0.0.1":
            url = f"http://10.0.0.1:{port}"
            note = "VPN only"
        else:
            url = f"http://{SERVER_IP}:{port}"
            note = None

        if port not in KNOWN and http_status(port) is None:
            system.append({"name": name, "icon": icon, "port": port})
            continue

        apps.append({
            "name": name, "icon": icon, "color": color, "desc": desc,
            "url": url, "port": port, "note": note,
        })

    apps.sort(key=lambda a: a["name"].lower())

    os.makedirs(WWW_DIR, exist_ok=True)
    page = build_page(apps, system)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(page)
    os.replace(tmp, INDEX_PATH)

    print(f"wrote {INDEX_PATH}: {len(apps)} apps, {len(system)} system entries")


if __name__ == "__main__":
    main()
