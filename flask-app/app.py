from flask import Flask, render_template, jsonify, request, send_file, session, redirect, url_for
import pandas as pd
import json
import os
from datetime import datetime, timedelta
import logging
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
import markdown
import database  # Import our new database module

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'chemopad-secret-key-2024')

# Session configuration
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True if using HTTPS
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)  # 30 minute timeout
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# Get password from environment variable
PASSWORD = os.environ.get('CHEMOPAD_PASSWORD', 'chemopad2024')

# Add proxy fix for nginx
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add after_request handler to prevent caching of dynamic pages
@app.after_request
def add_cache_control(response):
    # Prevent caching for HTML pages (dynamic content)
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session.permanent = True
        app.permanent_session_lifetime = timedelta(minutes=30)

        if 'authenticated' not in session or not session['authenticated']:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Global data storage
annotations_df = None
project_cards_df = None
matches = {}  # {annotation_annot_id: project_card_id}
notes = {}  # Store notes for each annotation  # {annotation_annot_id: project_card_id}

def load_data():
    """Load all CSV data"""
    global annotations_df, project_cards_df

    # Use absolute paths for production
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data')

    # Load annotations (skip missing cards)
    annotations_file = os.path.join(data_dir, 'chemoPAD-annotations-final.csv')
    annotations_df = pd.read_csv(annotations_file)
    annotations_df = annotations_df[annotations_df['missing_card'] != True].copy()
    # No need to create row_id, we'll use annot_id directly

    # Load project cards
    project_cards_file = os.path.join(data_dir, 'project_cards.csv')
    project_cards_df = pd.read_csv(project_cards_file)

    logger.info(f"Loaded {len(annotations_df)} annotations from {annotations_file}")
    logger.info(f"Loaded {len(project_cards_df)} project cards from {project_cards_file}")

    # Load existing matches from database
    global matches
    matches = database.get_all_matches()

    # Load existing notes from database
    global notes
    notes = database.get_all_notes()

    logger.info(f"Loaded {len(matches)} matches and {len(notes)} notes from database")

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page with password authentication"""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == PASSWORD:
            session['authenticated'] = True
            session.permanent = True
            logger.info("User successfully authenticated")
            return redirect(url_for('dashboard'))
        else:
            logger.warning("Failed login attempt")
            return render_template('login.html', error='Invalid password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    logger.info("User logged out")
    return redirect(url_for('login'))

@app.route('/help')
@login_required
def help():
    """Display help page from quick-start.md"""
    try:
        import re
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        help_file = os.path.join(base_dir, 'docs', 'quick-start.md')

        with open(help_file, 'r') as f:
            content = f.read()

        # Convert markdown to HTML
        html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])

        # Rewrite image paths from 'figs/...' to '/static/img/help/...'
        html_content = re.sub(r'src="figs/([^"]+)"', r'src="/static/img/help/\1"', html_content)

        return render_template('help.html', content=html_content)
    except Exception as e:
        logger.error(f"Error loading help content: {e}")
        return render_template('help.html', content='<p>Help content not available</p>')

@app.route('/')
@login_required
def dashboard():
    """API Dashboard - Level 1"""
    # Group by API
    api_stats = []

    for api in annotations_df['API'].unique():
        if pd.isna(api):
            continue

        api_data = annotations_df[annotations_df['API'] == api]
        unique_pads = api_data['PAD#'].unique()

        # Count completed PAD#s (all rows for that PAD# are matched)
        completed_pads = 0
        for pad in unique_pads:
            pad_rows = api_data[api_data['PAD#'] == pad]
            if all(annot_id in matches for annot_id in pad_rows['annot_id']):
                completed_pads += 1

        api_stats.append({
            'name': api,
            'total_pads': len(unique_pads),
            'completed_pads': completed_pads,
            'progress': (completed_pads / len(unique_pads) * 100) if len(unique_pads) > 0 else 0
        })

    # Sort by name
    api_stats.sort(key=lambda x: x['name'])

    # Calculate overall stats for dashboard cards
    stats = {
        'total_annotations': len(annotations_df),
        'total_project_cards': len(project_cards_df),
        'total_matches': len(matches),
        'total_notes': len(notes)
    }

    return render_template('dashboard.html', apis=api_stats, stats=stats)

@app.route('/api/<path:api_name>')
@login_required
def pad_list(api_name):
    """PAD# List for specific API - Level 2"""
    # Reload matches and notes from database to get latest data
    global matches, notes
    matches = database.get_all_matches()
    notes = database.get_all_notes()
    uncertainty = database.get_all_uncertainty()

    api_data = annotations_df[annotations_df['API'] == api_name]

    pad_stats = []
    for pad in api_data['PAD#'].unique():
        pad_rows = api_data[api_data['PAD#'] == pad]
        matched_count = sum(1 for annot_id in pad_rows['annot_id'] if annot_id in matches)

        # Count rows with notes
        notes_count = sum(1 for annot_id in pad_rows['annot_id'] if annot_id in notes)

        # Count matched rows that carry at least one low-confidence attribute flag
        uncertain_rows = sum(1 for annot_id in pad_rows['annot_id']
                             if annot_id in matches and uncertainty.get(annot_id))

        # Get sample name from first row
        sample = pad_rows.iloc[0]['Sample'] if pd.notna(pad_rows.iloc[0]['Sample']) else ''

        # Get candidates info for this PAD
        pad_candidates = project_cards_df[project_cards_df['sample_id'] == pad]
        total_candidates = len(pad_candidates)

        # Count selected candidates and deleted candidates
        selected_candidates = 0
        deleted_candidates = 0
        for idx, candidate in pad_candidates.iterrows():
            if candidate['id'] in matches.values():
                selected_candidates += 1
            if candidate['deleted']:
                deleted_candidates += 1

        pad_stats.append({
            'pad_num': int(pad),
            'sample': sample,
            'total_rows': len(pad_rows),
            'matched_rows': matched_count,
            'notes_count': notes_count,
            'candidates_selected': selected_candidates,
            'candidates_available': total_candidates,
            'candidates_deleted': deleted_candidates,
            'uncertain_rows': uncertain_rows,
            'status': 'complete' if matched_count == len(pad_rows) else
                     'partial' if matched_count > 0 else 'not_started'
        })

    # Sort by PAD#
    pad_stats.sort(key=lambda x: x['pad_num'])

    # Calculate API progress
    completed_pads = sum(1 for p in pad_stats if p['status'] == 'complete')
    api_progress = {
        'total': len(pad_stats),
        'completed': completed_pads,
        'percentage': (completed_pads / len(pad_stats) * 100) if len(pad_stats) > 0 else 0
    }

    return render_template('pad_list.html',
                         api_name=api_name,
                         pads=pad_stats,
                         api_progress=api_progress)

