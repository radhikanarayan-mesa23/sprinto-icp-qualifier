"""Extraction output schema. Every field is {value, source_url, evidence_quote}; unknown => all null."""

FIELDS = ["company_name", "certifications_found", "product_description", "buyer_type", "funding_stage",
          "open_roles_count", "enterprise_signal", "compliance_pressure_signal"]

_VALUE_TYPES = {
    "company_name": ["string", "null"],
    "certifications_found": ["array", "null"],
    "product_description": ["string", "null"],
    "buyer_type": ["string", "null"],            # business | consumer | both
    "funding_stage": ["string", "null"],
    "open_roles_count": ["integer", "null"],
    "enterprise_signal": ["string", "null"],     # enterprise TIER / plan on pricing
    "compliance_pressure_signal": ["string", "null"],  # enterprise customer logos, security/trust page, open compliance/security role
}


def _field(name):
    value = {"type": _VALUE_TYPES[name]}
    if name == "certifications_found":
        value["items"] = {"type": "string"}
    if name == "buyer_type":
        value["enum"] = ["business", "consumer", "both", None]
    return {
        "type": "object",
        "required": ["value", "source_url", "evidence_quote"],
        "additionalProperties": False,
        "properties": {
            "value": value,
            "source_url": {"type": ["string", "null"]},
            "evidence_quote": {"type": ["string", "null"]},
        },
    }


EXTRACTION_SCHEMA = {
    "type": "object",
    "required": FIELDS,
    "additionalProperties": False,
    "properties": {n: _field(n) for n in FIELDS},
}
