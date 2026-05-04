import jwt
import json
import asyncio
import aiohttp
from azure.identity import DefaultAzureCredential, OnBehalfOfCredential
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from typing import Dict, Any, Optional, Tuple
import logging
from config import settings
import time

logger = logging.getLogger(__name__)

class LogAnalyticsTokenCredential:
    """Custom credential that exchanges user tokens for Log Analytics API tokens"""
    
    def __init__(self, user_token: str, tenant_id: str, client_id: str, client_secret: str):
        self.user_token = user_token
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._log_analytics_token = None
        self._token_expiry = 0
        
    async def _get_log_analytics_token(self) -> str:
        """Exchange user token for Log Analytics API token using On-Behalf-Of flow"""
        if self._log_analytics_token and time.time() < self._token_expiry - 300:  # 5 min buffer
            return self._log_analytics_token
            
        try:
            # Prepare the On-Behalf-Of token request
            url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            
            data = {
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'assertion': self.user_token,
                'scope': 'https://api.loganalytics.io/.default',  # Log Analytics scope
                'requested_token_use': 'on_behalf_of'
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, headers=headers) as response:
                    if response.status == 200:
                        token_data = await response.json()
                        self._log_analytics_token = token_data['access_token']
                        self._token_expiry = time.time() + token_data.get('expires_in', 3600)
                        logger.info("Successfully obtained Log Analytics token via On-Behalf-Of flow")
                        return self._log_analytics_token
                    else:
                        error_text = await response.text()
                        logger.error(f"Token exchange failed: {response.status} - {error_text}")
                        raise ClientAuthenticationError(f"Token exchange failed: {error_text}")
                        
        except Exception as e:
            logger.error(f"Failed to exchange token for Log Analytics API: {e}")
            raise ClientAuthenticationError(f"Token exchange failed: {str(e)}")
    
    def get_token(self, *scopes, **kwargs) -> AccessToken:
        """Return Log Analytics API token"""
        # This is a sync method but we need async token exchange
        # For now, we'll use the original user token and let Azure SDK handle it
        # In a production environment, you'd want to implement proper async token caching
        
        # Check if this is for Log Analytics
        if scopes and any('loganalytics' in str(scope).lower() for scope in scopes):
            logger.warning("Log Analytics token requested but sync method called - using user token")
        
        # Decode to read expiry only — signature is already validated by AzureAuthHandler.validate_token
        try:
            decoded = jwt.decode(self.user_token, options={"verify_signature": False})
            expires_on = decoded.get('exp', int(time.time()) + 3600)
        except:
            expires_on = int(time.time()) + 3600
            
        return AccessToken(self.user_token, expires_on)

class UserTokenCredential:
    """Custom credential that uses user's access token for Azure API calls"""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        # Decode to read expiry/audience only — signature already verified by AzureAuthHandler.validate_token
        try:
            # Ensure token is a string for jwt.decode
            token_str = access_token if isinstance(access_token, str) else access_token.decode('utf-8')
            decoded = jwt.decode(token_str, options={"verify_signature": False})
            self.expires_on = decoded.get('exp', int(time.time()) + 3600)
            self.audience = decoded.get('aud', '')
            self.tenant_id = decoded.get('tid', '')
            logger.debug(f"Token audience: {self.audience}, tenant: {self.tenant_id}")
        except Exception as e:
            logger.warning(f"Could not decode token for expiry: {e}")
            # Default to 1 hour from now if we can't decode
            self.expires_on = int(time.time()) + 3600
            self.audience = ''
            self.tenant_id = ''
    
    def get_token(self, *scopes, **kwargs) -> AccessToken:
        """Return the user's access token
        
        Note: We now use dual token system where frontend sends:
        1. Management token (in Authorization header) for workspace/resource operations
        2. LA token (in X-LA-Token header) for KQL execution
        
        The get_user_with_dual_tokens dependency extracts both and stores them separately:
        - user_info['token'] and user_info['management_token'] = Management token
        - user_info['la_token'] = LA token (if available)
        """
        # Check if this is for Log Analytics (monitor APIs)
        if scopes and any('monitor' in str(scope).lower() or 'loganalytics' in str(scope).lower() for scope in scopes):
            # Only log warning if audience doesn't match (LA token should have api.loganalytics.io)
            if self.audience and 'loganalytics.io' not in self.audience and 'api.loganalytics.io' not in self.audience:
                logger.warning(f" Token audience mismatch: '{self.audience}' - LA API needs 'https://api.loganalytics.io'")
        
        return AccessToken(self.access_token, self.expires_on)

