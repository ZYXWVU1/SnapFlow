"""Third-party SDK diagnostics must not bypass SnapFlow payload redaction."""
import logging


def redact_sdk_record(record):
    if record.name == 'client' or record.name.startswith(('mcp.', 'httpx2', 'httpcore2')):
        record.msg = 'MCP/HTTP diagnostic (%s).' % record.levelname.lower()
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
    return True


class _SafeNamespaceFilter(logging.Filter):
    def filter(self, record):
        return redact_sdk_record(record)


class _SafeSDKFilter(logging.Filter):
    def filter(self, record):
        record.msg = 'MCP SDK diagnostic (%s).' % record.levelname.lower()
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


class _SafeHTTPFilter(logging.Filter):
    def filter(self, record):
        # HTTP logs can carry authorization codes in queries and metadata URLs.
        record.msg = 'HTTP transport diagnostic (%s).' % record.levelname.lower()
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def install_safe_sdk_logging():
    names = {'mcp.client.auth.oauth2', 'mcp.client.streamable_http', 'mcp.client.stdio',
        'mcp.client.session', 'mcp.client.client', 'client', 'mcp.shared.jsonrpc_dispatcher',
        'mcp.shared.direct_dispatcher', 'mcp.shared.dispatcher', 'mcp.shared.tool_name_validation'}
    names.update(name for name in logging.Logger.manager.loggerDict if name.startswith(('mcp.', 'httpcore2')))
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, _SafeSDKFilter) for f in logger.filters):
            logger.addFilter(_SafeSDKFilter())
    logger = logging.getLogger('httpx2')
    if not any(isinstance(f, _SafeHTTPFilter) for f in logger.filters):
        logger.addFilter(_SafeHTTPFilter())
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _SafeNamespaceFilter) for f in handler.filters):
            handler.addFilter(_SafeNamespaceFilter())
