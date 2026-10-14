"""Acceptance criterion 3: the audit reliably identifies incomplete sections."""


def issues(services, deal_id):
    return {(f.field, f.issue, f.page) for f in services.audit_deal(deal_id).findings}


def test_complete_contract_passes(services):
    a = services.audit_deal("D-1001")
    assert a.mode == "form" and a.complete and a.findings == []


def test_missing_fields_signature_and_initials_with_pages(services):
    a = services.audit_deal("D-1002")
    assert not a.complete
    assert issues(services, "D-1002") == {
        ("billing_email", "missing", 1),
        ("sig_customer_date", "missing", 3),
        ("sig_customer", "missing", 3),
        ("init_p2", "missing", 2),
    }


def test_price_mismatch_detected(services):
    a = services.audit_deal("D-1003")
    assert not a.complete
    [f] = a.findings
    assert f.issue == "price_mismatch" and "-8.000%" in f.detail


def test_flat_pdf_text_mode(services):
    a = services.audit_deal("D-1004")
    assert a.mode == "text" and not a.complete
    found = issues(services, "D-1004")
    assert ("company_number", "missing", 1) in found
    assert ("sig_provider", "missing", 3) in found
    assert ("init_p3", "missing", 3) in found
    assert ("quantity", "deal_mismatch", 2) in found  # contract says 6, CRM says 8
    assert ("sig_customer", "unverifiable", 3) in found  # typed signature is a warning, not an error


def test_invalid_values_are_reported(services, tmp_path):
    from closing_agent.audit import audit_contract
    from scripts.make_sample_data import make_form_contract

    pdf = tmp_path / "bad.pdf"
    values = {k: "x" for k in ("customer_name", "company_number", "sku", "sig_customer", "sig_provider",
                               "init_p1", "init_p2", "init_p3")}
    values.update(billing_email="not-an-email", start_date="tomorrow", quantity="ten",
                  term_months="12", total_net="100", sig_customer_date="2026-10-01")
    make_form_contract(pdf, values)
    a = audit_contract(pdf, "msa_v3", services.templates)
    invalid = {f.field for f in a.findings if f.issue == "invalid"}
    assert {"billing_email", "start_date", "quantity"} <= invalid


def test_summary_lines_are_buyer_readable(services):
    lines = services.audit_deal("D-1002").summary_lines()
    assert "Page 3: Customer signature is missing" in lines
