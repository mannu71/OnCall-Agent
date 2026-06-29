"""
Azure DevOps Configuration Manager

This module provides secure credential storage for Azure DevOps Personal Access Tokens (PATs).
Credentials are encrypted using Fernet symmetric encryption and stored in a JSON config file
with restricted permissions.

Requirements: 10.3, 10.4
"""

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel


class AzureDevOpsCredentials(BaseModel):
    """Model for Azure DevOps credentials"""
    pat_encrypted: str
    repository_id: str
    created_at: str
    updated_at: str


class ConfigManager:
    """
    Manages Azure DevOps credentials with encryption.
    
    Stores credentials for multiple organizations in an encrypted config file.
    Uses Fernet symmetric encryption with key from environment variable.
    
    Requirements: 10.3, 10.4
    """
    
    def __init__(self, config_path: Optional[str] = None, encryption_key: Optional[str] = None):
        """
        Initialize ConfigManager.
        
        Args:
            config_path: Path to config file. Defaults to config/azure_devops_credentials.json
            encryption_key: Fernet encryption key. Defaults to AZURE_DEVOPS_ENCRYPTION_KEY env var
            
        Raises:
            ValueError: If encryption key is not provided and not in environment
        """
        # Set config path
        if config_path:
            self.config_path = Path(config_path)
        else:
            # Default to config/azure_devops_credentials.json relative to project root
            project_root = Path(__file__).parent.parent.parent
            self.config_path = project_root / "config" / "azure_devops_credentials.json"
        
        # Get encryption key from parameter or environment
        key = encryption_key or os.environ.get("AZURE_DEVOPS_ENCRYPTION_KEY")
        if not key:
            raise ValueError(
                "Encryption key must be provided via encryption_key parameter "
                "or AZURE_DEVOPS_ENCRYPTION_KEY environment variable"
            )
        
        # Initialize Fernet cipher
        try:
            self.cipher = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise ValueError(f"Invalid encryption key format: {e}")
        
        # Ensure config directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize config file if it doesn't exist
        if not self.config_path.exists():
            self._write_config({"organizations": {}})
            self._set_file_permissions()
    
    def _read_config(self) -> Dict:
        """
        Read and parse config file.
        
        Returns:
            Dict containing config data
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            json.JSONDecodeError: If config file is invalid JSON
        """
        with open(self.config_path, 'r') as f:
            return json.load(f)
    
    def _write_config(self, config: Dict) -> None:
        """
        Write config data to file.
        
        Args:
            config: Config data to write
        """
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Ensure permissions are set after writing
        self._set_file_permissions()
    
    def _set_file_permissions(self) -> None:
        """
        Set restrictive file permissions (600 on Unix).
        
        On Unix systems, sets permissions to owner read/write only.
        On Windows, this is a no-op as Windows uses different permission model.
        
        Requirements: 10.4
        """
        if os.name != 'nt':  # Not Windows
            # Set permissions to 600 (owner read/write only)
            os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)
    
    def _encrypt_pat(self, pat: str) -> str:
        """
        Encrypt a Personal Access Token.
        
        Args:
            pat: Plain text PAT
            
        Returns:
            Encrypted PAT as base64 string
        """
        return self.cipher.encrypt(pat.encode()).decode()
    
    def _decrypt_pat(self, encrypted_pat: str) -> str:
        """
        Decrypt a Personal Access Token.
        
        Args:
            encrypted_pat: Encrypted PAT as base64 string
            
        Returns:
            Decrypted PAT as plain text
            
        Raises:
            InvalidToken: If decryption fails (wrong key or corrupted data)
        """
        try:
            return self.cipher.decrypt(encrypted_pat.encode()).decode()
        except InvalidToken:
            raise ValueError("Failed to decrypt PAT. Invalid encryption key or corrupted data.")
    
    _DEFAULT_KEY = "_default"

    def save_pat(self, pat: str) -> None:
        """
        Save the global Personal Access Token.

        Args:
            pat: Personal Access Token (plain text, will be encrypted)

        Requirements: 10.2, 10.3, 10.4, 10.5
        """
        self.save_credentials(organization=self._DEFAULT_KEY, pat=pat)

    def get_pat(self) -> Optional[str]:
        """
        Get the global Personal Access Token.

        Returns:
            Decrypted PAT string, or None if not configured.

        Requirements: 10.2, 10.3, 10.4, 10.5
        """
        creds = self.get_credentials(self._DEFAULT_KEY)
        return creds["pat"] if creds else None

    def save_credentials(
        self,
        organization: str,
        pat: str,
        repository_id: str = ""
    ) -> None:
        """
        Save or update credentials for an organization.

        Args:
            organization: Azure DevOps organization name
            pat: Personal Access Token (plain text, will be encrypted)
            repository_id: Optional repository GUID (no longer required)

        Requirements: 10.2, 10.3, 10.4, 10.5
        """
        # Read current config
        config = self._read_config()

        # Encrypt PAT
        encrypted_pat = self._encrypt_pat(pat)

        # Get current timestamp
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # Check if organization exists to preserve created_at
        existing = config["organizations"].get(organization)
        created_at = existing["created_at"] if existing else now

        # Update or create organization credentials
        config["organizations"][organization] = {
            "pat_encrypted": encrypted_pat,
            "repository_id": repository_id,
            "created_at": created_at,
            "updated_at": now
        }

        # Write config
        self._write_config(config)
    
    def get_credentials(self, organization: str) -> Optional[Dict[str, str]]:
        """
        Get credentials for an organization.
        
        Args:
            organization: Azure DevOps organization name
            
        Returns:
            Dict with 'pat' and 'repository_id' keys, or None if not found
            
        Requirements: 10.2, 10.3, 10.4, 10.5
        """
        config = self._read_config()
        
        org_config = config["organizations"].get(organization)
        if not org_config:
            return None
        
        # Decrypt PAT
        try:
            pat = self._decrypt_pat(org_config["pat_encrypted"])
        except ValueError:
            # If decryption fails, return None
            return None
        
        return {
            "pat": pat,
            "repository_id": org_config.get("repository_id", "")
        }
    
    def list_organizations(self) -> List[str]:
        """
        List all configured organizations.
        
        Returns:
            List of organization names
            
        Requirements: 10.2, 10.3, 10.4, 10.5
        """
        config = self._read_config()
        return list(config["organizations"].keys())
    
    def delete_credentials(self, organization: str) -> bool:
        """
        Delete credentials for an organization.
        
        Args:
            organization: Azure DevOps organization name
            
        Returns:
            True if credentials were deleted, False if organization not found
            
        Requirements: 10.2, 10.3, 10.4, 10.5
        """
        config = self._read_config()
        
        if organization not in config["organizations"]:
            return False
        
        del config["organizations"][organization]
        self._write_config(config)
        
        return True
