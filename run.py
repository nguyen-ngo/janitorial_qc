from app import create_app, db
from app.models import User, Facility, Area, InspectionTemplate, ChecklistItem, Inspection, InspectionResult, Issue
import os

app = create_app(os.getenv('FLASK_ENV') or 'default')

@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Facility': Facility,
        'Area': Area,
        'InspectionTemplate': InspectionTemplate,
        'ChecklistItem': ChecklistItem,
        'Inspection': Inspection,
        'InspectionResult': InspectionResult,
        'Issue': Issue
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
