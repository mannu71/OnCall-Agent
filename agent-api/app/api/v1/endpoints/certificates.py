"""Certificate management API routes."""
import logging
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form
import aiofiles
import aiofiles.os

router = APIRouter(prefix="/certificates", tags=["certificates"])
logger = logging.getLogger(__name__)

# Certificate storage path
CERTS_DIR = Path("data/certs")


def ensure_certs_dir():
    """Ensure certificates directory exists."""
    CERTS_DIR.mkdir(parents=True, exist_ok=True)


@router.get("", response_model=List[str])
async def list_certificates():
    """List all uploaded certificates.
    
    Returns:
        List of certificate filenames
    """
    ensure_certs_dir()
    certs = []
    for file in CERTS_DIR.iterdir():
        if file.is_file() and file.suffix.lower() in ('.pem', '.crt', '.cer', '.cert'):
            certs.append(file.name)
    return certs


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_certificate(
    file: UploadFile = File(...),
    name: str = Form(None)
):
    """Upload a certificate file.
    
    Args:
        file: Certificate file to upload
        name: Optional custom name for the certificate
        
    Returns:
        Upload result with certificate path
    """
    ensure_certs_dir()
    
    # Validate file type
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided"
        )
    
    # Check file extension
    allowed_extensions = {'.pem', '.crt', '.cer', '.cert'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Determine filename
    filename = name if name else file.filename
    # Sanitize filename
    filename = Path(filename).name  # Remove any path components
    if not Path(filename).suffix:
        filename += file_ext
    
    dest_path = CERTS_DIR / filename
    
    # Save file
    try:
        async with aiofiles.open(dest_path, "wb") as buffer:
            content = await file.read()
            await buffer.write(content)
        logger.info(f"Uploaded certificate: {filename}")
    except Exception as e:
        logger.error(f"Failed to save certificate: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save certificate: {str(e)}"
        )
    finally:
        await file.close()
    
    return {
        "success": True,
        "filename": filename,
        "path": f"/app/data/certs/{filename}",
        "message": f"Certificate '{filename}' uploaded successfully"
    }


@router.delete("/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_certificate(filename: str):
    """Delete a certificate file.
    
    Args:
        filename: Name of the certificate to delete
    """
    ensure_certs_dir()
    
    # Sanitize filename to prevent path traversal
    safe_filename = Path(filename).name
    cert_path = CERTS_DIR / safe_filename
    
    # Ensure we're still in the certs directory
    if cert_path.resolve().parent != CERTS_DIR.resolve():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename"
        )
    
    if not cert_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate '{filename}' not found"
        )
    
    try:
        await aiofiles.os.remove(cert_path)
        logger.info(f"Deleted certificate: {filename}")
    except Exception as e:
        logger.error(f"Failed to delete certificate: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete certificate: {str(e)}"
        )
    
    return None


@router.get("/{filename}/exists")
async def check_certificate_exists(filename: str):
    """Check if a certificate file exists.
    
    Args:
        filename: Name of the certificate to check
        
    Returns:
        Whether the certificate exists
    """
    ensure_certs_dir()
    
    # Sanitize filename
    safe_filename = Path(filename).name
    cert_path = CERTS_DIR / safe_filename
    
    return {
        "exists": cert_path.exists(),
        "filename": safe_filename,
        "path": f"/app/data/certs/{safe_filename}" if cert_path.exists() else None
    }
