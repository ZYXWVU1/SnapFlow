"""Presentation groups for stable, lazily created page routes."""
NAVIGATION = (
    ('home', 'Home', 'home'),
    ('memory', 'Memory', 'memory'),
    ('automations', 'Automations', 'workflows'),
    ('tools', 'Tools', 'tools'),
    ('settings', 'Settings', 'settings'),
)
AUTOMATION_TABS = (
    ('skills', 'Visual Skills'), ('workflows', 'Workflows'),
    ('desktop_skills', 'Desktop Skills · Beta'), ('evaluation', 'Evaluation'),
    ('history', 'Run History'),
)
CONNECTION_TABS = (('integrations', 'Services'), ('extensions', 'Extensions'))
AUTOMATION_ROUTES = {key for key, _ in AUTOMATION_TABS}
TOOL_ROUTES = {'tools', 'agent', 'integrations', 'extensions'}


def parent_route(page):
    if page in AUTOMATION_ROUTES:
        return 'automations'
    if page in TOOL_ROUTES:
        return 'tools'
    return page
