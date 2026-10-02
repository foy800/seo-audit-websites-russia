#!/usr/bin/env python3
"""Быстрая SEO-проверка сайта (только stdlib). Использование: quick_check.py https://example.ru [--pages 10]"""
import re
import ssl
import sys
import socket
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser

UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"
UA_GOOGLE = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_YANDEX = "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"

results = []


def add(level, title, detail=""):
    results.append((level, title, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url, ua=UA_BROWSER, follow=True, timeout=15, method="GET"):
    """Возвращает (status, headers, body_text, final_url) либо (None, {}, '', url) при ошибке."""
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "ru,en;q=0.8"}, method=method)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(req, timeout=timeout)
        body = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace") if method == "GET" else ""
        return r.status, r.headers, body, r.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read(200_000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, e.headers, body, url
    except Exception as e:
        return None, {}, str(e), url


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self._in_title = False
        self.metas = []
        self.links = []
        self.h = {"h1": [], "h2": [], "h3": []}
        self._cur_h = None
        self.imgs = []
        self.anchors = []
        self.jsonld = 0
        self._in_ld = False
        self.html_attrs = {}
        self.text_len = 0
        self._skip = 0
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_attrs = a
        elif tag == "title":
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag in self.h:
            self._cur_h = tag
            self.h[tag].append("")
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "a" and a.get("href"):
            self.anchors.append(a["href"])
        elif tag == "script":
            if (a.get("type") or "").lower() == "application/ld+json":
                self.jsonld += 1
            if a.get("src"):
                self.scripts.append(a["src"])
            self._skip += 1
        elif tag == "style":
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.h:
            self._cur_h = None
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title and self.title is not None:
            self.title += data
        if self._cur_h:
            self.h[self._cur_h][-1] += data
        if not self._skip:
            self.text_len += len(data.strip())

    def meta(self, name=None, prop=None):
        for m in self.metas:
            if name and (m.get("name") or "").lower() == name:
                return m.get("content")
            if prop and (m.get("property") or "").lower() == prop:
                return m.get("content")
        return None

    def link(self, rel):
        for l in self.links:
            if rel in (l.get("rel") or "").lower().split():
                return l.get("href")
        return None


def check_ssl(host):
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert()
        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        if days < 0:
            add("❌", "SSL-сертификат истёк", f"{-days} дн. назад")
        elif days < 14:
            add("⚠️", "SSL-сертификат скоро истекает", f"осталось {days} дн.")
        else:
            add("✅", "SSL-сертификат валиден", f"осталось {days} дн.")
    except ssl.SSLError as e:
        add("❌", "Ошибка SSL", str(e)[:120])
    except Exception as e:
        add("⚠️", "SSL не проверен", str(e)[:120])


def analyze_page(url, html, headers, is_home):
    p = Page()
    try:
        p.feed(html)
    except Exception:
        pass
    tag = "главная" if is_home else url
    robots = (p.meta(name="robots") or "").lower()
    yandex = (p.meta(name="yandex") or "").lower()
    xrt = (headers.get("X-Robots-Tag") or "").lower() if headers else ""
    if "noindex" in robots or "noindex" in yandex or "noindex" in xrt:
        add("❌", f"noindex [{tag}]", f"robots='{robots}' yandex='{yandex}' X-Robots-Tag='{xrt}'")
    elif is_home:
        add("✅", "noindex не найден")

    t = (p.title or "").strip()
    if not t:
        add("❌", f"Нет <title> [{tag}]")
    elif not 20 <= len(t) <= 70:
        add("⚠️", f"Длина title {len(t)} симв. [{tag}]", t[:90])
    elif is_home:
        add("✅", f"title ({len(t)} симв.)", t)

    d = (p.meta(name="description") or "").strip()
    if not d:
        add("❌", f"Нет meta description [{tag}]")
    elif not 50 <= len(d) <= 180:
        add("⚠️", f"Длина description {len(d)} симв. [{tag}]")
    elif is_home:
        add("✅", f"meta description ({len(d)} симв.)")

    h1 = [x.strip() for x in p.h["h1"] if x.strip()]
    if len(h1) == 0:
        add("❌", f"Нет H1 в исходном HTML [{tag}]", "возможно CSR без SSR" if p.text_len < 300 else "")
    elif len(h1) > 1:
        add("⚠️", f"H1 больше одного ({len(h1)}) [{tag}]")
    elif is_home:
        add("✅", "Один H1", h1[0][:80])
    if p.h["h3"] and not p.h["h2"]:
        add("⚠️", f"H3 без H2 [{tag}]")

    can = p.link("canonical")
    if not can:
        add("⚠️", f"Нет canonical [{tag}]")
    elif re.search(r"localhost|127\.0\.0\.1|\.vercel\.app|\.netlify\.app|\.pages\.dev|\.lovable", can):
        add("❌", f"canonical на dev/превью-домен [{tag}]", can)
    elif is_home:
        add("✅", "canonical", can)

    if is_home:
        if not (p.html_attrs.get("lang")):
            add("⚠️", "Нет lang у <html>")
        if not p.meta(name="viewport"):
            add("❌", "Нет meta viewport (не адаптивно)")
        else:
            add("✅", "viewport есть")
        og = [k for k in ("og:title", "og:description", "og:image", "og:url") if p.meta(prop=k)]
        miss = [k for k in ("og:title", "og:description", "og:image", "og:url") if k not in og]
        if miss:
            add("⚠️", "Не хватает Open Graph", ", ".join(miss))
        else:
            add("✅", "Open Graph полный")
        img = p.meta(prop="og:image")
        if img:
            if img.startswith("/") or not img.startswith("http"):
                add("⚠️", "og:image не абсолютный URL", img)
            elif re.search(r"localhost|vercel\.app", img):
                add("❌", "og:image на dev-домен", img)
        ogu = p.meta(prop="og:url") or ""
        if re.search(r"localhost|127\.0\.0\.1", ogu):
            add("❌", "og:url на localhost", ogu)
        add("✅" if p.jsonld else "⚠️", f"Schema.org JSON-LD блоков: {p.jsonld}")
        add("✅" if p.link("icon") or p.link("shortcut") else "⚠️", "Фавикон в <head>" if p.link("icon") or p.link("shortcut") else "Нет <link rel=icon>")
        if p.meta(name="yandex-verification"):
            add("✅", "Есть метатег Яндекс Вебмастера")
        else:
            add("ℹ️", "Метатег yandex-verification не найден (может быть подтверждение файлом/DNS)")
        if p.meta(name="google-site-verification"):
            add("✅", "Есть метатег Google Search Console")
        else:
            add("ℹ️", "Метатег google-site-verification не найден (возможно, подтверждение через DNS/файл)")
        if "mc.yandex.ru" in html or "ym(" in html:
            add("✅", "Яндекс Метрика найдена")
        else:
            add("⚠️", "Яндекс Метрика не найдена")
        if re.search(r"fonts\.googleapis|fonts\.gstatic|googletagmanager|google-analytics|recaptcha|connect\.facebook|facebook\.net", html):
            add("⚠️", "Внешние ресурсы, которые могут не грузиться/быть нежелательны в РФ",
                "Google Fonts/GTM/GA/reCAPTCHA/Facebook")
        if "hash" in html.lower() and re.search(r'href="#/', html):
            add("❌", "Hash-роутинг (#/…) — такие URL не индексируются")
        if p.text_len < 200:
            add("❌", "Мало текста в исходном HTML — вероятен CSR (пустой root)", f"{p.text_len} симв.")
        else:
            add("✅", f"Текст виден без JS ({p.text_len} симв.)")

    noalt = [i for i in p.imgs if "alt" not in i]
    if noalt:
        add("⚠️", f"Картинок без alt: {len(noalt)} из {len(p.imgs)} [{tag}]")
    return p


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    max_pages = 10
    if "--pages" in sys.argv:
        max_pages = int(sys.argv[sys.argv.index("--pages") + 1])
    if not url.startswith("http"):
        url = "https://" + url
    pu = urllib.parse.urlparse(url)
    host = pu.netloc
    base = f"{pu.scheme}://{host}"

    # 1. Доступность и редиректы
    st, hd, html, final = fetch(url)
    if st is None:
        add("❌", "Сайт недоступен", html[:150])
        return report()
    add("✅" if st == 200 else "❌", f"Главная отвечает {st}", final)
    if final.rstrip("/") != url.rstrip("/"):
        add("ℹ️", "Редирект главной", f"{url} → {final}")
    if pu.scheme == "https":
        check_ssl(host)
        st_http, _, _, f_http = fetch("http://" + host, follow=True)
        add("✅" if f_http.startswith("https://") else "❌", "HTTP → HTTPS редирект", f_http)
    alt = host[4:] if host.startswith("www.") else "www." + host
    st2, _, _, f2 = fetch(f"{pu.scheme}://{alt}", follow=True)
    if st2 is not None:
        same = urllib.parse.urlparse(f2).netloc == urllib.parse.urlparse(final).netloc
        add("✅" if same else "⚠️", "Зеркала www/без www склеены 301" if same else "www и без www — разные зеркала (дубли)", f"{alt} → {f2}")

    # 2. Боты
    for name, ua in (("Googlebot", UA_GOOGLE), ("YandexBot", UA_YANDEX)):
        s, _, b, _ = fetch(url, ua=ua)
        if s in (403, 429, 503) or (b and re.search(r"Just a moment|cf-challenge|Attention Required|captcha", b[:3000], re.I) and s != 200):
            add("❌", f"{name} получает {s} (возможно, Cloudflare/WAF-челлендж)")
        elif s == 200 and re.search(r"Just a moment|cf-challenge", b[:5000], re.I):
            add("❌", f"{name} видит страницу-челлендж")
        else:
            add("✅", f"{name} получает {s}")

    # 3. Главная
    home = analyze_page(final, html, hd, True)

    # 4. robots.txt
    s, _, rb, _ = fetch(base + "/robots.txt")
    sitemaps = []
    if s != 200 or "<html" in rb[:500].lower():
        add("❌", "robots.txt отсутствует или отдаёт HTML", f"статус {s}")
    else:
        add("✅", "robots.txt найден")
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", rb)
        if re.search(r"(?im)^\s*disallow:\s*/\s*$", rb):
            ua_blocks = re.split(r"(?im)^\s*user-agent:", rb)
            bad = [b.splitlines()[0].strip() for b in ua_blocks[1:] if re.search(r"(?im)^\s*disallow:\s*/\s*$", b)]
            add("❌", "robots.txt содержит Disallow: /", "для: " + ", ".join(bad))
        if not sitemaps:
            add("⚠️", "В robots.txt нет директивы Sitemap")
        if re.search(r"(?im)^\s*host:", rb):
            add("ℹ️", "Директива Host устарела (Яндекс её не использует)")
        ai = re.findall(r"(?i)user-agent:\s*(GPTBot|ClaudeBot|PerplexityBot|OAI-SearchBot|YandexAdditional|Google-Extended)", rb)
        if ai:
            add("ℹ️", "Правила для ИИ-ботов", ", ".join(sorted(set(ai))))

    # 5. sitemap
    urls = []
    for sm in sitemaps or [base + "/sitemap.xml"]:
        s, _, sb, _ = fetch(sm)
        if s == 200 and "<urlset" in sb or "<sitemapindex" in sb:
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sb)
            add("✅", f"sitemap найден: {len(locs)} записей", sm)
            if re.search(r"localhost|vercel\.app", sb):
                add("❌", "В sitemap есть localhost/vercel.app URL")
            if "<sitemapindex" not in sb:
                urls += locs
            else:
                for sub in locs[:3]:
                    s2, _, sb2, _ = fetch(sub)
                    if s2 == 200:
                        urls += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sb2)
            break
        else:
            add("❌", "sitemap недоступен", f"{sm} → {s}")

    # 6. soft 404
    s404, _, _, _ = fetch(base + "/nonexistent-page-seo-check-xyz")
    add("✅" if s404 == 404 else "❌", f"Несуществующий URL отдаёт {s404}" + ("" if s404 == 404 else " (soft 404)"))

    # 7. llms.txt
    s, _, _, _ = fetch(base + "/llms.txt")
    add("✅" if s == 200 else "ℹ️", "llms.txt найден" if s == 200 else "llms.txt не найден (желательно)")

    # 8. Выборка страниц: внутренние ссылки + sitemap
    internal = []
    for a in home.anchors:
        full = urllib.parse.urljoin(final, a.split("#")[0])
        if urllib.parse.urlparse(full).netloc == urllib.parse.urlparse(final).netloc and full not in internal and full != final:
            internal.append(full)
    if re.search(r'href="#/', html):
        pass
    sample = list(dict.fromkeys(urls[:max_pages] + internal[:max_pages]))[:max_pages]
    titles, descs = {}, {}
    broken = []
    for u in sample:
        s, h, b, f = fetch(u)
        if s is None or s >= 400:
            broken.append(f"{u} → {s}")
            continue
        if "text/html" not in (h.get("Content-Type") or ""):
            continue
        pg = analyze_page(u, b, h, False)
        titles.setdefault((pg.title or "").strip(), []).append(u)
        descs.setdefault((pg.meta(name="description") or "").strip(), []).append(u)
    if broken:
        add("❌", f"Битые ссылки/страницы: {len(broken)}", "; ".join(broken[:8]))
    elif sample:
        add("✅", f"Проверено страниц: {len(sample)}, битых нет")
    for k, v in titles.items():
        if k and len(v) > 1:
            add("⚠️", f"Дубль title на {len(v)} страницах", k[:70])
    for k, v in descs.items():
        if k and len(v) > 1:
            add("⚠️", f"Дубль description на {len(v)} страницах", k[:70])

    report()


def report():
    order = {"❌": 0, "⚠️": 1, "ℹ️": 2, "✅": 3}
    for lvl, t, d in sorted(results, key=lambda r: order[r[0]]):
        print(f"{lvl} {t}" + (f" — {d}" if d else ""))
    bad = sum(1 for r in results if r[0] == "❌")
    warn = sum(1 for r in results if r[0] == "⚠️")
    ok = sum(1 for r in results if r[0] == "✅")
    print(f"\nИтого: ❌ {bad}  ⚠️ {warn}  ✅ {ok}")
    print("Не проверено автоматически: Вебмастер/Search Console, скорость из РФ, беклинки, регион, юр. требования.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
