"""
Unit tests for Azure DevOps Configuration Manager

Tests credential storage, encryption, and file operations.
"""

import json
import os
import stat
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.services.azure_config_manager import ConfigManager


@pytest.fixture
def encryption_key():
    """Generate a test encryption key"""
    return Fernet.generate_key().decode()


@pytest.fixture
def temp_config_file():
    """Create a temporary config file"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_path = f.name
    # Delete the file so ConfigManager can initialize it properly
    os.unlink(temp_path)
    yield temp_path
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def config_manager(temp_config_file, encryption_key):
    """Create a ConfigManager instance with temp file"""
    return ConfigManager(config_path=temp_config_file, encryption_key=encryption_key)


class TestConfigManagerInitialization:
    """Test ConfigManager initialization"""
    
    def test_init_with_explicit_key(self, temp_config_file, encryption_key):
        """Test initialization with explicit encryption key"""
        manager = ConfigManager(config_path=temp_config_file, encryption_key=encryption_key)
        assert manager.config_path == Path(temp_config_file)
        assert manager.cipher is not None
    
    def test_init_with_env_key(self, temp_config_file, encryption_key, monkeypatch):
        """Test initialization with encryption key from environment"""
        monkeypatch.setenv("AZURE_DEVOPS_ENCRYPTION_KEY", encryption_key)
        manager = ConfigManager(config_path=temp_config_file)
        assert manager.cipher is not None
    
    def test_init_without_key_raises_error(self, temp_config_file, monkeypatch):
        """Test initialization without encryption key raises ValueError"""
        monkeypatch.delenv("AZURE_DEVOPS_ENCRYPTION_KEY", raising=False)
        with pytest.raises(ValueError, match="Encryption key must be provided"):
            ConfigManager(config_path=temp_config_file)
    
    def test_init_with_invalid_key_raises_error(self, temp_config_file):
        """Test initialization with invalid encryption key raises ValueError"""
        with pytest.raises(ValueError, match="Invalid encryption key format"):
            ConfigManager(config_path=temp_config_file, encryption_key="invalid-key")
    
    def test_init_creates_config_file(self, temp_config_file, encryption_key):
        """Test initialization creates config file if it doesn't exist"""
        # temp_config_file fixture already ensures file doesn't exist
        # Verify it doesn't exist before creating manager
        assert not os.path.exists(temp_config_file)
        
        manager = ConfigManager(config_path=temp_config_file, encryption_key=encryption_key)
        
        # Check file was created
        assert os.path.exists(temp_config_file)
        
        # Check file has correct structure
        with open(temp_config_file, 'r') as f:
            config = json.load(f)
        assert config == {"organizations": {}}
    
    def test_init_creates_config_directory(self, encryption_key):
        """Test initialization creates config directory if it doesn't exist"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "subdir" / "config.json"
            
            manager = ConfigManager(config_path=str(config_path), encryption_key=encryption_key)
            
            assert config_path.exists()
            assert config_path.parent.exists()
    
    @pytest.mark.skipif(os.name == 'nt', reason="Unix permissions test")
    def test_init_sets_file_permissions(self, temp_config_file, encryption_key):
        """Test initialization sets restrictive file permissions on Unix"""
        manager = ConfigManager(config_path=temp_config_file, encryption_key=encryption_key)
        
        # Check file permissions are 600 (owner read/write only)
        file_stat = os.stat(temp_config_file)
        permissions = stat.S_IMODE(file_stat.st_mode)
        expected = stat.S_IRUSR | stat.S_IWUSR  # 0o600
        
        assert permissions == expected


class TestCredentialManagement:
    """Test credential save, get, list, and delete operations"""
    
    def test_save_credentials_new_organization(self, config_manager, temp_config_file):
        """Test saving credentials for a new organization"""
        config_manager.save_credentials(
            organization="my-org",
            pat="my-secret-pat",
            repository_id="repo-guid-123"
        )
        
        # Read config file directly
        with open(temp_config_file, 'r') as f:
            config = json.load(f)
        
        assert "my-org" in config["organizations"]
        org_config = config["organizations"]["my-org"]
        assert "pat_encrypted" in org_config
        assert org_config["repository_id"] == "repo-guid-123"
        assert "created_at" in org_config
        assert "updated_at" in org_config
        
        # Verify PAT is encrypted (not plain text)
        assert org_config["pat_encrypted"] != "my-secret-pat"
    
    def test_save_credentials_update_existing(self, config_manager):
        """Test updating credentials for existing organization preserves created_at"""
        # Save initial credentials
        config_manager.save_credentials(
            organization="my-org",
            pat="old-pat",
            repository_id="old-repo"
        )
        
        # Get created_at timestamp
        creds1 = config_manager._read_config()
        created_at = creds1["organizations"]["my-org"]["created_at"]
        
        # Update credentials
        config_manager.save_credentials(
            organization="my-org",
            pat="new-pat",
            repository_id="new-repo"
        )
        
        # Check created_at is preserved
        creds2 = config_manager._read_config()
        assert creds2["organizations"]["my-org"]["created_at"] == created_at
        assert creds2["organizations"]["my-org"]["repository_id"] == "new-repo"
    
    def test_get_credentials_existing_organization(self, config_manager):
        """Test retrieving credentials for existing organization"""
        # Save credentials
        config_manager.save_credentials(
            organization="my-org",
            pat="my-secret-pat",
            repository_id="repo-guid-123"
        )
        
        # Get credentials
        creds = config_manager.get_credentials("my-org")
        
        assert creds is not None
        assert creds["pat"] == "my-secret-pat"
        assert creds["repository_id"] == "repo-guid-123"
    
    def test_get_credentials_nonexistent_organization(self, config_manager):
        """Test retrieving credentials for non-existent organization returns None"""
        creds = config_manager.get_credentials("nonexistent-org")
        assert creds is None
    
    def test_get_credentials_with_wrong_key(self, temp_config_file, encryption_key):
        """Test retrieving credentials with wrong encryption key returns None"""
        # Save credentials with one key
        manager1 = ConfigManager(config_path=temp_config_file, encryption_key=encryption_key)
        manager1.save_credentials(
            organization="my-org",
            pat="my-secret-pat",
            repository_id="repo-guid-123"
        )
        
        # Try to get credentials with different key
        different_key = Fernet.generate_key().decode()
        manager2 = ConfigManager(config_path=temp_config_file, encryption_key=different_key)
        creds = manager2.get_credentials("my-org")
        
        assert creds is None
    
    def test_list_organizations_empty(self, config_manager):
        """Test listing organizations when none are configured"""
        orgs = config_manager.list_organizations()
        assert orgs == []
    
    def test_list_organizations_multiple(self, config_manager):
        """Test listing multiple configured organizations"""
        # Save credentials for multiple organizations
        config_manager.save_credentials("org1", "pat1", "repo1")
        config_manager.save_credentials("org2", "pat2", "repo2")
        config_manager.save_credentials("org3", "pat3", "repo3")
        
        orgs = config_manager.list_organizations()
        
        assert len(orgs) == 3
        assert "org1" in orgs
        assert "org2" in orgs
        assert "org3" in orgs
    
    def test_delete_credentials_existing_organization(self, config_manager):
        """Test deleting credentials for existing organization"""
        # Save credentials
        config_manager.save_credentials("my-org", "pat", "repo")
        
        # Delete credentials
        result = config_manager.delete_credentials("my-org")
        
        assert result is True
        assert config_manager.get_credentials("my-org") is None
        assert "my-org" not in config_manager.list_organizations()
    
    def test_delete_credentials_nonexistent_organization(self, config_manager):
        """Test deleting credentials for non-existent organization returns False"""
        result = config_manager.delete_credentials("nonexistent-org")
        assert result is False


class TestEncryption:
    """Test encryption and decryption functionality"""
    
    def test_encrypt_decrypt_roundtrip(self, config_manager):
        """Test encrypting and decrypting a PAT"""
        original_pat = "my-secret-pat-12345"
        
        # Encrypt
        encrypted = config_manager._encrypt_pat(original_pat)
        
        # Verify it's encrypted (not plain text)
        assert encrypted != original_pat
        
        # Decrypt
        decrypted = config_manager._decrypt_pat(encrypted)
        
        # Verify roundtrip
        assert decrypted == original_pat
    
    def test_decrypt_with_wrong_key_raises_error(self, temp_config_file):
        """Test decrypting with wrong key raises ValueError"""
        # Encrypt with one key
        key1 = Fernet.generate_key().decode()
        manager1 = ConfigManager(config_path=temp_config_file, encryption_key=key1)
        encrypted = manager1._encrypt_pat("secret-pat")
        
        # Try to decrypt with different key
        key2 = Fernet.generate_key().decode()
        manager2 = ConfigManager(config_path=temp_config_file, encryption_key=key2)
        
        with pytest.raises(ValueError, match="Failed to decrypt PAT"):
            manager2._decrypt_pat(encrypted)
    
    def test_decrypt_invalid_data_raises_error(self, config_manager):
        """Test decrypting invalid data raises ValueError"""
        with pytest.raises(ValueError, match="Failed to decrypt PAT"):
            config_manager._decrypt_pat("invalid-encrypted-data")


class TestFilePermissions:
    """Test file permission handling"""
    
    @pytest.mark.skipif(os.name == 'nt', reason="Unix permissions test")
    def test_file_permissions_after_save(self, config_manager, temp_config_file):
        """Test file permissions are maintained after saving credentials"""
        config_manager.save_credentials("my-org", "pat", "repo")
        
        # Check file permissions are still 600
        file_stat = os.stat(temp_config_file)
        permissions = stat.S_IMODE(file_stat.st_mode)
        expected = stat.S_IRUSR | stat.S_IWUSR  # 0o600
        
        assert permissions == expected
    
    @pytest.mark.skipif(os.name == 'nt', reason="Unix permissions test")
    def test_file_permissions_after_delete(self, config_manager, temp_config_file):
        """Test file permissions are maintained after deleting credentials"""
        config_manager.save_credentials("my-org", "pat", "repo")
        config_manager.delete_credentials("my-org")
        
        # Check file permissions are still 600
        file_stat = os.stat(temp_config_file)
        permissions = stat.S_IMODE(file_stat.st_mode)
        expected = stat.S_IRUSR | stat.S_IWUSR  # 0o600
        
        assert permissions == expected


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_save_credentials_with_special_characters(self, config_manager):
        """Test saving credentials with special characters in PAT"""
        special_pat = "pat-with-!@#$%^&*()_+-=[]{}|;:',.<>?/~`"
        
        config_manager.save_credentials("my-org", special_pat, "repo")
        creds = config_manager.get_credentials("my-org")
        
        assert creds["pat"] == special_pat
    
    def test_save_credentials_with_unicode(self, config_manager):
        """Test saving credentials with unicode characters"""
        unicode_pat = "pat-with-unicode-你好-مرحبا-🔐"
        
        config_manager.save_credentials("my-org", unicode_pat, "repo")
        creds = config_manager.get_credentials("my-org")
        
        assert creds["pat"] == unicode_pat
    
    def test_organization_name_with_special_characters(self, config_manager):
        """Test organization names with special characters"""
        org_name = "my-org-123_test.com"
        
        config_manager.save_credentials(org_name, "pat", "repo")
        creds = config_manager.get_credentials(org_name)
        
        assert creds is not None
        assert org_name in config_manager.list_organizations()
    
    def test_empty_pat(self, config_manager):
        """Test saving empty PAT"""
        config_manager.save_credentials("my-org", "", "repo")
        creds = config_manager.get_credentials("my-org")
        
        assert creds["pat"] == ""
    
    def test_multiple_operations_sequence(self, config_manager):
        """Test sequence of multiple operations"""
        # Save multiple organizations
        config_manager.save_credentials("org1", "pat1", "repo1")
        config_manager.save_credentials("org2", "pat2", "repo2")
        config_manager.save_credentials("org3", "pat3", "repo3")
        
        # Update one
        config_manager.save_credentials("org2", "new-pat2", "new-repo2")
        
        # Delete one
        config_manager.delete_credentials("org3")
        
        # Verify final state
        orgs = config_manager.list_organizations()
        assert len(orgs) == 2
        assert "org1" in orgs
        assert "org2" in orgs
        assert "org3" not in orgs
        
        creds2 = config_manager.get_credentials("org2")
        assert creds2["pat"] == "new-pat2"
        assert creds2["repository_id"] == "new-repo2"
