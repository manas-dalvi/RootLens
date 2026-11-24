from typing import Dict, Type
from app.parsers.app_log_parser import ApplicationLogParser
from app.parsers.base import BaseLogParser
from app.parsers.csv_parser import CSVLogParser
from app.parsers.hdfs_parser import HDFSLogParser
from app.parsers.json_parser import JSONLogParser
from app.parsers.syslog_parser import SyslogParser

_PARSER_REGISTRY: Dict[str, Type[BaseLogParser]] = {
    "csv": CSVLogParser,
    "json": JSONLogParser,
    "syslog": SyslogParser,
    "application_log": ApplicationLogParser,
    "hdfs": HDFSLogParser,
}


def get_parser_for_format(format_type: str) -> BaseLogParser:
    """Return an instantiated parser for the verified ingestion format.
    
    Uses format type validated during the ingestion phase rather than
    speculatively inferring from untrusted raw content.
    """
    key = format_type.lower().strip()
    if key == "unstructured":
        return ApplicationLogParser()
    parser_cls = _PARSER_REGISTRY.get(key)
    if not parser_cls:
        raise ValueError(f"Unsupported format type: '{format_type}'. Supported formats: {list(_PARSER_REGISTRY.keys())}.")
    return parser_cls()

