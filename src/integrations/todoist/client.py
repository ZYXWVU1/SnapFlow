"""Fixed Todoist API v1 task operations."""
from datetime import date, datetime


class TodoistProvider:
    def __init__(self, api):
        self.api = api

    def list_projects(self):
        response = self.api.request('GET', 'projects')
        return response.get('results', []) if isinstance(response, dict) else []

    def test_connection(self):
        self.list_projects()
        return True

    def create_task(self, title, *, project_id=None, description='', due_date=None,
                    due_datetime=None, priority=1):
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Task title is required.')
        if project_id is not None and (not isinstance(project_id, str) or not project_id.strip()):
            raise ValueError('Invalid Todoist project.')
        if type(priority) is not int or not 1 <= priority <= 4:
            raise ValueError('Todoist priority must be 1–4.')
        if due_date and due_datetime:
            raise ValueError('Choose a date or datetime, not both.')
        body = {'content': title.strip(), 'description': description, 'priority': priority}
        if project_id:
            body['project_id'] = project_id
        if due_date:
            try:
                body['due_date'] = date.fromisoformat(due_date).isoformat()
            except (TypeError, ValueError):
                raise ValueError('Task due date must be YYYY-MM-DD.') from None
        if due_datetime:
            try:
                parsed = datetime.fromisoformat(due_datetime)
                if parsed.tzinfo is None:
                    raise ValueError()
                body['due_datetime'] = parsed.isoformat()
            except (TypeError, ValueError):
                raise ValueError('Task due datetime needs a timezone.') from None
        return self.api.request('POST', 'tasks', json=body, write=True)
