"""
Database module for ChemoPAD Annotation Matcher
Uses SQLite for reliable concurrent access and data persistence
"""

import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

def get_db_path():
    """Get the database file path"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_dir = os.path.join(base_dir, 'database')

    # Create database directory if it doesn't exist
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)

    return os.path.join(db_dir, 'chemopad.db')

@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(get_db_path(), timeout=30.0)  # 30 second timeout for locks
    conn.row_factory = sqlite3.Row  # Enable column access by name
    conn.execute('PRAGMA journal_mode=WAL')  # Write-Ahead Logging for better concurrency
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Initialize database tables"""
    with get_db() as conn:
        # Create matches table with annot_id as primary key
        conn.execute('''
            CREATE TABLE IF NOT EXISTS matches (
                annot_id INTEGER PRIMARY KEY,
                card_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create notes table with annot_id as primary key
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                annot_id INTEGER PRIMARY KEY,
                note_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create backup table for recovery
        conn.execute('''
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_type TEXT NOT NULL,
                backup_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create invalid_cards table for marking duplicate/problematic cards
        conn.execute('''
            CREATE TABLE IF NOT EXISTS invalid_cards (
                card_id INTEGER PRIMARY KEY,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        logger.info("Database initialized successfully")

def save_match(annot_id, card_id):
    """Save a match to the database"""
    with get_db() as conn:
        if card_id is None:
            # Delete the match
            conn.execute('DELETE FROM matches WHERE annot_id = ?', (annot_id,))
        else:
            # Insert or update the match
            conn.execute('''
                INSERT OR REPLACE INTO matches (annot_id, card_id, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (annot_id, str(card_id)))

        conn.commit()
        logger.info(f"Saved match: annot_id={annot_id}, card_id={card_id}")

def save_note(annot_id, note_text):
    """Save a note to the database"""
    with get_db() as conn:
        if not note_text:
            # Delete the note if empty
            conn.execute('DELETE FROM notes WHERE annot_id = ?', (annot_id,))
        else:
            # Insert or update the note
            conn.execute('''
                INSERT OR REPLACE INTO notes (annot_id, note_text, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (annot_id, note_text))

        conn.commit()
        logger.info(f"Saved note: annot_id={annot_id}")

def get_all_matches():
    """Get all matches as a dictionary"""
    matches = {}
    with get_db() as conn:
        cursor = conn.execute('SELECT annot_id, card_id FROM matches')
        for row in cursor:
            # Keep "no_match" as string, convert others to int if possible
            card_id = row['card_id']
            if card_id != "no_match":
                try:
                    card_id = int(card_id)
                except (ValueError, TypeError):
                    pass
            matches[row['annot_id']] = card_id

    return matches

def get_all_notes():
    """Get all notes as a dictionary"""
    notes = {}
    with get_db() as conn:
        cursor = conn.execute('SELECT annot_id, note_text FROM notes')
        for row in cursor:
            notes[row['annot_id']] = row['note_text']

    return notes

def migrate_from_json():
    """Migrate existing JSON data to database"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    session_dir = os.path.join(base_dir, 'session')

    migrated = False

    # Migrate matches
    matches_file = os.path.join(session_dir, 'matches.json')
    if os.path.exists(matches_file):
        logger.info(f"Migrating matches from {matches_file}")
        try:
            with open(matches_file, 'r') as f:
                matches = json.load(f)

            with get_db() as conn:
                for row_id, card_id in matches.items():
                    conn.execute('''
                        INSERT OR REPLACE INTO matches (row_id, card_id)
                        VALUES (?, ?)
                    ''', (int(row_id), str(card_id)))

                conn.commit()

            # Rename old file to .migrated
            os.rename(matches_file, matches_file + '.migrated')
            logger.info(f"Migrated {len(matches)} matches successfully")
            migrated = True
        except Exception as e:
            logger.error(f"Failed to migrate matches: {e}")

    # Migrate notes
    notes_file = os.path.join(session_dir, 'notes.json')
    if os.path.exists(notes_file):
        logger.info(f"Migrating notes from {notes_file}")
        try:
            with open(notes_file, 'r') as f:
                notes = json.load(f)

            with get_db() as conn:
                for row_id, note_text in notes.items():
                    conn.execute('''
                        INSERT OR REPLACE INTO notes (row_id, note_text)
                        VALUES (?, ?)
                    ''', (int(row_id), note_text))

                conn.commit()

            # Rename old file to .migrated
            os.rename(notes_file, notes_file + '.migrated')
            logger.info(f"Migrated {len(notes)} notes successfully")
            migrated = True
        except Exception as e:
            logger.error(f"Failed to migrate notes: {e}")

    return migrated

def backup_database():
    """Create a backup of current data"""
    matches = get_all_matches()
    notes = get_all_notes()

    backup_data = {
        'matches': matches,
        'notes': notes,
        'timestamp': datetime.now().isoformat()
    }

    with get_db() as conn:
        conn.execute('''
            INSERT INTO backups (backup_type, backup_data)
            VALUES ('automatic', ?)
        ''', (json.dumps(backup_data),))

        # Keep only last 10 backups
        conn.execute('''
            DELETE FROM backups
            WHERE id NOT IN (
                SELECT id FROM backups
                ORDER BY created_at DESC
                LIMIT 10
            )
        ''')

        conn.commit()

    logger.info("Database backup created")

def create_file_backup(backup_type='manual'):
    """Create a physical file backup of the database"""
    import shutil

    # Create backup directory if it doesn't exist
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backup_dir = os.path.join(base_dir, 'database', 'backups')

    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)

    # Generate filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_filename = f"{backup_type}_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    # Copy the database file
    source_db = get_db_path()
    shutil.copy2(source_db, backup_path)

    # Clean up old backups based on type
    cleanup_old_backups(backup_dir, backup_type)

    logger.info(f"File backup created: {backup_filename}")
    return backup_filename, os.path.getsize(backup_path)

def cleanup_old_backups(backup_dir, backup_type):
    """Remove old backups keeping only recent ones based on type"""
    # Define retention policies
    retention_policies = {
        'manual': 5,     # Keep last 5 manual backups
        'auto': 1,       # Keep only 1 auto backup (rolling)
        'export': 3,     # Keep last 3 export backups
        'daily': 7,      # Keep last 7 daily backups
    }

    max_files = retention_policies.get(backup_type, 10)

    # Get all backup files of this type
    pattern = f"{backup_type}_*.db"
    backups = []
    for filename in os.listdir(backup_dir):
        if filename.startswith(f"{backup_type}_") and filename.endswith('.db'):
            filepath = os.path.join(backup_dir, filename)
            backups.append((filepath, filename))

    # Sort by filename (newest first) — the timestamp is embedded in the
    # filename and is always unique. mtime can collide because shutil.copy2
    # preserves the source file's mtime, which would let cleanup delete the
    # backup that was just created.
    backups.sort(key=lambda x: x[1], reverse=True)

    # Remove old backups
    for filepath, _ in backups[max_files:]:
        os.remove(filepath)
        logger.info(f"Removed old backup: {os.path.basename(filepath)}")

def get_backup_info():
    """Get information about existing backups"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backup_dir = os.path.join(base_dir, 'database', 'backups')

    if not os.path.exists(backup_dir):
        return {'backups': [], 'total_size': 0, 'last_backup': None}

    backups = []
    total_size = 0
    last_backup = None

    for filename in os.listdir(backup_dir):
        if filename.endswith('.db'):
            filepath = os.path.join(backup_dir, filename)
            file_stats = os.stat(filepath)
            backup_time = datetime.fromtimestamp(file_stats.st_mtime)

            backups.append({
                'filename': filename,
                'size': file_stats.st_size,
                'created': backup_time.isoformat(),
                'age': (datetime.now() - backup_time).total_seconds()
            })

            total_size += file_stats.st_size

            if last_backup is None or backup_time > last_backup:
                last_backup = backup_time

    # Sort by creation time (newest first)
    backups.sort(key=lambda x: x['created'], reverse=True)

    return {
        'backups': backups[:10],  # Return only last 10
        'total_size': total_size,
        'last_backup': last_backup.isoformat() if last_backup else None,
        'last_backup_age': (datetime.now() - last_backup).total_seconds() if last_backup else None
    }

def get_stats():
    """Get database statistics"""
    with get_db() as conn:
        match_count = conn.execute('SELECT COUNT(*) FROM matches').fetchone()[0]
        note_count = conn.execute('SELECT COUNT(*) FROM notes').fetchone()[0]
        backup_count = conn.execute('SELECT COUNT(*) FROM backups').fetchone()[0]
        invalid_card_count = conn.execute('SELECT COUNT(*) FROM invalid_cards').fetchone()[0]

        return {
            'total_matches': match_count,
            'total_notes': note_count,
            'total_backups': backup_count,
            'total_invalid_cards': invalid_card_count
        }

def mark_card_invalid(card_id, reason=''):
    """Mark a card as invalid/duplicate"""
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO invalid_cards (card_id, reason)
            VALUES (?, ?)
        ''', (card_id, reason))
        conn.commit()
        logger.info(f"Marked card as invalid: card_id={card_id}, reason={reason}")

def unmark_card_invalid(card_id):
    """Remove invalid mark from a card"""
    with get_db() as conn:
        conn.execute('DELETE FROM invalid_cards WHERE card_id = ?', (card_id,))
        conn.commit()
        logger.info(f"Unmarked card as invalid: card_id={card_id}")

def get_all_invalid_cards():
    """Get all invalid cards as a dictionary"""
    invalid_cards = {}
    with get_db() as conn:
        cursor = conn.execute('SELECT card_id, reason FROM invalid_cards')
        for row in cursor:
            invalid_cards[row['card_id']] = row['reason']
    return invalid_cards

def is_card_invalid(card_id):
    """Check if a card is marked as invalid"""
    with get_db() as conn:
        result = conn.execute('SELECT 1 FROM invalid_cards WHERE card_id = ?', (card_id,)).fetchone()
        return result is not None

# Initialize database when module is imported
init_db()

# Try to migrate existing JSON data
if migrate_from_json():
    logger.info("Successfully migrated existing data to database")
    backup_database()  # Create initial backup after migration