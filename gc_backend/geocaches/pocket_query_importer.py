"""
Importer for Geocaching.com Pocket Queries.

This module handles importing geocaches from pocket queries, which are
premium member features that allow downloading GPX files with geocache data.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
import requests

logger = logging.getLogger(__name__)


class PocketQueryImporter:
    """Import geocaches from Geocaching.com pocket queries."""
    
    POCKET_QUERY_DOWNLOAD_URL = 'https://www.geocaching.com/pocket/downloadpq.aspx'
    POCKET_QUERIES_LIST_URL = 'https://www.geocaching.com/pocket/'
    
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault('User-Agent', 'GeoApp/1.0 (+https://example.local)')
    
    @staticmethod
    def validate_pocket_query_code(code: str) -> str:
        """Validate and normalize a pocket query code (e.g., PQ1234 or a GUID)."""
        normalized = (code or '').strip().upper()
        
        # Check if it's a PQ code format
        if re.match(r'^PQ[0-9A-Z]+$', normalized):
            return normalized
        
        # Check if it's a GUID format (used in some PQ URLs)
        if re.match(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$', normalized):
            return normalized
        
        raise ValueError('invalid_pocket_query_code')
    
    def _find_download_link_from_main_page(self, guid: str) -> str | None:
        """Find the download link for a PQ from the main pocket queries page."""
        try:
            resp = self.session.get(self.POCKET_QUERIES_LIST_URL, timeout=30)
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for the row containing this GUID
            for row in soup.find_all('tr'):
                row_html = str(row)
                if guid.lower() in row_html.lower():
                    # Found the row, now look for download link
                    for link in row.find_all('a', href=True):
                        href = link['href']
                        if 'download' in href.lower():
                            if href.startswith('http'):
                                return href
                            elif href.startswith('/'):
                                return f'https://www.geocaching.com{href}'
                            else:
                                return f'https://www.geocaching.com/pocket/{href}'
            
            # Alternative: look for download buttons/links with the GUID
            for elem in soup.find_all(['a', 'button'], href=True):
                href = elem.get('href', '')
                if guid.lower() in href.lower() and 'download' in href.lower():
                    if href.startswith('http'):
                        return href
                    elif href.startswith('/'):
                        return f'https://www.geocaching.com{href}'
                    else:
                        return f'https://www.geocaching.com/pocket/{href}'
            
        except Exception as e:
            logger.debug(f"Failed to find download link from main page: {e}")
        
        return None
    
    def download_pocket_query_gpx(self, pq_code: str) -> bytes:
        """
        Download a pocket query as GPX/ZIP file.
        
        Args:
            pq_code: The pocket query code (e.g., PQ1234 or GUID)
            
        Returns:
            Raw bytes of the GPX or ZIP file
            
        Raises:
            ValueError: If pocket query code is invalid
            LookupError: If pocket query not found or not accessible
            RuntimeError: If download fails
        """
        code = self.validate_pocket_query_code(pq_code)
        logger.info(f"Downloading pocket query {code}")
        
        # For GUID format, we need to find the download link
        if not code.startswith('PQ'):
            # First, try to find the download link from the main PQ list page
            logger.debug(f"Looking for download link on main PQ page for {code}")
            download_link = self._find_download_link_from_main_page(code)
            
            if download_link:
                logger.debug(f"Found download link from main page: {download_link}")
                try:
                    download_resp = self.session.get(download_link, timeout=60, allow_redirects=True)
                    
                    if download_resp.status_code == 200:
                        # Check by magic bytes
                        if download_resp.content[:2] == b'PK':  # ZIP file
                            logger.info(f"Successfully downloaded pocket query {code} as ZIP ({len(download_resp.content)} bytes)")
                            return download_resp.content
                        
                        if download_resp.content[:5] == b'<?xml':  # XML/GPX file
                            logger.info(f"Successfully downloaded pocket query {code} as GPX ({len(download_resp.content)} bytes)")
                            return download_resp.content
                except Exception as e:
                    logger.debug(f"Failed to download from main page link: {e}")
            
            # Fallback: Try the PQ details page
            details_url = f'https://www.geocaching.com/pocket/gcquery.aspx?guid={code}'
            logger.debug(f"Fetching PQ details from: {details_url}")
            
            try:
                resp = self.session.get(details_url, timeout=30)
                resp.raise_for_status()
                
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Debug: Save the HTML for analysis
                logger.debug(f"PQ page title: {soup.title.string if soup.title else 'No title'}")
                
                # Look for download links in the page
                download_link = None
                
                # Method 1: Look for form submissions (PQ might use POST)
                for form in soup.find_all('form'):
                    action = form.get('action', '')
                    if 'download' in action.lower():
                        # This is a download form
                        if action.startswith('http'):
                            download_link = action
                        elif action.startswith('/'):
                            download_link = f'https://www.geocaching.com{action}'
                        else:
                            download_link = f'https://www.geocaching.com/pocket/{action}'
                        logger.debug(f"Found download form action: {download_link}")
                        break
                
                # Method 2: Look for direct download links
                if not download_link:
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        link_text = link.get_text(strip=True).lower()
                        if 'download' in href.lower() or 'download' in link_text:
                            # Extract the full URL
                            if href.startswith('http'):
                                download_link = href
                            elif href.startswith('/'):
                                download_link = f'https://www.geocaching.com{href}'
                            else:
                                download_link = f'https://www.geocaching.com/pocket/{href}'
                            logger.debug(f"Found download link: {download_link}")
                            break
                
                # Method 3: Look for buttons with data attributes
                if not download_link:
                    for button in soup.find_all(['button', 'a', 'input'], attrs={'data-download-url': True}):
                        download_link = button['data-download-url']
                        if not download_link.startswith('http'):
                            download_link = f'https://www.geocaching.com{download_link}'
                        logger.debug(f"Found download link in data attribute: {download_link}")
                        break
                
                # Method 4: Look for JavaScript variables or data
                if not download_link:
                    for script in soup.find_all('script'):
                        script_text = script.string or ''
                        # Look for download URLs in JavaScript
                        url_match = re.search(r'["\']([^"\']*download[^"\']*)["\']', script_text, re.IGNORECASE)
                        if url_match:
                            potential_url = url_match.group(1)
                            if 'http' in potential_url or potential_url.startswith('/'):
                                download_link = potential_url if potential_url.startswith('http') else f'https://www.geocaching.com{potential_url}'
                                logger.debug(f"Found download URL in JavaScript: {download_link}")
                                break
                
                # Method 5: The PQ page might just be a list page, try direct download URL patterns
                if not download_link:
                    # Try different known patterns
                    patterns = [
                        f'https://www.geocaching.com/pocket/downloadpq.aspx?g={code}',
                        f'https://www.geocaching.com/pocket/downloadpq.aspx?id={code}',
                        f'https://www.geocaching.com/api/proxy/web/v1/pocketquery/{code}/download',
                    ]
                    for pattern in patterns:
                        logger.debug(f"Trying pattern: {pattern}")
                        download_link = pattern
                        break
                
                if download_link:
                    logger.debug(f"Attempting download from: {download_link}")
                    download_resp = self.session.get(download_link, timeout=60, allow_redirects=True)
                    
                    if download_resp.status_code == 200:
                        # Check if we got actual file content
                        content_type = download_resp.headers.get('Content-Type', '').lower()
                        
                        # Check by magic bytes
                        if download_resp.content[:2] == b'PK':  # ZIP file
                            logger.info(f"Successfully downloaded pocket query {code} as ZIP ({len(download_resp.content)} bytes)")
                            return download_resp.content
                        
                        if download_resp.content[:5] == b'<?xml':  # XML/GPX file
                            logger.info(f"Successfully downloaded pocket query {code} as GPX ({len(download_resp.content)} bytes)")
                            return download_resp.content
                        
                        # Check by content type
                        if 'zip' in content_type or 'gpx' in content_type or 'xml' in content_type or 'octet-stream' in content_type:
                            logger.info(f"Successfully downloaded pocket query {code} ({len(download_resp.content)} bytes)")
                            return download_resp.content
                
            except requests.RequestException as e:
                logger.error(f"Failed to fetch PQ details: {e}")
        
        # Fallback: Try legacy URLs
        urls_to_try = [
            f'https://www.geocaching.com/api/proxy/web/v1/pocketquery/{code}/download',
            f'https://www.geocaching.com/pocket/downloadpq.aspx?guid={code}',
        ]
        
        for url in urls_to_try:
            try:
                logger.debug(f"Trying fallback URL: {url}")
                resp = self.session.get(url, timeout=60, allow_redirects=True)
                
                if resp.status_code == 404:
                    continue
                
                if resp.status_code == 403:
                    raise LookupError('pocket_query_requires_premium')
                
                resp.raise_for_status()
                
                # Check by magic bytes
                if resp.content[:2] == b'PK':  # ZIP file
                    logger.info(f"Successfully downloaded pocket query {code} as ZIP ({len(resp.content)} bytes)")
                    return resp.content
                
                if resp.content[:5] == b'<?xml':  # XML/GPX file
                    logger.info(f"Successfully downloaded pocket query {code} as GPX ({len(resp.content)} bytes)")
                    return resp.content
                
            except requests.RequestException:
                continue
        
        logger.error(f"Failed to download pocket query {code} from all attempted methods")
        raise LookupError('pocket_query_not_found')
    
    def get_pocket_query_info(self, pq_code: str) -> dict:
        """
        Get information about a pocket query.
        
        Args:
            pq_code: The pocket query code
            
        Returns:
            Dictionary with pocket query information
        """
        code = self.validate_pocket_query_code(pq_code)
        
        return {
            'code': code,
            'name': code,
            'type': 'pocket_query'
        }
    
    def get_user_pocket_queries(self) -> list[dict]:
        """
        Get all pocket queries for the authenticated user.
        
        Returns:
            List of dictionaries with pocket query information (guid, name, count, etc.)
        """
        logger.info("Fetching user's pocket queries")
        
        try:
            resp = self.session.get(self.POCKET_QUERIES_LIST_URL, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch user's pocket queries: {e}")
            return []
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'html.parser')
        queries = []
        
        # Method 1: Look for PQ table rows (modern layout)
        for row in soup.find_all('tr', class_=re.compile(r'pq-row|pocket-query-row', re.IGNORECASE)):
            guid = None
            name = None
            count = 0
            
            # Extract GUID from links or data attributes
            for link in row.find_all('a', href=True):
                href = link['href']
                match = re.search(r'[?&]guid=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', href, re.IGNORECASE)
                if match:
                    guid = match.group(1).upper()
                    # Try to get name from link text
                    link_text = link.get_text(strip=True)
                    if link_text and 'download' not in link_text.lower():
                        name = link_text
                    break
            
            # Try data attributes
            if not guid:
                guid_attr = row.get('data-guid') or row.get('data-pq-guid')
                if guid_attr:
                    guid = guid_attr.upper()
            
            # Extract name from specific columns
            if not name:
                name_cell = row.find('td', class_=re.compile(r'name|title', re.IGNORECASE))
                if name_cell:
                    name = name_cell.get_text(strip=True)
            
            # Extract count
            count_cell = row.find('td', class_=re.compile(r'count|caches|results', re.IGNORECASE))
            if count_cell:
                count_text = count_cell.get_text(strip=True)
                count_match = re.search(r'(\d+)', count_text)
                if count_match:
                    count = int(count_match.group(1))
            
            if guid:
                if not name:
                    name = f"PQ {guid[:8]}"
                
                if not any(q['guid'] == guid for q in queries):
                    queries.append({
                        'guid': guid,
                        'name': name,
                        'count': count
                    })
        
        # Method 2: Look for any links with GUIDs (fallback)
        if not queries:
            for link in soup.find_all('a', href=True):
                href = link['href']
                match = re.search(r'[?&]guid=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', href, re.IGNORECASE)
                if match:
                    guid = match.group(1).upper()
                    
                    # Try to find the PQ name
                    name = link.get_text(strip=True)
                    if not name or 'download' in name.lower():
                        # Look for name in parent row
                        parent_row = link.find_parent('tr')
                        if parent_row:
                            # Look for the name in the row
                            for cell in parent_row.find_all('td'):
                                cell_text = cell.get_text(strip=True)
                                if cell_text and 'download' not in cell_text.lower() and len(cell_text) > 3:
                                    name = cell_text
                                    break
                    
                    if not name:
                        name = f"PQ {guid[:8]}"
                    
                    # Try to find the count in the same row
                    count = 0
                    parent_row = link.find_parent('tr')
                    if parent_row:
                        row_text = parent_row.get_text()
                        count_match = re.search(r'(\d+)\s*(?:cache|géocache)', row_text, re.IGNORECASE)
                        if count_match:
                            count = int(count_match.group(1))
                    
                    # Avoid duplicates
                    if not any(q['guid'] == guid for q in queries):
                        queries.append({
                            'guid': guid,
                            'name': name,
                            'count': count
                        })
        
        # Method 2: Look for data attributes
        for elem in soup.find_all(attrs={'data-pq-guid': True}):
            guid = elem.get('data-pq-guid', '').strip().upper()
            if guid and re.match(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$', guid):
                name = elem.get('data-pq-name', f"PQ {guid[:8]}")
                count = int(elem.get('data-pq-count', 0))
                
                if not any(q['guid'] == guid for q in queries):
                    queries.append({
                        'guid': guid,
                        'name': name,
                        'count': count
                    })
        
        logger.info(f"Found {len(queries)} pocket queries for user")
        return queries
