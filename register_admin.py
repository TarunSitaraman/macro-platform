"""Utility script to register a default admin user and tenant."""

import os
from dotenv import load_dotenv

load_dotenv()

from src.database import SessionLocal, User, Tenant
from src.utils.auth import hash_password

def register_admin():
    db = SessionLocal()
    try:
        # Check if tenant exists
        tenant = db.query(Tenant).filter(Tenant.slug == "default-tenant").first()
        if not tenant:
            tenant = Tenant(
                name="Default Tenant",
                slug="default-tenant"
            )
            db.add(tenant)
            db.flush()
            print(f"Created tenant: {tenant.name}")
        
        # Check if user exists
        user = db.query(User).filter(User.email == "admin@example.com").first()
        if not user:
            user = User(
                email="admin@example.com",
                hashed_password=hash_password("admin123"),
                full_name="System Admin",
                tenant_id=tenant.tenant_id,
                role="admin"
            )
            db.add(user)
            db.commit()
            print(f"Created admin user: {user.email}")
        else:
            print(f"Admin user already exists: {user.email}")
    finally:
        db.close()

if __name__ == "__main__":
    register_admin()
