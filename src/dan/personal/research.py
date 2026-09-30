"""Personal research uses the Workspace search and page-reading tools."""
import asyncio
import re
import unicodedata
from urllib.parse import urlsplit
from itertools import zip_longest

from dan.tools.web_search import web_search
from dan.tools.web_fetch import web_fetch
from dan.tools._public_web import public_url
from .store import now
from .runner import redact_source


async def collect(queries, urls, authorized, progress):
    sources = []
    failures = []
    progress('Searching…' if queries else 'Reading…')

    async def search(query):
        if not authorized():
            raise ValueError('Research stopped')
        try:
            result = await asyncio.wait_for(web_search(query=redact_source(query), num_results=5,
                search_depth='quick', fetch_content=False, browser_fallback=False,
                multi_provider=False, max_provider_searches=2), 35)
            return result
        except Exception:
            return {'results': [], 'unavailable': True}

    async with asyncio.timeout(85):
        results = await asyncio.gather(*(search(query) for query in queries[:2]))
        for url in urls[:3]:
            try:
                public_url(url)
            except ValueError:
                failures.append('A requested URL is not a public page.'); continue
            sources.append({'url': url, 'title': url, 'excerpt': '', 'kind': 'requested_page', 'checked_at': now()})
        for result in results:
            if not result.get('results'):
                failures.append('A search returned no usable results.')
        # Give each query a page-read slot, rather than exhausting the first query.
        for batch in zip_longest(*(result.get('results', [])[:5] for result in results)):
            for query_index, row in enumerate(batch):
                if row is None:
                    continue
                url = str(row.get('url', ''))
                try:
                    public_url(url)
                except ValueError:
                    continue
                if any(source['url'] == url for source in sources):
                    continue
                sources.append({'url': url, 'title': str(row.get('title', url))[:240],
                    'excerpt': str(row.get('snippet', ''))[:1600], 'kind': 'search_result',
                    'query_index': query_index, 'checked_at': now(), 'search_cached': bool(result.get('cache_hit'))})
        sources = sources[:8]
        progress('Reading…')

        menu_links = []
        async def read(source):
            if not authorized():
                raise ValueError('Research stopped')
            try:
                page = await web_fetch(source['url'], timeout=20, max_length=5000, public_only=True)
                if page.get('content_requires_browser') or not str(page.get('content', '')).strip():
                    source['read_error'] = 'Page requires an interactive browser or has no readable text.'
                else:
                    for link in page.get('links', []):
                        menu_links.append(link)
                    source.update(url=page['url'], search_excerpt=source['excerpt'], excerpt=page['content'], kind='page', checked_at=now())
            except Exception:
                source['read_error'] = 'Page could not be read; only the search excerpt is available.'

        def priority(source):
            if source['kind'] == 'requested_page':
                return 100
            plain = unicodedata.normalize('NFKD', ' '.join(queries)).encode('ascii', 'ignore').decode().lower()
            terms = set(re.findall(r'[a-z]{4,}', plain)) - {'restaurant', 'restaurants', 'official', 'dinner', 'menu', 'menus', 'prices', 'opening', 'hours', 'hong', 'kong'}
            host = urlsplit(source['url']).hostname or ''
            return sum(len(term) for term in terms if term in host)
        ranked = sorted(sources, key=priority, reverse=True)
        selected = [source for source in ranked if source['kind'] == 'requested_page'][:3]
        for index in range(len(queries)):
            candidate = next((source for source in ranked if source.get('query_index') == index), None)
            if candidate is not None and candidate not in selected and len(selected) < 3:
                selected.append(candidate)
        selected += [source for source in ranked if source not in selected][:3-len(selected)]
        await asyncio.gather(*(read(source) for source in selected))
        # Follow actual menu links once; never invent URLs or traverse a site unboundedly.
        menus = []
        for link in sorted(menu_links, key=lambda item: ('.pdf' not in item['url'].lower(), 'dinner' not in item['title'].lower())):
            try:
                public_url(link['url'])
            except ValueError:
                continue
            if any(source['url'] == link['url'] for source in sources + menus):
                continue
            menus.append({'url': link['url'], 'title': link['title'], 'excerpt': '', 'kind': 'requested_page', 'checked_at': now()})
            if len(menus) == 2:
                break
        await asyncio.gather(*(read(source) for source in menus))
        sources.extend(menus)
    for index, source in enumerate(sources):
        source['id'] = f'S{index + 1}'
    return {'queries': queries, 'sources': sources, 'failures': failures, 'checked_at': now()}


def references(choice, evidence):
    known = {source['id']: source for source in evidence['sources'] if source['excerpt'].strip()}
    if any(identity not in known for identity in choice.citations):
        raise ValueError('The AI returned an unverified source. Please ask me to try again.')
    selected = choice.citations or list(known)[:3]
    return [{key: known[identity][key] for key in ('id', 'url', 'title', 'kind', 'checked_at')} for identity in dict.fromkeys(selected)]