@app.route('/match/<path:api_name>/<int:pad_num>')
@login_required
def match_page(api_name, pad_num):
    """Annotation Matching page - Level 3"""
    # Reload matches and notes from database to get latest data
    global matches, notes
    matches = database.get_all_matches()
    notes = database.get_all_notes()
    uncertainty = database.get_all_uncertainty()

    # Get all annotation rows for this PAD#
    pad_annotations = annotations_df[
        (annotations_df['API'] == api_name) &
        (annotations_df['PAD#'] == pad_num)
    ].copy()

    # Get all project cards for this PAD# (sample_id)
    candidates = project_cards_df[
        project_cards_df['sample_id'] == pad_num
    ].copy()

    # Mark which candidates are already used
    used_ids = set(matches.values())
    candidates['is_used'] = candidates['id'].isin(used_ids)

    # Prepare annotation rows with their matches and notes
    rows_data = []
    for idx, row in pad_annotations.iterrows():
        row_dict = row.to_dict()
        annot_id = int(row['annot_id'])
        matched_id = matches.get(annot_id)
        row_dict['annot_id'] = annot_id  # Include annot_id in the dict
        row_dict['matched_id'] = matched_id if matched_id != "no_match" else None
        row_dict['is_no_match'] = matched_id == "no_match"
        row_dict['notes'] = notes.get(annot_id, '')
        row_dict['uncertain_fields'] = list(uncertainty.get(annot_id, set()))
        rows_data.append(row_dict)

    # Convert candidates to dict for JSON
    candidates_data = candidates.to_dict('records')

    # Calculate progress - count both matched candidates and no_match rows
    matched_count = sum(1 for r in rows_data if r['matched_id'] or r['is_no_match'])

    # Get list of all PAD#s for this API to find next/previous ones
    api_data = annotations_df[annotations_df['API'] == api_name]
    all_pads = sorted(api_data['PAD#'].unique())

    # Find next and previous PAD#s in sequence
    next_pad = None
    prev_pad = None
    try:
        current_idx = all_pads.index(pad_num)
        if current_idx < len(all_pads) - 1:
            next_pad = int(all_pads[current_idx + 1])
        if current_idx > 0:
            prev_pad = int(all_pads[current_idx - 1])
    except (ValueError, IndexError):
        pass

    return render_template('match.html',
                         api_name=api_name,
                         pad_num=pad_num,
                         annotations=rows_data,
                         candidates=candidates_data,
                         matched_count=matched_count,
                         total_rows=len(rows_data),
                         next_pad=next_pad,
                         prev_pad=prev_pad)

