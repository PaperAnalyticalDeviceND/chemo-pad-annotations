// Main JavaScript for ChemoPAD Matcher

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('ChemoPAD Matcher loaded');
});

// Helper functions for future enhancements
function showNotification(message, type = 'info') {
    // Simple alert-based notification for now
    const prefix = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    alert(`${prefix} ${message}`);
}

function confirmAction(message) {
    return confirm(message);
}

// Backup functionality
async function createBackup(event) {
    event.preventDefault();

    const backupBtn = document.getElementById('backup-btn');
    const originalText = backupBtn.textContent;

    // Show loading state
    backupBtn.textContent = '⏳ Creating backup...';
    backupBtn.style.pointerEvents = 'none';

    try {
        const response = await fetch((window.SCRIPT_ROOT || '') + '/api/backup', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        });

        const data = await response.json();

        if (response.ok && data.status === 'success') {
            showNotification(`Backup created successfully: ${data.filename} (${formatBytes(data.size)})`, 'success');
        } else {
            showNotification(data.message || 'Failed to create backup', 'error');
        }
    } catch (error) {
        showNotification('Error creating backup: ' + error.message, 'error');
    } finally {
        // Restore button state
        backupBtn.textContent = originalText;
        backupBtn.style.pointerEvents = 'auto';
    }
}

// Helper function to format bytes
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}