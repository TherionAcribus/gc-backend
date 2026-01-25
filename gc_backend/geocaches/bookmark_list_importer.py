"""
Importer for Geocaching.com Bookmark Lists.

This module handles importing geocaches from bookmark lists using either:
1. Web scraping (for users without API access)
2. Geocaching.com API (if available)
"""

from __future__ import annotations

import logging
import re
from typing import Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class BookmarkListImporter:
    """Import geocaches from Geocaching.com bookmark lists."""
    
    BOOKMARK_LIST_URL = 'https://www.geocaching.com/plan/lists/'
    USER_LISTS_URL = 'https://www.geocaching.com/my/lists.aspx'
    
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault('User-Agent', 'GeoApp/1.0 (+https://example.local)')
    
    @staticmethod
    def validate_bookmark_code(code: str) -> str:
        """Validate and normalize a bookmark list code (e.g., BM1234)."""
        normalized = (code or '').strip().upper()
        if not re.match(r'^BM[0-9A-Z]+$', normalized):
            raise ValueError('invalid_bookmark_code')
        return normalized
    
    def get_geocaches_from_list(self, bookmark_code: str) -> list[str]:
        """
        Get all geocache codes from a bookmark list.
        
        Args:
            bookmark_code: The bookmark list code (e.g., BM1234)
            
        Returns:
            List of GC codes found in the bookmark list
            
        Raises:
            ValueError: If bookmark code is invalid
            LookupError: If bookmark list not found or not accessible
            RuntimeError: If scraping fails
        """
        code = self.validate_bookmark_code(bookmark_code)
        logger.info(f"Fetching geocaches from bookmark list {code}")
        
        gc_codes = []
        
        # Try the Next.js data endpoint first
        logger.debug(f"Starting geocache extraction for {code}")
        try:
            build_id = 'release-20260122.1.2725'  # Default build ID
            nextjs_url = f'https://www.geocaching.com/_next/data/{build_id}/en/plan/lists/{code}.json?bmCode={code}'
            logger.debug(f"Trying Next.js data endpoint: {nextjs_url}")
            
            resp = self.session.get(nextjs_url, timeout=30)
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    logger.debug(f"Next.js response received, parsing data...")
                    
                    # Extract geocaches from pageProps
                    if isinstance(data, dict):
                        page_props = data.get('pageProps', {})
                        logger.debug(f"pageProps keys: {list(page_props.keys()) if page_props else 'None'}")
                        
                        # Look for geocaches in various possible locations
                        geocaches = (
                            page_props.get('geocaches') or
                            page_props.get('items') or
                            page_props.get('caches') or
                            []
                        )
                        
                        if isinstance(geocaches, list):
                            for item in geocaches:
                                if isinstance(item, dict):
                                    gc_code = item.get('referenceCode') or item.get('code') or item.get('gcCode')
                                    if gc_code and gc_code.startswith('GC'):
                                        if gc_code not in gc_codes:
                                            gc_codes.append(gc_code)
                        
                        # Also check if there's a 'list' object with geocaches
                        list_obj = page_props.get('list', {})
                        if isinstance(list_obj, dict):
                            list_geocaches = list_obj.get('geocaches') or list_obj.get('items') or []
                            if isinstance(list_geocaches, list):
                                for item in list_geocaches:
                                    if isinstance(item, dict):
                                        gc_code = item.get('referenceCode') or item.get('code') or item.get('gcCode')
                                        if gc_code and gc_code.startswith('GC'):
                                            if gc_code not in gc_codes:
                                                gc_codes.append(gc_code)
                    
                    if gc_codes:
                        logger.info(f"Found {len(gc_codes)} geocaches from Next.js data")
                        return gc_codes
                    else:
                        logger.debug(f"No geocaches found in Next.js data structure")
                        
                except Exception as e:
                    logger.debug(f"Failed to parse Next.js data: {e}", exc_info=True)
            else:
                logger.debug(f"Next.js endpoint returned status {resp.status_code}")
        except Exception as e:
            logger.debug(f"Next.js endpoint failed: {e}", exc_info=True)
        
        # Fallback: Try the HTML page
        logger.debug(f"Falling back to HTML page scraping")
        url = f'{self.BOOKMARK_LIST_URL}{code}'
        
        try:
            resp = self.session.get(url, timeout=30)
            
            if resp.status_code == 404:
                logger.warning(f"Bookmark list {code} not found (404)")
                raise LookupError('bookmark_list_not_found')
            
            if resp.status_code == 403:
                logger.warning(f"Bookmark list {code} is private or requires authentication (403)")
                raise LookupError('bookmark_list_private')
            
            resp.raise_for_status()
            
        except requests.RequestException as e:
            logger.error(f"Failed to fetch bookmark list {code}: {e}")
            raise RuntimeError(f"Failed to fetch bookmark list: {e}") from e
        
        # Parse HTML to extract geocache codes
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Look for JSON data embedded in script tags
        for script in soup.find_all('script'):
            script_text = script.string or ''
            if 'GC' in script_text and ('geocache' in script_text.lower() or 'referenceCode' in script_text):
                # Try to extract GC codes from JSON
                for match in re.finditer(r'"(?:referenceCode|code|gcCode)"\s*:\s*"(GC[0-9A-Z]+)"', script_text):
                    gc_code = match.group(1).upper()
                    if gc_code not in gc_codes:
                        gc_codes.append(gc_code)
        
        if gc_codes:
            logger.info(f"Found {len(gc_codes)} geocaches from embedded JSON")
            return gc_codes
        
        # Method 1: Look for GC codes in links
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Match patterns like /geocache/GC12345 or /seek/cache_details.aspx?wp=GC12345
            match = re.search(r'/geocache/(GC[0-9A-Z]+)', href)
            if not match:
                match = re.search(r'[?&]wp=(GC[0-9A-Z]+)', href)
            if match:
                gc_code = match.group(1).upper()
                if gc_code not in gc_codes:
                    gc_codes.append(gc_code)
        
        # Method 2: Look for GC codes in data attributes or text
        for elem in soup.find_all(attrs={'data-geocache-code': True}):
            gc_code = elem['data-geocache-code'].upper()
            if gc_code not in gc_codes and re.match(r'^GC[0-9A-Z]+$', gc_code):
                gc_codes.append(gc_code)
        
        # Method 3: Search for GC codes in text content
        text_content = soup.get_text()
        for match in re.finditer(r'\b(GC[0-9A-Z]{3,})\b', text_content):
            gc_code = match.group(1).upper()
            if gc_code not in gc_codes:
                gc_codes.append(gc_code)
        
        logger.info(f"Found {len(gc_codes)} geocaches in bookmark list {code}")
        
        if not gc_codes:
            logger.warning(f"No geocaches found in bookmark list {code}")
            raise LookupError('no_geocaches_in_list')
        
        return gc_codes
    
    def get_list_info(self, bookmark_code: str) -> dict:
        """
        Get information about a bookmark list.
        
        Args:
            bookmark_code: The bookmark list code (e.g., BM1234)
            
        Returns:
            Dictionary with list information (name, description, count, etc.)
        """
        code = self.validate_bookmark_code(bookmark_code)
        url = f'{self.BOOKMARK_LIST_URL}{code}'
        
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch bookmark list info for {code}: {e}")
            return {'code': code, 'name': code, 'count': 0}
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Try to extract list name
        name = code
        title_elem = soup.find('h1')
        if title_elem:
            name = title_elem.get_text(strip=True)
        
        # Try to extract description
        description = None
        desc_elem = soup.find('div', class_='description')
        if desc_elem:
            description = desc_elem.get_text(strip=True)
        
        return {
            'code': code,
            'name': name,
            'description': description,
            'url': url
        }
    
    def get_user_bookmark_lists(self) -> list[dict]:
        """
        Get all bookmark lists for the authenticated user.
        
        Returns:
            List of dictionaries with list information (code, name, count, etc.)
        """
        logger.info("Fetching user's bookmark lists")
        
        # Try the Next.js data endpoint first (modern approach)
        try:
            # Try to get the build ID from the main page first
            build_id = 'release-20260122.1.2725'  # Default, will be updated if we can detect it
            
            # Try the Next.js data endpoint for the lists page
            nextjs_url = f'https://www.geocaching.com/_next/data/{build_id}/en/plan/lists.json'
            logger.debug(f"Trying Next.js data endpoint: {nextjs_url}")
            resp = self.session.get(nextjs_url, timeout=30)
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    lists = []
                    
                    # The Next.js data structure has lists in pageProps.lists.data
                    if isinstance(data, dict):
                        page_props = data.get('pageProps', {})
                        lists_obj = page_props.get('lists', {})
                        
                        # The lists are in the 'data' array
                        if isinstance(lists_obj, dict):
                            lists_data = lists_obj.get('data', [])
                        else:
                            lists_data = lists_obj if isinstance(lists_obj, list) else []
                        
                        if isinstance(lists_data, list):
                            for item in lists_data:
                                if isinstance(item, dict):
                                    code = item.get('referenceCode')
                                    if code and code.startswith('BM'):
                                        lists.append({
                                            'code': code,
                                            'name': item.get('name', code),
                                            'count': item.get('count', 0),
                                            'url': f'{self.BOOKMARK_LIST_URL}{code}'
                                        })
                                        logger.debug(f"Found list from Next.js: {code} - {item.get('name')} ({item.get('count')} caches)")
                    
                    if lists:
                        logger.info(f"Found {len(lists)} bookmark lists from Next.js data")
                        return lists
                except Exception as e:
                    logger.debug(f"Failed to parse Next.js data response: {e}")
        except Exception as e:
            logger.debug(f"Next.js data endpoint failed: {e}")
        
        # Try the API endpoint
        try:
            api_url = 'https://www.geocaching.com/api/proxy/web/v1/users/me/lists'
            logger.debug(f"Trying API endpoint: {api_url}")
            resp = self.session.get(api_url, timeout=30)
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    lists = []
                    
                    # Parse the API response
                    if isinstance(data, dict) and 'lists' in data:
                        for item in data['lists']:
                            code = item.get('referenceCode') or item.get('code')
                            if code and code.startswith('BM'):
                                lists.append({
                                    'code': code,
                                    'name': item.get('name', code),
                                    'count': item.get('geocacheCount', 0) or item.get('count', 0),
                                    'url': f'{self.BOOKMARK_LIST_URL}{code}'
                                })
                    elif isinstance(data, list):
                        for item in data:
                            code = item.get('referenceCode') or item.get('code')
                            if code and code.startswith('BM'):
                                lists.append({
                                    'code': code,
                                    'name': item.get('name', code),
                                    'count': item.get('geocacheCount', 0) or item.get('count', 0),
                                    'url': f'{self.BOOKMARK_LIST_URL}{code}'
                                })
                    
                    if lists:
                        logger.info(f"Found {len(lists)} bookmark lists from API")
                        return lists
                except Exception as e:
                    logger.debug(f"Failed to parse API response: {e}")
        except Exception as e:
            logger.debug(f"API endpoint failed: {e}")
        
        # Fallback: Try scraping the HTML page
        try:
            resp = self.session.get(self.USER_LISTS_URL, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch user's bookmark lists: {e}")
            return []
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        lists = []
        
        logger.debug(f"Page title: {soup.title.string if soup.title else 'No title'}")
        
        # Check if there's JSON data embedded in the page
        for script in soup.find_all('script'):
            script_text = script.string or ''
            # Look for JSON data containing lists
            if 'BM' in script_text and ('lists' in script_text.lower() or 'bookmark' in script_text.lower()):
                # Try to extract JSON
                json_match = re.search(r'(\{.*"lists".*\}|\[.*"referenceCode".*\])', script_text, re.DOTALL)
                if json_match:
                    try:
                        import json
                        json_data = json.loads(json_match.group(1))
                        
                        items = json_data.get('lists', []) if isinstance(json_data, dict) else json_data
                        for item in items:
                            if isinstance(item, dict):
                                code = item.get('referenceCode') or item.get('code')
                                if code and code.startswith('BM'):
                                    lists.append({
                                        'code': code,
                                        'name': item.get('name', code),
                                        'count': item.get('geocacheCount', 0) or item.get('count', 0),
                                        'url': f'{self.BOOKMARK_LIST_URL}{code}'
                                    })
                        
                        if lists:
                            logger.info(f"Found {len(lists)} bookmark lists from embedded JSON")
                            return lists
                    except Exception as e:
                        logger.debug(f"Failed to parse embedded JSON: {e}")
        
        # Method 1: Look for ALL links with /plan/lists/ pattern (most reliable)
        for link in soup.find_all('a', href=True):
            href = link['href']
            match = re.search(r'/plan/lists/(BM[0-9A-Z]+)', href)
            if match:
                code = match.group(1)
                
                # Skip if already found
                if any(l['code'] == code for l in lists):
                    continue
                
                # Get name from link text
                name = link.get_text(strip=True)
                if not name or len(name) < 2:
                    name = code
                
                # Try to find count in surrounding context
                count = 0
                
                # Look in parent elements for count
                for parent in [link.parent, link.find_parent('div'), link.find_parent('tr'), link.find_parent('li')]:
                    if parent:
                        parent_text = parent.get_text()
                        # Look for patterns like "123 caches", "123 items", "123"
                        count_match = re.search(r'(\d+)\s*(?:cache|géocache|item|result)', parent_text, re.IGNORECASE)
                        if count_match:
                            count = int(count_match.group(1))
                            break
                        # Also try just a number near the link
                        numbers = re.findall(r'\b(\d+)\b', parent_text)
                        if numbers:
                            # Take the first reasonable number (not too large)
                            for num in numbers:
                                num_int = int(num)
                                if 0 < num_int < 10000:
                                    count = num_int
                                    break
                        if count > 0:
                            break
                
                lists.append({
                    'code': code,
                    'name': name,
                    'count': count,
                    'url': f'{self.BOOKMARK_LIST_URL}{code}'
                })
                
                logger.debug(f"Found list: {code} - {name} ({count} caches)")
        
        # Method 2: Look for data attributes (if modern page structure)
        for elem in soup.find_all(attrs={'data-list-code': True}):
            code = elem.get('data-list-code', '').strip().upper()
            if code and re.match(r'^BM[0-9A-Z]+$', code):
                if any(l['code'] == code for l in lists):
                    continue
                    
                name = elem.get('data-list-name', code)
                count = int(elem.get('data-list-count', 0))
                
                lists.append({
                    'code': code,
                    'name': name,
                    'count': count,
                    'url': f'{self.BOOKMARK_LIST_URL}{code}'
                })
                
                logger.debug(f"Found list from data attr: {code} - {name} ({count} caches)")
        
        logger.info(f"Found {len(lists)} bookmark lists for user")
        return lists