@app.route('/match-card/<int:card_id>')
@login_required
def match_card_redirect(card_id):
    """Redirect from card ID to the appropriate matching page"""
    # Find the card in project_cards_df
    card_row = project_cards_df[project_cards_df['id'] == card_id]

    if card_row.empty:
        # Card not found, redirect to dashboard with error
        logger.warning(f"Card ID {card_id} not found")
        return redirect(url_for('dashboard'))

    card = card_row.iloc[0]

    # Get PAD# (sample_id) from the card
    pad_num = card['sample_id']

    # Get API name from sample_name
    sample_name = card['sample_name']
    if pd.notna(sample_name) and '(' in sample_name:
        api_name = sample_name.split('(')[0].strip()
    else:
        api_name = sample_name if pd.notna(sample_name) else 'Unknown'

    # Find if there's a matching annotation for this PAD#
    matching_annotations = annotations_df[
        (annotations_df['API'] == api_name) &
        (annotations_df['PAD#'] == pad_num)
    ]

    if not matching_annotations.empty:
        # Redirect to the matching page for this API/PAD
        return redirect(url_for('match_page', api_name=api_name, pad_num=pad_num))
    else:
        # No matching annotation found, try to find any annotation with this PAD#
        any_pad_annotations = annotations_df[annotations_df['PAD#'] == pad_num]
        if not any_pad_annotations.empty:
            # Use the first API found
            first_api = any_pad_annotations.iloc[0]['API']
            return redirect(url_for('match_page', api_name=first_api, pad_num=pad_num))
        else:
            # No annotations at all for this PAD#, redirect to dashboard
            logger.info(f"No annotations found for PAD# {pad_num}")
            return redirect(url_for('dashboard'))

@app.route('/api/save_match', methods=['POST'])
@login_required
def save_match():
    """Save a match between annotation row and project card"""
    data = request.json
    annot_id = int(data['annot_id'])  # Changed from row_id to annot_id
    card_id = data.get('card_id')
    is_no_match = data.get('is_no_match', False)

    try:
        if card_id and card_id != "no_match":
            card_id = int(card_id)
            # Check if card_id is already used
            current_matches = database.get_all_matches()
            if card_id in current_matches.values():
                return jsonify({'success': False, 'error': 'ID already matched to another annotation'})
            database.save_match(annot_id, card_id)
        elif is_no_match:
            # Mark as no match - no card, so attribute uncertainty no longer applies
            database.save_match(annot_id, "no_match")
            database.clear_uncertainty(annot_id)
        else:
            # Unmatching - delete the entry and any attribute uncertainty flags
            database.save_match(annot_id, None)
            database.clear_uncertainty(annot_id)

        # Reload matches for in-memory cache
        global matches
        matches = database.get_all_matches()

        # Check if this PAD is now complete and create auto-backup
        if card_id or is_no_match:  # Only check completion if we're adding a match, not removing
            # Find the PAD# for this annotation
            annotation = annotations_df[annotations_df['annot_id'] == annot_id]
            if not annotation.empty:
                api_name = annotation.iloc[0]['API']
                pad_num = annotation.iloc[0]['PAD#']

                # Get all annotations for this PAD
                pad_annotations = annotations_df[
                    (annotations_df['API'] == api_name) &
                    (annotations_df['PAD#'] == pad_num)
                ]

                # Check if all annotations for this PAD are now matched
                all_matched = all(annot_id in matches for annot_id in pad_annotations['annot_id'])

                if all_matched:
                    # PAD is complete! Create auto-backup
                    logger.info(f"PAD {pad_num} for API {api_name} is now complete. Creating auto-backup.")
                    database.create_file_backup('auto')

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error saving match: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/save_note', methods=['POST'])
@login_required
def save_note():
    """Save notes for an annotation row"""
    data = request.json
    annot_id = int(data['annot_id'])  # Changed from row_id to annot_id
    note_text = data.get('note', '')

    try:
        database.save_note(annot_id, note_text)

        # Reload notes for in-memory cache
        global notes
        notes = database.get_all_notes()

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error saving note: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/save_uncertainty', methods=['POST'])
@login_required
def save_uncertainty():
    """Flag/unflag a specific attribute of a matched annotation as low-confidence.

    Uncertainty is per-(annot_id, field): it marks one attribute label as a best
    guess without weakening the match itself or the other attributes.
    """
    data = request.json
    annot_id = int(data['annot_id'])
    field = data.get('field')
    uncertain = bool(data.get('uncertain', False))

    if field not in database.UNCERTAINTY_FIELDS:
        return jsonify({'success': False, 'error': f'Unknown field: {field}'})

    try:
        database.set_uncertainty(annot_id, field, uncertain)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error saving uncertainty: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/backup', methods=['POST'])
