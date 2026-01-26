"""
Service d'authentification centralisé pour Geocaching.com.

Ce service gère l'authentification via:
1. Cookies extraits du navigateur (Firefox, Chrome, Edge) - méthode legacy
2. Login username/password comme c:geo - méthode recommandée

L'authentification par username/password utilise le même flow que c:geo:
1. GET sur la page de login pour obtenir le __RequestVerificationToken
2. POST des credentials (UsernameOrEmail, Password, token)
3. Stockage des cookies de session pour les requêtes futures
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import browser_cookie3
import requests

logger = logging.getLogger(__name__)


class AuthMethod(Enum):
    """Méthode d'authentification utilisée."""
    NONE = "none"
    BROWSER_COOKIES = "browser_cookies"
    CREDENTIALS = "credentials"


class AuthStatus(Enum):
    """Statut de l'authentification."""
    NOT_CONFIGURED = "not_configured"
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    LOGIN_FAILED = "login_failed"
    CAPTCHA_REQUIRED = "captcha_required"
    ACCOUNT_NOT_VALIDATED = "account_not_validated"


@dataclass
class UserInfo:
    """Informations sur l'utilisateur connecté."""
    username: str
    reference_code: Optional[str] = None
    user_type: Optional[str] = None  # Basic, Premium, etc.
    public_guid: Optional[str] = None
    avatar_url: Optional[str] = None
    date_format: Optional[str] = None
    finds_count: Optional[int] = None


@dataclass
class AuthState:
    """État actuel de l'authentification."""
    status: AuthStatus
    method: AuthMethod
    user_info: Optional[UserInfo] = None
    last_check: Optional[datetime] = None
    error_message: Optional[str] = None


