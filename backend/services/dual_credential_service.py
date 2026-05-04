"""
Dual Authentication Service
Handles both user tokens (for user-scoped operations) and service principal (for background prefetch)
"""

from azure.identity import ClientSecretCredential
from typing import Dict, Any
import logging
from config import settings

logger = logging.getLogger(__name__)

class DualCredentialService:
    """
    Manages both user and service principal credentials.
    - User credentials: For user-scoped API operations
    - Service principal: For background prefetch operations only
    """
    
    def __init__(self):
        self.settings = settings
        self._service_principal_credential = None
        
    def get_service_principal_credential(self) -> ClientSecretCredential:
        """
        Get service principal credential for background operations.
        Credentials are loaded from environment variables via settings, not hardcoded.
        """
        if not self._service_principal_credential:
            # Validate that all required credentials are present in environment
            if not all([
                self.settings.AZURE_SERVICE_PRINCIPAL_CLIENT_ID,
                self.settings.AZURE_SERVICE_PRINCIPAL_CLIENT_SECRET,
                self.settings.AZURE_SERVICE_PRINCIPAL_TENANT_ID
            ]):
                raise ValueError("Service principal configuration is incomplete. Check environment variables.")
            
            # CWE-798: These values come from environment variables, not hardcoded
            self._service_principal_credential = ClientSecretCredential(
                tenant_id=self.settings.AZURE_SERVICE_PRINCIPAL_TENANT_ID,
                client_id=self.settings.AZURE_SERVICE_PRINCIPAL_CLIENT_ID,
                client_secret=self.settings.AZURE_SERVICE_PRINCIPAL_CLIENT_SECRET
            )
            
        return self._service_principal_credential
    
    def get_user_credential(self, user_data: Dict[str, Any], token_type: str = 'management'):
        """
        Get user credential for user-scoped operations with dual token support.
        
        Args:
            user_data: Dictionary containing user information and tokens
            token_type: Type of token to use - 'management' or 'loganalytics'
                       - 'management': For Azure Resource Manager APIs (Cost, Resource Management)
                       - 'loganalytics': For Log Analytics KQL queries
        
        Returns:
            UserTokenCredential with the appropriate token
        """
        # Extract the appropriate token based on token_type
        if token_type == 'loganalytics':
            # For LA operations, use 'la_token' explicitly
            access_token = user_data.get('la_token')
            if not access_token:
                # Fall back to management token if LA token not available
                access_token = user_data.get('management_token')
                logger.warning(" LA token not found, using management token (may cause issues for KQL queries)")
        else:
            # For management operations, use 'management_token' or fallback to 'token'
            access_token = user_data.get('management_token') or user_data.get('token')
        
        if not access_token:
            logger.error(" No user access token found in user_data")
            logger.error(f"   user_data has {len(user_data)} keys")
            logger.error(f"   Requested token_type: {token_type}")
            logger.error("   Service principal should ONLY be used for background prefetch operations")
            raise ValueError(f"No {token_type} token found in user_data. Cannot use service principal for user-initiated operations.")
        
        # Use user's token credential (implements Azure credential protocol)
        from auth import UserTokenCredential
        logger.debug(f" Using user {token_type} credential for user-scoped operations")
        return UserTokenCredential(access_token)

# Global instance
dual_cred_service = DualCredentialService()