from flask import Blueprint
demo_bp = Blueprint('demo', __name__, url_prefix='/admin/demo')

@demo_bp.route('/ping', methods=['GET', 'POST'])
def ping():
    return 'pong'

@demo_bp.route('/list')
def list_items():
    return []
