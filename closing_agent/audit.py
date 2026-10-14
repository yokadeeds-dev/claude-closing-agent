"""Contract audit: required fields, signatures, initials, price and deal consistency.

Two reading modes:
  form  - fillable PDFs (AcroForm), the normal output of e-sign tools and form editors.
          Field values and the page each field sits on are read exactly.
  text  - flattened PDFs (e.g. a Google Doc exported to PDF). Values are read from the
          printed "Label: value" lines; underscores mean blank.

The audit is deterministic on purpose: an LLM summarises the findings for the buyer,
but it never decides whether a contract is complete.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from pypdf import PdfReader

from .pricing import PriceMatrix, PricingError, parse_money

BLANK = re.compile(r"^[\s_\-.]*$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y")


@dataclass
class Finding:
    field: str
    label: str
    page: int | None
    issue: str  # missing | invalid | price_mismatch | deal_mismatch | unverifiable
    severity: str  # error | warning
    detail: str = ""


@dataclass
class AuditResult:
    file: str
    template: str
    mode: str
    complete: bool
    findings: list[Finding] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        """Buyer-facing one-liners, e.g. 'Page 3: Customer signature is missing'."""
        out = []
        for f in self.findings:
            where = f"Page {f.page}: " if f.page else ""
            text = {
                "missing": f"{f.label} is missing",
                "invalid": f"{f.label} looks invalid ({f.detail})",
                "price_mismatch": f"price does not match our price list ({f.detail})",
                "deal_mismatch": f"{f.label} differs from the agreed order ({f.detail})",
                "unverifiable": f"{f.label}: {f.detail}",
            }[f.issue]
            out.append(where + text)
        return out


def load_templates(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------- reading ----------

def _read_form(reader: PdfReader) -> tuple[dict[str, str], dict[str, int]]:
    values: dict[str, str] = {}
    pages: dict[str, int] = {}
    for page_no, page in enumerate(reader.pages, start=1):
        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            name = annot.get("/T")
            if name is None and "/Parent" in annot:
                name = annot["/Parent"].get_object().get("/T")
            if name is None:
                continue
            name = str(name)
            pages.setdefault(name, page_no)
            obj = annot if "/V" in annot else annot.get("/Parent", annot).get_object()
            raw = obj.get("/V")
            if raw is None:
                values.setdefault(name, "")
            elif obj.get("/FT") == "/Sig":
                values[name] = "[digital signature]"  # a /Sig field with a value is signed
            else:
                values[name] = str(raw)
    return values, pages


def _read_text(reader: PdfReader, template: dict) -> tuple[dict[str, str], dict[str, int]]:
    labels = {name: spec["label"] for name, spec in template["fields"].items()}
    labels.update(template.get("signatures", {}))
    values: dict[str, str] = {}
    pages: dict[str, int] = {}
    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for name, label in labels.items():
            m = re.search(rf"^{re.escape(label)}:[ \t]*(.*)$", text, re.MULTILINE)
            if m and name not in values:
                values[name] = m.group(1).strip()
                pages[name] = page_no
        init_label = template.get("initials_label", "Initials")
        for p, name in (template.get("initials") or {}).items():
            if int(p) == page_no:
                m = re.search(rf"^{re.escape(init_label)}:[ \t]*(.*)$", text, re.MULTILINE)
                values[name] = m.group(1).strip() if m else ""
                pages[name] = page_no
    return values, pages


# ---------- checks ----------

def _is_blank(v: str | None) -> bool:
    return v is None or bool(BLANK.match(v))


def _check_value(kind: str, value: str) -> str | None:
    """Return an error description, or None when the value is acceptable."""
    if kind == "email" and not EMAIL.match(value):
        return "not an email address"
    if kind == "integer" and not value.strip().isdigit():
        return "not a whole number"
    if kind == "money":
        try:
            parse_money(value)
        except PricingError:
            return "not an amount"
    if kind == "date":
        for fmt in DATE_FORMATS:
            try:
                datetime.strptime(value.strip(), fmt)
                return None
            except ValueError:
                continue
        return "not a date"
    return None


def audit_contract(
    pdf_path: str | Path,
    template_name: str,
    templates: dict,
    matrix: PriceMatrix | None = None,
    deal: dict | None = None,
    tolerance_pct: float = 1.0,
) -> AuditResult:
    if template_name not in templates:
        raise ValueError(f"Unknown contract template {template_name!r}")
    template = templates[template_name]
    reader = PdfReader(str(pdf_path))

    form_values, form_pages = _read_form(reader)
    expected_names = set(template["fields"]) | set(template.get("signatures", {}))
    if expected_names & set(form_values):
        mode, values, pages = "form", form_values, form_pages
    else:
        mode = "text"
        values, pages = _read_text(reader, template)

    findings: list[Finding] = []

    for name, spec in template["fields"].items():
        v = values.get(name)
        if _is_blank(v):
            findings.append(Finding(name, spec["label"], pages.get(name), "missing", "error"))
        elif problem := _check_value(spec.get("check", "text"), v):
            findings.append(Finding(name, spec["label"], pages.get(name), "invalid", "error", problem))

    for name, label in (template.get("signatures") or {}).items():
        v = values.get(name)
        if _is_blank(v):
            findings.append(Finding(name, label, pages.get(name), "missing", "error"))
        elif mode == "text":
            findings.append(Finding(name, label, pages.get(name), "unverifiable", "warning",
                                    "typed signature in a flat PDF - authenticity cannot be verified; "
                                    "route through e-signature"))

    init_label = template.get("initials_label", "Initials")
    for page_no, name in (template.get("initials") or {}).items():
        if _is_blank(values.get(name)):
            findings.append(Finding(name, init_label, int(page_no), "missing", "error"))

    pc = template.get("price_check")
    have_line = pc and not any(_is_blank(values.get(pc[k])) for k in ("sku", "quantity", "term_months"))
    if matrix and have_line:
        sku = values[pc["sku"]].strip()
        try:
            qty, term = int(values[pc["quantity"]]), int(values[pc["term_months"]])
            if not _is_blank(values.get(pc["total_net"])):
                cmp = matrix.compare(sku, qty, term, values[pc["total_net"]], tolerance_pct)
                if not cmp["within_tolerance"]:
                    findings.append(Finding(
                        pc["total_net"], template["fields"][pc["total_net"]]["label"],
                        pages.get(pc["total_net"]), "price_mismatch", "error",
                        f"contract {cmp['offered_total_net']} vs price list {cmp['expected_total_net']}, "
                        f"{cmp['variance_pct']}%"))
        except (ValueError, PricingError) as exc:
            findings.append(Finding(pc["sku"], "Order line", pages.get(pc["sku"]), "invalid", "error", str(exc)))

    if deal and have_line:
        for key, deal_key in (("sku", "sku"), ("quantity", "quantity"), ("term_months", "term_months")):
            contract_v = values.get(pc[key], "").strip()
            if contract_v and contract_v != str(deal[deal_key]):
                findings.append(Finding(
                    pc[key], template["fields"][pc[key]]["label"], pages.get(pc[key]), "deal_mismatch",
                    "error", f"contract says {contract_v}, CRM deal says {deal[deal_key]}"))

    findings.sort(key=lambda f: (f.page or 99, f.severity != "error"))
    complete = not any(f.severity == "error" for f in findings)
    return AuditResult(str(pdf_path), template_name, mode, complete, findings, values)
