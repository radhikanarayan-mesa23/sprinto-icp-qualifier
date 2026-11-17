"""Mock Hunter. Responses are Hunter-shaped (data.emails[], meta.results). No network."""


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def person(first, last, position, confidence=90, seniority="senior", type_="personal", value=None):
    return {"value": value or f"{first.lower()}.{last.lower()}@example.com", "type": type_, "confidence": confidence,
            "first_name": first, "last_name": last, "position": position, "seniority": seniority,
            "department": "it"}


def body(emails, total=None):
    return {"data": {"domain": "example.com", "emails": emails}, "meta": {"results": total if total is not None else len(emails)}}


class FakeHunter:
    """transport(domain). Pops scripted responses per call; records domains searched."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, domain):
        self.calls.append(domain)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r
