from dataclasses import dataclass

TECHNICAL = "Technical"
ONPAGE = "On-Page"
OFFPAGE = "Off-Page"
CONTENT = "Content (Spelling & Grammar)"
CATEGORIES = [TECHNICAL, ONPAGE, OFFPAGE, CONTENT]

HIGH, MEDIUM, LOW = "High", "Medium", "Low"
SEVERITY_WEIGHT = {HIGH: 10, MEDIUM: 5, LOW: 2}
SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}


@dataclass
class Issue:
    category: str
    severity: str
    code: str          # stable id, used to group the same issue across pages
    title: str
    recommendation: str
    url: str           # "" for site-wide issues
    detail: str = ""


def issue(category, severity, code, title, recommendation, url="", detail=""):
    return Issue(category, severity, code, title, recommendation, url, detail)