class AzureAuthHandler:
    """Handles Azure AD authentication and token validation"""
    
    def __init__(self):
        self.authority = settings.AZURE_AUTHORITY
        self.audit_service = None  # Will be set after initialization
        self._jwks_cache: Dict[str, Tuple[Dict, float]] = {}  # tenant_id -> (jwks, cached_at)
        self._jwks_cache_ttl = settings.JWKS_CACHE_TTL

        # Note: No need for hardcoded client_id or tenant_id - extracted from user token
        
    async def _get_jwks(self, tenant_id: str) -> Dict[str, Any]:
        """Fetch and cache Azure AD JWKS (public keys) for signature verification."""
        cached = self._jwks_cache.get(tenant_id)
        if cached and (time.time() - cached[1]) < self._jwks_cache_ttl:
            return cached[0]

        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        async with aiohttp.ClientSession() as session:
            async with session.get(jwks_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    raise ClientAuthenticationError(f"Failed to fetch Azure AD public keys: HTTP {response.status}")
                jwks = await response.json()

        self._jwks_cache[tenant_id] = (jwks, time.time())
        logger.debug(f"Fetched {len(jwks.get('keys', []))} JWKS keys for tenant {tenant_id}")
        return jwks

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate Azure AD JWT token and extract user information
        """
        try:
            # Normalize token to string
            if isinstance(token, dict):
                # If token is already a dict (already decoded), return it
                logger.warning("Token is already a dict, returning as-is")
                return token
            elif isinstance(token, str):
                token_str = token
            else:
                try:
                    token_str = token.decode('utf-8')
                except AttributeError:
                    raise ClientAuthenticationError(f"Invalid token type: {type(token)}")

            # Extract header and unverified claims to locate the correct JWKS key
            header = jwt.get_unverified_header(token_str)
            kid = header.get('kid')
            unverified_claims = jwt.decode(token_str, options={"verify_signature": False, "verify_exp": False})
            tenant_id = unverified_claims.get('tid', 'common')

            # Fetch Azure AD public keys and find the signing key by kid
            jwks = await self._get_jwks(tenant_id)
            signing_key = None
            for key_data in jwks.get('keys', []):
                if key_data.get('kid') == kid:
                    signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
                    break

            if signing_key is None:
                raise ClientAuthenticationError(
                    f"Unable to find Azure AD signing key (kid={kid}) for tenant {tenant_id}"
                )

            # Decode with full signature verification; audience is validated manually below
            decoded_token = jwt.decode(
                token_str,
                signing_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )

            # Check token audience - MUST be for Azure Management API
            token_audience = decoded_token.get('aud', '')
            
            # Valid audiences for Azure Management API
            valid_audiences = [
                'https://management.azure.com',
                'https://management.azure.com/',
                'https://management.core.windows.net/',
                'https://management.core.windows.net'
            ]
            
            # Check if audience is valid (handle both string and array)
            is_valid_audience = False
            if isinstance(token_audience, list):
                is_valid_audience = any(aud in valid_audiences for aud in token_audience)
            else:
                is_valid_audience = token_audience in valid_audiences
            
            if not is_valid_audience:
                user_email = decoded_token.get('preferred_username') or decoded_token.get('email') or decoded_token.get('upn') or 'unknown'
                logger.error(
                    f"❌ INVALID TOKEN AUDIENCE: '{token_audience}'. "
                    f"Expected one of: {valid_audiences}. "
                    f"This token is for Microsoft Graph (00000003...) but Azure Management API requires management.azure.com audience. "
                    f"Frontend must request a token with scope 'https://management.azure.com/user_impersonation'"
                )
                
                # Log SSO/authentication failure
                if self.audit_service:
                    try:
                        self.audit_service.log_event(
                            action='failed_auth',
                            user_email=user_email,
                            details={
                                'reason': 'Invalid token audience',
                                'expected': 'management.azure.com',
                                'received': token_audience,
                                'error_type': 'sso_token_audience_mismatch'
                            },
                            status='failure'
                        )
                    except Exception as audit_error:
                        logger.debug(f"Failed to log audit event: {audit_error}")
                
                raise ClientAuthenticationError(
                    "Invalid token audience. Token must be requested with Azure Management API scope "
                    "(https://management.azure.com/user_impersonation), not Microsoft Graph scope. "
                    "Please refresh your browser to get a new token."
                )
            
            # Validate token claims using values from the token itself
            if not self._validate_token_claims(decoded_token):
                user_email = decoded_token.get('preferred_username') or decoded_token.get('email') or decoded_token.get('upn') or 'unknown'
                
                # Log SSO/authentication failure
                if self.audit_service:
                    try:
                        self.audit_service.log_event(
                            action='failed_auth',
                            user_email=user_email,
                            details={
                                'reason': 'Invalid token claims',
                                'error_type': 'sso_token_claims_invalid'
                            },
                            status='failure'
                        )
                    except Exception as audit_error:
                        logger.debug(f"Failed to log audit event: {audit_error}")
                
                raise ClientAuthenticationError("Invalid token claims")
                
            return decoded_token
            
        except jwt.InvalidTokenError as e:
            logger.error(f"Invalid JWT token: {e}")
            
            # Try to extract email even from invalid token
            user_email = 'unknown'
            try:
                decoded = jwt.decode(token_str, options={"verify_signature": False})
                user_email = decoded.get('preferred_username') or decoded.get('email') or decoded.get('upn') or 'unknown'
            except Exception:
                pass
            
            # Log SSO/authentication failure
            if self.audit_service:
                try:
                    self.audit_service.log_event(
                        action='failed_auth',
                        user_email=user_email,
                        details={
                            'reason': f'Invalid JWT token format: {str(e)}',
                            'error_type': 'sso_jwt_invalid'
                        },
                        status='failure'
                    )
                except Exception as audit_error:
                    logger.debug(f"Failed to log audit event: {audit_error}")
            
            raise ClientAuthenticationError("Invalid token format")
        except ClientAuthenticationError:
            # Re-raise ClientAuthenticationError as-is (already logged above)
            raise
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            
            # Try to extract email
            user_email = 'unknown'
            try:
                if isinstance(token, str):
                    decoded = jwt.decode(token, options={"verify_signature": False})
                    user_email = decoded.get('preferred_username') or decoded.get('email') or decoded.get('upn') or 'unknown'
            except Exception:
                pass
            
            # Log SSO/authentication failure
            if self.audit_service:
                try:
                    self.audit_service.log_event(
                        action='failed_auth',
                        user_email=user_email,
                        details={
                            'reason': f'Token validation error: {str(e)}',
                            'error_type': 'sso_validation_error'
                        },
                        status='failure'
                    )
                except Exception as audit_error:
                    logger.debug(f"Failed to log audit event: {audit_error}")
            
            raise ClientAuthenticationError("Token validation failed")
    
    async def get_user_credential(self, user_token: str) -> UserTokenCredential:
        """
        Create a credential object using the user's token for Azure API calls
        """
        # Validate the token first
        await self.validate_token(user_token)
        
        # Return credential that will use the user's token
        return UserTokenCredential(user_token)
    
    async def get_on_behalf_of_credential(self, user_token: str) -> UserTokenCredential:
        """
        Create a credential for accessing Azure APIs as the user
        Using direct user token approach since we're using delegated permissions
        """
        try:
            # Validate the token first
            await self.validate_token(user_token)
            
            # Return user token credential
            return UserTokenCredential(user_token)
        except Exception as e:
            logger.error(f"Failed to create user credential: {e}")
            raise ClientAuthenticationError("Failed to create user credential")
    
    def _validate_token_claims(self, token: Dict[str, Any]) -> bool:
        """
        Validate essential token claims using values from the token itself
        """
        try:
            # Check if token has required claims
            required_claims = ['aud', 'iss', 'oid', 'tid', 'exp']
            for claim in required_claims:
                if claim not in token:
                    logger.error(f"Missing required claim: {claim}")
                    return False
            
            # Extract tenant ID from the token
            token_tenant_id = token.get('tid')
            
            # Validate issuer - Azure AD can use different issuer endpoints
            token_issuer = token.get('iss', '')
            valid_issuers = [
                f"https://login.microsoftonline.com/{token_tenant_id}/v2.0",
                f"https://sts.windows.net/{token_tenant_id}/"
            ]
            
            if not any(token_issuer.rstrip('/') == issuer.rstrip('/') for issuer in valid_issuers):
                logger.error(f"Invalid issuer: {token_issuer}. Expected one of: {valid_issuers}")
                return False
            
            # Check if token is expired (basic check - jwt library should handle this)
            import time
            if token.get('exp', 0) < time.time():
                logger.error("Token has expired")
                return False
                
            logger.debug(f"Token validated for tenant: {token_tenant_id}, client: {token.get('aud')}")
            return True
            
        except Exception as e:
            logger.error(f"Token claims validation error: {e}")
            return False
    
    async def get_user_credentials(self, user_token: str) -> DefaultAzureCredential:
        """
        Create Azure credential using user's delegated permissions
        """
        try:
            # In production, use the user's token for on-behalf-of flow
            # For MVP, use DefaultAzureCredential which will use Managed Identity in Azure
            credential = DefaultAzureCredential()
            return credential
        except Exception as e:
            logger.error(f"Failed to create user credentials: {e}")
            raise ClientAuthenticationError("Failed to create Azure credentials")
    
    async def check_subscription_access(self, user_info: Dict[str, Any], subscription_id: str) -> bool:
        """
        Check if user has access to a specific subscription
        """
        try:
            from azure.mgmt.resource import SubscriptionClient
            
            credential = await self.get_user_credentials(user_info.get('token', ''))
            sub_client = SubscriptionClient(credential)
            
            # Try to get the subscription - if successful, user has access
            subscription = sub_client.subscriptions.get(subscription_id)
            return subscription is not None
            
        except Exception as e:
            logger.warning(f"Subscription access check failed for {subscription_id}: {e}")
            return False
    
    async def get_user_permissions(self, user_info: Dict[str, Any], subscription_id: str) -> list:
        """
        Get user's permissions for a specific subscription
        """
        try:
            from azure.mgmt.authorization import AuthorizationManagementClient
            
            credential = await self.get_user_credentials(user_info.get('token', ''))
            auth_client = AuthorizationManagementClient(credential, subscription_id)
            
            # Get role assignments for the user
            user_object_id = user_info.get('oid')
            role_assignments = auth_client.role_assignments.list_for_subscription(
                filter=f"principalId eq '{user_object_id}'"
            )
            
            permissions = []
            for assignment in role_assignments:
                role_definition = auth_client.role_definitions.get_by_id(
                    assignment.role_definition_id
                )
                permissions.append({
                    'role_name': role_definition.role_name,
                    'role_id': assignment.role_definition_id,
                    'scope': assignment.scope
                })
            
            return permissions
            
        except Exception as e:
            logger.error(f"Failed to get user permissions: {e}")
            return []