@login_required
def create_backup():
    """Create a manual backup of the database"""
    try:
        filename, size = database.create_file_backup('manual')
        backup_info = database.get_backup_info()

        return jsonify({
            'status': 'success',
            'filename': filename,
            'size': size,
            'last_backup': backup_info.get('last_backup'),
            'total_backups': len(backup_info.get('backups', []))
        })
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/backup/info')
@login_required
def backup_info():
    """Get information about existing backups"""
    try:
        info = database.get_backup_info()

        # Format the response for the frontend
        if info['last_backup_age']:
            minutes_ago = int(info['last_backup_age'] / 60)
            if minutes_ago < 60:
                info['last_backup_text'] = f"{minutes_ago} minutes ago"
            elif minutes_ago < 1440:
                hours_ago = minutes_ago // 60
                info['last_backup_text'] = f"{hours_ago} hour{'s' if hours_ago > 1 else ''} ago"
            else:
                days_ago = minutes_ago // 1440
                info['last_backup_text'] = f"{days_ago} day{'s' if days_ago > 1 else ''} ago"
        else:
            info['last_backup_text'] = "Never"

        return jsonify(info)
    except Exception as e:
        logger.error(f"Error getting backup info: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/export')
@login_required
def export_data():
    """Export all matched data to CSV"""
    # Reload notes and matches from database to get latest data
    global matches, notes
    matches = database.get_all_matches()
    notes = database.get_all_notes()
    invalid_cards = database.get_all_invalid_cards()
    uncertainty = database.get_all_uncertainty()

    # Create a file backup before export
    database.create_file_backup('export')

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Load ALL annotations including those with missing_card=True
    annotations_file = os.path.join(base_dir, 'data', 'chemoPAD-annotations-final.csv')
    all_annotations_df = pd.read_csv(annotations_file)

    # Create export dataframe - keep original annotation columns + missing_card flag
    original_columns = ['annot_id', 'PAD#', 'Camera', 'Lighting (lightbox, benchtop, benchtop dark)',
                       'black/white background', 'API', 'Sample',
                       'mg concentration (w/w mg/mg or w/v mg/mL)', '% Conc', 'missing_card']

    # Only keep original columns that exist
    keep_columns = [col for col in original_columns if col in all_annotations_df.columns]
    export_df = all_annotations_df[keep_columns].copy()

    # Add matched_id column - map using annot_id directly
    export_df['matched_id'] = None
    for idx in export_df.index:
        annot_id = int(export_df.at[idx, 'annot_id'])
        if annot_id in matches:
            export_df.at[idx, 'matched_id'] = matches[annot_id]

    # Add matched_sample_id right after matched_id
    export_df['matched_sample_id'] = None

    # Add other project_cards fields for matched rows
    project_fields = ['sample_name', 'quantity', 'camera_type_1', 'deleted',
                     'date_of_creation', 'processed_file_location']
    for field in project_fields:
        export_df[f'matched_{field}'] = None

    # Add URL field (will be generated from processed_file_location)
    export_df['matched_url'] = None

    # Add card status (Valid / With Issue) for the matched card, from the issue flags.
    # Only populated when there is a matched db id; left blank for no_match / unreviewed rows.
    export_df['matched_card_status'] = None
    export_df['matched_card_issue'] = None

    # Add per-attribute uncertainty flags (TRUE/FALSE). Only populated for matched rows;
    # marks an attribute label as a low-confidence best guess without weakening the match.
    for fkey in database.UNCERTAINTY_FIELDS:
        export_df[f'{fkey}_uncertain'] = None

    # Add notes column for student observations
    export_df['notes'] = None

    # Fill in project data for matched rows
    for annot_id, card_id in matches.items():
        # Find the row with this annot_id
        matching_rows = export_df[export_df['annot_id'] == annot_id]
        if len(matching_rows) == 0:
            continue  # Skip if annot_id not found in export (shouldn't happen)

        orig_idx = matching_rows.index[0]

        if card_id == "no_match":
            # For no_match annotations, still add the notes
            if annot_id in notes:
                export_df.at[orig_idx, 'notes'] = notes[annot_id]
            export_df.at[orig_idx, 'matched_id'] = "no_match"
        elif card_id in project_cards_df['id'].values:
            card_data = project_cards_df[project_cards_df['id'] == card_id].iloc[0]

            # Add sample_id right after matched_id
            export_df.at[orig_idx, 'matched_sample_id'] = card_data.get('sample_id')

            # Card status from the issue flags: "With Issue" if flagged, else "Valid"
            if int(card_id) in invalid_cards:
                export_df.at[orig_idx, 'matched_card_status'] = 'With Issue'
                export_df.at[orig_idx, 'matched_card_issue'] = invalid_cards[int(card_id)]
            else:
                export_df.at[orig_idx, 'matched_card_status'] = 'Valid'

            # Per-attribute uncertainty for this matched row: TRUE if flagged, else FALSE
            flagged = uncertainty.get(annot_id, set())
            for fkey in database.UNCERTAINTY_FIELDS:
                export_df.at[orig_idx, f'{fkey}_uncertain'] = 'TRUE' if fkey in flagged else 'FALSE'

            # Add other fields
            for field in project_fields:
                if field in card_data:
                    export_df.at[orig_idx, f'matched_{field}'] = card_data[field]

            # Generate URL from processed_file_location
            if 'processed_file_location' in card_data and pd.notna(card_data['processed_file_location']):
                export_df.at[orig_idx, 'matched_url'] = f"https://pad.crc.nd.edu{card_data['processed_file_location']}"

            # Add notes if any
            if annot_id in notes:
                export_df.at[orig_idx, 'notes'] = notes[annot_id]

    # Add notes for rows that have notes but aren't in matches
    # This handles annotations with notes that haven't been matched or marked as no_match yet
    for annot_id, note_text in notes.items():
        if annot_id not in matches:
            # Find the row with this annot_id
            matching_rows = export_df[export_df['annot_id'] == annot_id]
            if len(matching_rows) > 0:
                orig_idx = matching_rows.index[0]
                export_df.at[orig_idx, 'notes'] = note_text

    # Remove processed_file_location (we have URL instead) but keep missing_card column
    export_df = export_df.drop(columns=['matched_processed_file_location'], errors='ignore')

    # Convert ID columns to ensure they export as integers without decimals
    # We need to handle this specially for CSV export
    id_columns = ['annot_id', 'PAD#', 'matched_id', 'matched_sample_id']

    # Store original dtypes for restoration if needed
    for col in id_columns:
        if col in export_df.columns:
            # Convert column to object type first to prevent pandas auto-conversion
            # Handle NaN/None values carefully
            def format_id(x):
                # Check if value is NaN, None, or empty string
                if pd.isna(x) or x is None or (isinstance(x, str) and x == ''):
                    return ''
                # Check if value is "no_match" - special case to preserve
                if x == "no_match":
                    return "no_match"
                # Convert to integer (removing any decimal) then to string
                try:
                    return str(int(float(x)))
                except (ValueError, TypeError):
                    return ''

            export_df[col] = export_df[col].apply(format_id)
            # Force object dtype to ensure strings aren't converted back to float
            export_df[col] = export_df[col].astype('object')

    # Generate filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exports_dir = os.path.join(base_dir, 'exports')

    # Create exports directory if it doesn't exist
    if not os.path.exists(exports_dir):
        os.makedirs(exports_dir)

    filename = os.path.join(exports_dir, f'chemopad_matched_export_{timestamp}.csv')

    # Export to CSV with special handling to preserve integer format
    # Use float_format to prevent .0 decimals, but since we converted to strings, this shouldn't be needed
    export_df.to_csv(filename, index=False, na_rep='')

    return send_file(filename, as_attachment=True, download_name=f'chemopad_export_{timestamp}.csv')

@app.route('/api/stats')
@login_required
def get_stats():
    """Get overall statistics"""
    total_annotations = len(annotations_df)
    matched_annotations = len(matches)

    # Count completed PAD#s
    pad_groups = annotations_df.groupby('PAD#')
    completed_pads = 0
    total_pads = len(pad_groups)

    for pad, group in pad_groups:
        if all(annot_id in matches for annot_id in group['annot_id']):
            completed_pads += 1

    return jsonify({
        'total_annotations': total_annotations,
        'matched_annotations': matched_annotations,
        'total_pads': total_pads,
        'completed_pads': completed_pads,
        'annotation_progress': (matched_annotations / total_annotations * 100) if total_annotations > 0 else 0,
        'pad_progress': (completed_pads / total_pads * 100) if total_pads > 0 else 0
    })

@app.route('/gallery')
@login_required
def gallery():
    """Annotation Review - Quality review of PAD annotations organized by lighting conditions"""
    # Reload matches from database to get latest data
    global matches
    matches = database.get_all_matches()

    # Get optional API filter from URL parameter
    api_filter = request.args.get('api', None)

    # Prepare image data with all annotations
    gallery_data = []

    for idx, row in annotations_df.iterrows():
        annot_id = row['annot_id']
        pad_num = row['PAD#']

        # Get match status
        matched_card_id = matches.get(annot_id, None)
        match_status = 'unmatched' if matched_card_id is None else ('no_match' if matched_card_id == 'no_match' else 'matched')

        # If matched to a card, get the image URL
        image_url = None
        if matched_card_id and matched_card_id != 'no_match':
            # Find the project card
            card = project_cards_df[project_cards_df['id'] == matched_card_id]
            if not card.empty:
                image_path = card.iloc[0]['processed_file_location']
                if pd.notna(image_path):
                    # Convert path to URL
                    image_url = image_path.replace('/var/www/html/', 'https://pad.crc.nd.edu/')

        # Get note if exists
        note = notes.get(annot_id, '')

        gallery_data.append({
            'annot_id': int(annot_id),
            'pad_num': int(pad_num),
            'lighting': row['Lighting (lightbox, benchtop, benchtop dark)'],
            'camera': row['Camera'],
            'background': row['black/white background'],
            'api': row['API'],
            'sample': row['Sample'] if pd.notna(row['Sample']) else '',
            'concentration': row['mg concentration (w/w mg/mg or w/v mg/mL)'] if pd.notna(row['mg concentration (w/w mg/mg or w/v mg/mL)']) else '',
            'match_status': match_status,
            'image_url': image_url,
            'card_id': matched_card_id if matched_card_id and matched_card_id != 'no_match' else None,
            'note': note
        })

    # Group by lighting condition
    lighting_groups = {}
    for item in gallery_data:
        lighting = item['lighting']
        if lighting not in lighting_groups:
            lighting_groups[lighting] = []
        lighting_groups[lighting].append(item)

    # Sort lighting groups for consistent display
    lighting_order = ['lightbox', 'benchtop', 'no light']
    sorted_lighting = sorted(lighting_groups.keys(), key=lambda x: lighting_order.index(x) if x in lighting_order else 999)

    # Get unique values for filters
    unique_cameras = sorted(annotations_df['Camera'].unique())
    unique_apis = sorted(annotations_df['API'].unique())

    return render_template('gallery.html',
                         gallery_data=gallery_data,
                         lighting_groups=lighting_groups,
                         sorted_lighting=sorted_lighting,
                         unique_cameras=unique_cameras,
                         unique_apis=unique_apis,
                         api_filter=api_filter)

@app.route('/cards-gallery')
@login_required
def cards_gallery():
    """Lab Card Inventory - Gallery of all project cards (matched and unmatched)"""
    # Reload matches from database
    global matches
    matches = database.get_all_matches()

    # Get invalid cards
    invalid_cards = database.get_all_invalid_cards()

    # Get optional API filter from URL parameter
    api_filter = request.args.get('api', None)

    # Get all matched card IDs
    matched_card_ids = set([card_id for card_id in matches.values() if card_id != 'no_match'])

    # Prepare ALL cards data (both matched and unmatched)
    cards_data = []

    for idx, row in project_cards_df.iterrows():
        card_id = row['id']

        # Extract API/drug name from sample_name
        sample_name = row['sample_name'] if pd.notna(row['sample_name']) else 'Unknown'
        api = sample_name.split('(')[0].strip() if '(' in sample_name else sample_name

        # Get image URL
        image_url = None
        if pd.notna(row['processed_file_location']):
            image_url = row['processed_file_location'].replace('/var/www/html/', 'https://pad.crc.nd.edu/')

        # Parse camera type
        camera = row['camera_type_1'] if pd.notna(row['camera_type_1']) else 'Unknown'

        # Parse date
        date_created = row['date_of_creation'] if pd.notna(row['date_of_creation']) else ''

        # Check if matched
        is_matched = card_id in matched_card_ids

        # Check if marked as invalid
        is_invalid = card_id in invalid_cards
        invalid_reason = invalid_cards.get(card_id, '') if is_invalid else ''

        # Get PAD ID (sample_id)
        pad_id = row['sample_id'] if pd.notna(row['sample_id']) else 'N/A'

        cards_data.append({
            'card_id': int(card_id),
            'pad_id': pad_id,  # PAD ID from sample_id field
            'api': api,
            'sample_name': sample_name,
            'camera': camera,
            'date': date_created,
            'image_url': image_url,
            'is_matched': is_matched,
            'is_invalid': is_invalid,
            'invalid_reason': invalid_reason,
            'quantity': row['quantity'] if pd.notna(row['quantity']) else None,
            'notes': row['notes'] if pd.notna(row['notes']) else ''
        })

    # Group by API
    api_groups = {}
    for card in cards_data:
        api = card['api']
        if api not in api_groups:
            api_groups[api] = []
        api_groups[api].append(card)

    # Sort API groups alphabetically
    sorted_apis = sorted(api_groups.keys())

    # Get unique cameras for filters
    unique_cameras = sorted(set([card['camera'] for card in cards_data]))

    # Count matched vs unmatched
    total_matched = sum(1 for card in cards_data if card['is_matched'])
    total_unmatched = len(cards_data) - total_matched

    return render_template('cards_gallery.html',
                         cards_data=cards_data,
                         api_groups=api_groups,
                         sorted_apis=sorted_apis,
                         unique_cameras=unique_cameras,
                         api_filter=api_filter,
                         total_cards=len(cards_data),
                         total_matched=total_matched,
                         total_unmatched=total_unmatched)

@app.route('/api/mark-card-invalid', methods=['POST'])
@login_required
def mark_card_invalid_route():
    """Mark a card as invalid/duplicate"""
    data = request.json
    card_id = data.get('card_id')
    reason = data.get('reason', '')

    if not card_id:
        return jsonify({'success': False, 'error': 'card_id required'}), 400

    try:
        database.mark_card_invalid(int(card_id), reason)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error marking card invalid: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/unmark-card-invalid', methods=['POST'])
@login_required
def unmark_card_invalid_route():
    """Remove invalid mark from a card"""
    data = request.json
    card_id = data.get('card_id')

    if not card_id:
        return jsonify({'success': False, 'error': 'card_id required'}), 400

    try:
        database.unmark_card_invalid(int(card_id))
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error unmarking card invalid: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Load data when module is imported (for gunicorn)
try:
    load_data()
    logger.info("Data loaded successfully on module import")
except Exception as e:
    logger.error(f"Failed to load data on module import: {e}")

if __name__ == '__main__':
    # For development only
    app.run(host='0.0.0.0', port=5001, debug=False)