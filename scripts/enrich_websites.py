#!/usr/bin/env python3
"""Add each company's actual homepage URL to data/companies.js(.json) as `web`.

Source waterfall:
  1. Yahoo Finance quoteSummary assetProfile.website  (primary — cleanest, per-ticker)
  2. Wikidata official-website (P856) keyed by exchange ticker (P249)  (gap-fill)
Companies with no URL from either source keep no `web` field; the map falls back
to a Google search link for those.

Resumable: progress is cached (ticker -> url, "" = known miss) so re-runs only
fetch what's missing. Rerun after the data pipeline regenerates companies.js.

Usage: python3 scripts/enrich_websites.py [--cache /path/to/cache.json]
"""
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(sys.argv[sys.argv.index('--cache') + 1]) if '--cache' in sys.argv \
    else REPO / 'scripts' / '.websites_cache.json'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'


def clean(url):
    """Keep only plausible http(s) URLs."""
    if not url:
        return ''
    url = url.strip()
    if not url.lower().startswith(('http://', 'https://')):
        return ''
    if len(url) > 200 or ' ' in url:
        return ''
    return url


class Yahoo:
    def __init__(self):
        self.opener = None
        self.crumb = None
        self._login()

    def _login(self):
        cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self.opener.addheaders = [('User-Agent', UA)]
        try:
            self.opener.open('https://fc.yahoo.com', timeout=30)
        except urllib.error.HTTPError:
            pass  # fc.yahoo.com 404s but still sets the cookie
        self.crumb = self.opener.open(
            'https://query1.finance.yahoo.com/v1/test/getcrumb', timeout=30).read().decode()

    def website(self, ticker):
        """Returns (url_or_empty, ok). ok=False means transient failure — retry later."""
        u = ('https://query2.finance.yahoo.com/v10/finance/quoteSummary/'
             f'{urllib.parse.quote(ticker)}?modules=assetProfile&crumb='
             + urllib.parse.quote(self.crumb))
        try:
            d = json.load(self.opener.open(u, timeout=30))
            r = d.get('quoteSummary', {}).get('result')
            if not r:
                return '', True
            return clean(r[0].get('assetProfile', {}).get('website', '')), True
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return '', True          # unknown symbol — a real miss
            if e.code in (401, 403):
                self._login()            # crumb expired
                return '', False
            if e.code == 429:
                time.sleep(30)           # throttled — back off
                return '', False
            return '', False
        except Exception:
            return '', False


def wikidata():
    q = """SELECT ?ticker ?website WHERE {
      ?company p:P414 ?ex . ?ex pq:P249 ?ticker . ?company wdt:P856 ?website .
      VALUES ?exchange { wd:Q13677 wd:Q82059 wd:Q846626 wd:Q7112944 }
      ?ex ps:P414 ?exchange . }"""
    url = 'https://query.wikidata.org/sparql?format=json&query=' + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={'User-Agent': 'DavidCao-CompanyMap/1.0 (davidcao139@gmail.com)'})
    d = json.load(urllib.request.urlopen(req, timeout=180))
    out = {}
    for r in d['results']['bindings']:
        t = r['ticker']['value'].upper()
        out.setdefault(t, clean(r['website']['value']))
    return out


def main():
    companies = json.loads((REPO / 'data' / 'companies.json').read_text())
    tickers = [c['ticker'].upper() for c in companies if c.get('ticker')]
    cache = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text())
    todo = [t for t in tickers if t not in cache]
    print(f'{len(tickers)} tickers; {len(cache)} cached; {len(todo)} to fetch from Yahoo', flush=True)

    y = Yahoo()
    fails = 0
    for i, t in enumerate(todo):
        url, ok = y.website(t)
        if ok:
            cache[t] = url
            fails = 0
        else:
            fails += 1
            if fails >= 10:
                print('too many consecutive failures — flushing cache and pausing 120s', flush=True)
                CACHE.write_text(json.dumps(cache))
                time.sleep(120)
                y._login()
                fails = 0
        if (i + 1) % 200 == 0:
            CACHE.write_text(json.dumps(cache))
            hits = sum(1 for v in cache.values() if v)
            print(f'  {i + 1}/{len(todo)} fetched — {hits} sites found so far', flush=True)
        time.sleep(0.35)
    CACHE.write_text(json.dumps(cache))

    missing = [t for t in tickers if not cache.get(t)]
    print(f'Yahoo done: {sum(1 for t in tickers if cache.get(t))} hits, {len(missing)} misses; trying Wikidata…', flush=True)
    try:
        wd = wikidata()
        wd_used = 0
        for t in missing:
            if wd.get(t):
                cache[t] = wd[t]
                wd_used += 1
        print(f'Wikidata filled {wd_used} more', flush=True)
        CACHE.write_text(json.dumps(cache))
    except Exception as e:
        print('Wikidata pass failed (non-fatal):', e, flush=True)

    n = 0
    for c in companies:
        url = cache.get((c.get('ticker') or '').upper())
        if url:
            c['web'] = url
            n += 1
        else:
            c.pop('web', None)
    print(f'coverage: {n}/{len(companies)} companies have a website URL', flush=True)
    (REPO / 'data' / 'companies.json').write_text(json.dumps(companies))
    (REPO / 'data' / 'companies.js').write_text('window.COMPANIES = ' + json.dumps(companies) + ';\n')
    print('wrote data/companies.json and data/companies.js', flush=True)


if __name__ == '__main__':
    main()
