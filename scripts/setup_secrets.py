"""
Helper script to generate secure secrets for local development.

WARNING: This is a development utility only!
- The generated file contains secrets in plain text (by design)
- This script should NEVER be used in production
- The output file is gitignored to prevent accidental commits
- Always delete the generated file after copying secrets to your .env
"""
import secrets


def generate_api_secret() -> str:
    """Generate secure API secret key."""
    return secrets.token_urlsafe(32)


def generate_jwt_secret() -> str:
    """Generate secure JWT secret key."""
    return secrets.token_urlsafe(64)


if __name__ == "__main__":
    import os
    from pathlib import Path
    
    # Generate secrets
    api_secret = generate_api_secret()
    jwt_secret = generate_jwt_secret()
    
    # Write to a temporary file instead of printing to console
    # lgtm[py/clear-text-storage-sensitive-data] - This is a dev utility script that intentionally generates secrets in plain text
    output_file = Path("generated_secrets.env")
    with open(output_file, 'w') as f:
        f.write(f"API_SECRET_KEY={api_secret}\n")  # lgtm[py/clear-text-storage-sensitive-data]
        f.write(f"JWT_SECRET_KEY={jwt_secret}\n")  # lgtm[py/clear-text-storage-sensitive-data]
    
    # Set secure permissions (read/write for owner only)
    if os.name != 'nt':  # Unix-like systems
        output_file.chmod(0o600)
    
    print(f"[OK] Secrets generated and saved to: {output_file}")
    print("=> Copy these values to your .env file")
    print("=> DELETE the generated_secrets.env file immediately after copying")
    print("WARNING: This file contains secrets in plain text!")
    print("WARNING: Do not commit this file to version control!")



