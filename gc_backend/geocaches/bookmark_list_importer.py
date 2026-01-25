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
    
    def get_geocache_codes_from_list(self, bookmark_code: str) -> list[str]:
        """
        Extract all geocache codes from a bookmark list.
        
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
        gc_codes = []
        
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
        
        try:
            resp = self.session.get(self.USER_LISTS_URL, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch user's bookmark lists: {e}")
            return []
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        lists = []
        
        # Method 1: Look for list cards/rows in modern layout
        for card in soup.find_all(['div', 'tr'], class_=re.compile(r'list-card|list-row|bookmark-list', re.IGNORECASE)):
            code = None
            name = None
            count = 0
            
            # Extract code from links
            for link in card.find_all('a', href=True):
                href = link['href']
                match = re.search(r'/plan/lists/(BM[0-9A-Z]+)', href)
                if match:
                    code = match.group(1)
                    # Try to get name from link text
                    link_text = link.get_text(strip=True)
                    if link_text and len(link_text) > 2:
                        name = link_text
                    break
            
            # Try data attributes
            if not code:
                code_attr = card.get('data-list-code') or card.get('data-code')
                if code_attr:
                    code = code_attr.strip().upper()
            
            # Extract name from specific elements
            if not name:
                name_elem = card.find(['h3', 'h4', 'span', 'div'], class_=re.compile(r'name|title', re.IGNORECASE))
                if name_elem:
                    name = name_elem.get_text(strip=True)
            
            # Extract count
            count_elem = card.find(['span', 'div'], class_=re.compile(r'count|caches|items', re.IGNORECASE))
            if count_elem:
                count_text = count_elem.get_text(strip=True)
                count_match = re.search(r'(\d+)', count_text)
                if count_match:
                    count = int(count_match.group(1))
            else:
                # Try to find count in the entire card text
                card_text = card.get_text()
                count_match = re.search(r'(\d+)\s*(?:cache|géocache|item)', card_text, re.IGNORECASE)
                if count_match:
                    count = int(count_match.group(1))
            
            if code:
                if not name:
                    name = code
                
                if not any(l['code'] == code for l in lists):
                    lists.append({
                        'code': code,
                        'name': name,
                        'count': count,
                        'url': f'{self.BOOKMARK_LIST_URL}{code}'
                    })
        
        # Method 2: Look for any links with BM codes (fallback)
        if not lists:
            for link in soup.find_all('a', href=True):
                href = link['href']
                match = re.search(r'/plan/lists/(BM[0-9A-Z]+)', href)
                if match:
                    code = match.group(1)
                    name = link.get_text(strip=True) or code
                    
                    # Try to find the count in parent elements
                    count = 0
                    parent_row = link.find_parent(['tr', 'div'])
                    if parent_row:
                        row_text = parent_row.get_text()
                        count_match = re.search(r'(\d+)\s*(?:cache|géocache)', row_text, re.IGNORECASE)
                        if count_match:
                            count = int(count_match.group(1))
                    
                    # Avoid duplicates
                    if not any(l['code'] == code for l in lists):
                        lists.append({
                            'code': code,
                            'name': name,
                            'count': count,
                            'url': f'{self.BOOKMARK_LIST_URL}{code}'
                        })
        
        # Method 3: Look for data attributes
        for elem in soup.find_all(attrs={'data-list-code': True}):
            code = elem.get('data-list-code', '').strip().upper()
            if code and re.match(r'^BM[0-9A-Z]+$', code):
                name = elem.get('data-list-name', code)
                count = int(elem.get('data-list-count', 0))
                
                if not any(l['code'] == code for l in lists):
                    lists.append({
                        'code': code,
                        'name': name,
                        'count': count,
                        'url': f'{self.BOOKMARK_LIST_URL}{code}'
                    })
        
        logger.info(f"Found {len(lists)} bookmark lists for user")
        return lists
