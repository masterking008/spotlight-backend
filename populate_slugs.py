#!/usr/bin/env python3
"""
Script to populate slug fields for existing casting calls.
Run this after the migration to ensure all existing records have slugs.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import get_db
from models import CastingCall

def populate_slugs():
    """Populate slug fields for all existing casting calls."""
    db = next(get_db())
    
    try:
        # Get all casting calls without slugs
        casting_calls = db.query(CastingCall).filter(
            (CastingCall.show_slug.is_(None)) | 
            (CastingCall.role_slug.is_(None))
        ).all()
        
        print(f"Found {len(casting_calls)} casting calls to update...")
        
        for cc in casting_calls:
            print(f"Updating: {cc.show} - {cc.role}")
            cc.update_slugs()
            
        db.commit()
        print(f"Successfully updated {len(casting_calls)} casting calls with slugs!")
        
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    populate_slugs()