class GeocachingAuthService:
    """
    Service centralisé pour l'authentification Geocaching.com.
    
    Singleton thread-safe qui gère l'authentification et fournit
    une session requests configurée pour les autres services.
    """
    
    LOGIN_URI = "https://www.geocaching.com/account/signin"
    LOGOUT_URI = "https://www.geocaching.com/account/logout"
    SERVER_PARAMS_URI = "https://www.geocaching.com/play/serverparameters/params"
    DASHBOARD_URI = "https://www.geocaching.com/account/dashboard"
    
    REQUEST_VERIFICATION_TOKEN = "__RequestVerificationToken"
    
    # Durée de validité du cache d'état (en secondes)
    STATE_CACHE_TTL = 300  # 5 minutes
    
    _instance: Optional["GeocachingAuthService"] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "GeocachingAuthService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        
        self._session: Optional[requests.Session] = None
        self._auth_state = AuthState(
            status=AuthStatus.NOT_CONFIGURED,
            method=AuthMethod.NONE
        )
        self._credentials_file = self._get_credentials_file_path()
        self._session_lock = threading.Lock()
        self._initialized = True
        
        logger.info("GeocachingAuthService initialized")
    
    def _get_credentials_file_path(self) -> Path:
        """Retourne le chemin du fichier de stockage des credentials."""
        # Utiliser le dossier data de l'application
        data_dir = Path(__file__).parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / ".gc_credentials.json"
    
    def _create_session(self) -> requests.Session:
        """Crée une nouvelle session requests avec les headers appropriés."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'GeoApp/1.0 (+https://mysterai.io)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
        })
        return session
    
    def get_session(self) -> requests.Session:
        """
        Retourne une session requests authentifiée.
        
        La session est créée et authentifiée si nécessaire.
        Cette méthode est thread-safe.
        """
        with self._session_lock:
            if self._session is None:
                self._session = self._create_session()
                self._try_restore_session()
            
            return self._session
    
    def _try_restore_session(self) -> None:
        """Tente de restaurer une session précédente."""
        # 1. Essayer les credentials sauvegardés
        saved = self._load_saved_credentials()
        if saved and saved.get("method") == "credentials":
            username = saved.get("username")
            password = saved.get("password")
            if username and password:
                logger.info("Attempting to restore session with saved credentials...")
                status = self._do_login(username, password)
                if status == AuthStatus.LOGGED_IN:
                    logger.info("Session restored successfully")
                    return
        
        # 2. Fallback: essayer les cookies du navigateur si configuré
        if saved and saved.get("method") == "browser_cookies":
            browser = saved.get("browser", "auto")
            logger.info(f"Attempting to restore session with browser cookies ({browser})...")
            self._load_browser_cookies(browser)
            if self._verify_login_status():
                self._auth_state = AuthState(
                    status=AuthStatus.LOGGED_IN,
                    method=AuthMethod.BROWSER_COOKIES,
                    last_check=datetime.now()
                )
                self._fetch_user_info()
                logger.info("Session restored with browser cookies")
    
    def _load_saved_credentials(self) -> Optional[dict]:
        """Charge les credentials sauvegardés (si existants)."""
        if not self._credentials_file.exists():
            return None
        
        try:
            with open(self._credentials_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load saved credentials: {e}")
            return None
    
    def _save_credentials(self, method: str, **kwargs) -> None:
        """Sauvegarde les credentials de manière sécurisée."""
        data = {"method": method, **kwargs}
        
        try:
            logger.info(f"Saving credentials to {self._credentials_file}...")
            with open(self._credentials_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            
            # Restreindre les permissions (Unix only)
            try:
                os.chmod(self._credentials_file, 0o600)
            except (OSError, AttributeError):
                pass  # Windows ou autre
            
            logger.info(f"Credentials saved successfully (method: {method})")
                
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}", exc_info=True)
    
    def _clear_saved_credentials(self) -> None:
        """Supprime les credentials sauvegardés."""
        try:
            if self._credentials_file.exists():
                self._credentials_file.unlink()
        except Exception as e:
            logger.warning(f"Failed to clear saved credentials: {e}")
    
    # ==================== LOGIN METHODS ====================
    
    def login_with_credentials(
        self, 
        username: str, 
        password: str, 
        remember: bool = True
    ) -> AuthState:
        """
        Authentification avec username/password (méthode c:geo).
        
        Args:
            username: Nom d'utilisateur ou email Geocaching.com
            password: Mot de passe
            remember: Si True, sauvegarde les credentials pour les sessions futures
            
        Returns:
            État de l'authentification après la tentative
        """
        with self._session_lock:
            # Toujours créer une nouvelle session pour un login fresh
            logger.info("Creating fresh session for credentials login...")
            self._session = self._create_session()
        
        status = self._do_login(username, password)
        
        if status == AuthStatus.LOGGED_IN and remember:
            self._save_credentials("credentials", username=username, password=password)
        
        return self._auth_state
    
    def _do_login(self, username: str, password: str) -> AuthStatus:
        """
        Effectue le login effectif.
        
        Flow similaire à c:geo:
        1. GET page de login -> extraction du token
        2. POST credentials
        3. Vérification du statut
        """
        try:
            # Étape 1: Récupérer la page de login et le token
            logger.info(f"Fetching login page for {username}...")
            resp = self._session.get(self.LOGIN_URI, timeout=30)
            
            if resp.status_code != 200:
                self._auth_state = AuthState(
                    status=AuthStatus.LOGIN_FAILED,
                    method=AuthMethod.CREDENTIALS,
                    error_message=f"Failed to fetch login page (status {resp.status_code})"
                )
                return AuthStatus.LOGIN_FAILED
            
            # Vérifier si déjà connecté (mais vérifier vraiment avec serverparameters)
            if self._is_logged_in_page(resp.text):
                logger.info(f"Login page suggests already logged in, verifying...")
                if self._verify_login_status():
                    logger.info(f"Confirmed: already logged in as {username}")
                    self._auth_state = AuthState(
                        status=AuthStatus.LOGGED_IN,
                        method=AuthMethod.CREDENTIALS,
                        last_check=datetime.now()
                    )
                    self._fetch_user_info()
                    return AuthStatus.LOGGED_IN
                else:
                    logger.info("False positive on login page, proceeding with login...")
            
            # Extraire le token
            token = self._extract_verification_token(resp.text)
            if not token:
                self._auth_state = AuthState(
                    status=AuthStatus.LOGIN_FAILED,
                    method=AuthMethod.CREDENTIALS,
                    error_message="Could not extract verification token from login page"
                )
                return AuthStatus.LOGIN_FAILED
            
            # Étape 2: POST des credentials
            logger.info(f"Posting credentials for {username}...")
            login_data = {
                "UsernameOrEmail": username,
                "Password": password,
                self.REQUEST_VERIFICATION_TOKEN: token
            }
            
            resp = self._session.post(
                self.LOGIN_URI,
                data=login_data,
                timeout=30,
                allow_redirects=True
            )
            
            logger.debug(f"Login POST response: status={resp.status_code}, url={resp.url}")
            
            # Si on est redirigé vers /play/search ou /account/dashboard, c'est un succès
            if resp.status_code == 200 and ('/play/search' in resp.url or '/account/dashboard' in resp.url):
                logger.info(f"Login successful for {username} (redirected to {resp.url})")
                self._auth_state = AuthState(
                    status=AuthStatus.LOGGED_IN,
                    method=AuthMethod.CREDENTIALS,
                    last_check=datetime.now()
                )
                self._fetch_user_info()
                return AuthStatus.LOGGED_IN
            
            # Étape 3: Analyser la réponse
            return self._analyze_login_response(resp.text, username)
            
        except requests.RequestException as e:
            logger.error(f"Login request failed: {e}")
            self._auth_state = AuthState(
                status=AuthStatus.LOGIN_FAILED,
                method=AuthMethod.CREDENTIALS,
                error_message=f"Network error: {str(e)}"
            )
            return AuthStatus.LOGIN_FAILED
    
    def _extract_verification_token(self, html: str) -> Optional[str]:
        """Extrait le __RequestVerificationToken de la page HTML."""
        # Pattern: <input name="__RequestVerificationToken" type="hidden" value="XXX" />
        pattern = r'<input[^>]*name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']'
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Alternative: attributs dans un ordre différent
        pattern2 = r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']__RequestVerificationToken["\']'
        match2 = re.search(pattern2, html, re.IGNORECASE)
        if match2:
            return match2.group(1)
        
        return None
    
    def _is_logged_in_page(self, html: str) -> bool:
        """Vérifie si la page indique qu'on est connecté."""
        # Vérifier d'abord isLoggedIn:true qui est le plus fiable
        if '"isLoggedIn":true' in html or '"isLoggedIn": true' in html:
            return True
        
        # Indicateurs secondaires (moins fiables)
        indicators = [
            'account/logout',
            'sign out',
        ]
        
        html_lower = html.lower()
        # Compter combien d'indicateurs sont présents
        matches = sum(1 for ind in indicators if ind.lower() in html_lower)
        
        # Exiger au moins 2 indicateurs pour éviter les faux positifs
        logger.debug(f"Login page indicators found: {matches}/{len(indicators)}")
        return matches >= 2
    
    def _analyze_login_response(self, html: str, username: str) -> AuthStatus:
        """Analyse la réponse du login pour déterminer le statut."""
        
        logger.debug(f"Analyzing login response (length: {len(html)} chars)")
        
        # Succès
        if self._is_logged_in_page(html):
            logger.info(f"Login successful for {username}")
            self._auth_state = AuthState(
                status=AuthStatus.LOGGED_IN,
                method=AuthMethod.CREDENTIALS,
                last_check=datetime.now()
            )
            self._fetch_user_info()
            return AuthStatus.LOGGED_IN
        
        # Captcha requis
        if 'g-recaptcha' in html or 'recaptcha' in html.lower():
            logger.warning(f"Captcha required for {username}")
            self._auth_state = AuthState(
                status=AuthStatus.CAPTCHA_REQUIRED,
                method=AuthMethod.CREDENTIALS,
                error_message="Captcha required. Please try again later or use browser cookies method."
            )
            return AuthStatus.CAPTCHA_REQUIRED
        
        # Identifiants incorrects
        if 'signup-validation-error' in html or 'incorrect' in html.lower():
            logger.warning(f"Wrong credentials for {username}")
            self._auth_state = AuthState(
                status=AuthStatus.LOGIN_FAILED,
                method=AuthMethod.CREDENTIALS,
                error_message="Username or password incorrect"
            )
            return AuthStatus.LOGIN_FAILED
        
        # Compte non validé
        if 'account/join/success' in html or 'validate' in html.lower():
            logger.warning(f"Account not validated for {username}")
            self._auth_state = AuthState(
                status=AuthStatus.ACCOUNT_NOT_VALIDATED,
                method=AuthMethod.CREDENTIALS,
                error_message="Account not validated. Please check your email."
            )
            return AuthStatus.ACCOUNT_NOT_VALIDATED
        
        # Échec générique
        logger.warning(f"Login failed for {username} (unknown reason)")
        # Log un extrait de la réponse pour débogage
        snippet = html[:500] if len(html) > 500 else html
        logger.debug(f"Response snippet: {snippet}")
        self._auth_state = AuthState(
            status=AuthStatus.LOGIN_FAILED,
            method=AuthMethod.CREDENTIALS,
            error_message="Login failed for unknown reason"
        )
        return AuthStatus.LOGIN_FAILED
    
    def login_with_browser_cookies(
        self, 
        browser: str = "auto",
        remember: bool = True
    ) -> AuthState:
        """
        Authentification avec les cookies du navigateur.
        
        Args:
            browser: Navigateur à utiliser ('firefox', 'chrome', 'edge', 'auto')
            remember: Si True, sauvegarde la préférence pour les sessions futures
            
        Returns:
            État de l'authentification après la tentative
        """
        with self._session_lock:
            if self._session is None:
                self._session = self._create_session()
            else:
                self._session.cookies.clear()
        
        self._load_browser_cookies(browser)
        
        if self._verify_login_status():
            self._auth_state = AuthState(
                status=AuthStatus.LOGGED_IN,
                method=AuthMethod.BROWSER_COOKIES,
                last_check=datetime.now()
            )
            self._fetch_user_info()
            
            if remember:
                self._save_credentials("browser_cookies", browser=browser)
            
            logger.info(f"Logged in with {browser} browser cookies")
        else:
            self._auth_state = AuthState(
                status=AuthStatus.LOGIN_FAILED,
                method=AuthMethod.BROWSER_COOKIES,
                error_message="No valid session found in browser cookies. Please login in your browser first."
            )
        
        return self._auth_state
    
    def _load_browser_cookies(self, browser: str = "auto") -> None:
        """Charge les cookies du navigateur spécifié."""
        browsers_map = {
            'firefox': [('Firefox', browser_cookie3.firefox)],
            'chrome': [('Chrome', browser_cookie3.chrome)],
            'edge': [('Edge', browser_cookie3.edge)],
            'auto': [
                ('Firefox', browser_cookie3.firefox),
                ('Chrome', browser_cookie3.chrome),
                ('Edge', browser_cookie3.edge),
            ],
        }
        
        browsers = browsers_map.get(browser.lower(), browsers_map['auto'])
        
        for browser_name, browser_func in browsers:
            try:
                logger.debug(f"Trying to load cookies from {browser_name}...")
                cookies = browser_func(domain_name='geocaching.com')
                
                cookie_count = 0
                for cookie in cookies:
                    self._session.cookies.set_cookie(cookie)
                    cookie_count += 1
                
                if cookie_count > 0:
                    logger.info(f"Loaded {cookie_count} cookies from {browser_name}")
                    return
                    
            except Exception as e:
                logger.debug(f"Failed to load cookies from {browser_name}: {e}")
                continue
        
        logger.warning("No browser cookies could be loaded")
    
    def _verify_login_status(self) -> bool:
        """Vérifie si la session actuelle est authentifiée."""
        try:
            # Utiliser SERVER_PARAMS_URI qui est plus fiable pour vérifier la connexion
            resp = self._session.get(self.SERVER_PARAMS_URI, timeout=30)
            
            if resp.status_code != 200:
                logger.debug(f"Verify login failed: status {resp.status_code}")
                return False
            
            # Vérifier si les données contiennent isLoggedIn: true
            text = resp.text
            logger.debug(f"Server params response (first 300 chars): {text[:300]}")
            
            if '"isLoggedIn":true' in text or '"isLoggedIn": true' in text:
                logger.debug("Login verification successful")
                return True
            
            logger.debug("Login verification failed: isLoggedIn not found or false")
            return False
            
        except Exception as e:
            logger.warning(f"Failed to verify login status: {e}")
            return False
    
    def _fetch_user_info(self) -> None:
        """Récupère les informations de l'utilisateur connecté."""
        try:
            resp = self._session.get(self.SERVER_PARAMS_URI, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch user info: status {resp.status_code}")
                return
            
            # Parser le JavaScript: var serverParameters = {...};
            text = resp.text
            start = text.find('{')
            end = text.rfind(';')
            if start == -1 or end == -1:
                logger.warning("Failed to parse server parameters: no JSON found")
                return
            
            json_str = text[start:end]
            data = json.loads(json_str)
            
            user_info_data = data.get('user:info', {})
            if user_info_data and user_info_data.get('isLoggedIn'):
                self._auth_state.user_info = UserInfo(
                    username=user_info_data.get('username', 'Unknown'),
                    reference_code=user_info_data.get('referenceCode'),
                    user_type=user_info_data.get('userType'),
                    public_guid=user_info_data.get('publicGuid'),
                    avatar_url=user_info_data.get('avatarUrl'),
                    date_format=user_info_data.get('dateFormat'),
                )
                logger.info(f"User info fetched: {self._auth_state.user_info.username} ({self._auth_state.user_info.user_type})")
            else:
                logger.warning(f"User info indicates not logged in: isLoggedIn={user_info_data.get('isLoggedIn')}")
                
        except Exception as e:
            logger.warning(f"Failed to fetch user info: {e}", exc_info=True)
    
    # ==================== LOGOUT & STATUS ====================
    
    def logout(self) -> AuthState:
        """Déconnexion de Geocaching.com."""
        with self._session_lock:
            if self._session:
                try:
                    self._session.post(self.LOGOUT_URI, timeout=30)
                except Exception as e:
                    logger.warning(f"Logout request failed: {e}")
                
                self._session.cookies.clear()
        
        self._clear_saved_credentials()
        
        self._auth_state = AuthState(
            status=AuthStatus.LOGGED_OUT,
            method=AuthMethod.NONE
        )
        
        logger.info("Logged out from Geocaching.com")
        return self._auth_state
    
    def get_auth_state(self, force_check: bool = False) -> AuthState:
        """
        Retourne l'état actuel de l'authentification.
        
        Args:
            force_check: Si True, vérifie le statut même si le cache est valide
            
        Returns:
            État de l'authentification
        """
        # Vérifier si le cache est encore valide
        if not force_check and self._auth_state.last_check:
            age = datetime.now() - self._auth_state.last_check
            if age < timedelta(seconds=self.STATE_CACHE_TTL):
                return self._auth_state
        
        # Vérifier le statut réel
        if self._session and self._verify_login_status():
            self._auth_state.status = AuthStatus.LOGGED_IN
            self._auth_state.last_check = datetime.now()
            if not self._auth_state.user_info:
                self._fetch_user_info()
        elif self._auth_state.status == AuthStatus.LOGGED_IN:
            # Était connecté mais plus maintenant
            self._auth_state.status = AuthStatus.LOGGED_OUT
            self._auth_state.last_check = datetime.now()
        
        return self._auth_state
    
    def is_logged_in(self) -> bool:
        """Vérifie rapidement si l'utilisateur est connecté."""
        return self.get_auth_state().status == AuthStatus.LOGGED_IN
    
    def get_configured_method(self) -> Optional[str]:
        """Retourne la méthode d'authentification configurée."""
        saved = self._load_saved_credentials()
        return saved.get("method") if saved else None


# Singleton instance
_auth_service: Optional[GeocachingAuthService] = None


def get_auth_service() -> GeocachingAuthService:
    """Retourne l'instance singleton du service d'authentification."""
    global _auth_service
    if _auth_service is None:
        _auth_service = GeocachingAuthService()
    return _auth_service
