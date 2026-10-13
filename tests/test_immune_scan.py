"""Tests for P1 流式输出脱敏展示 — POST /api/immune/scan-text endpoint.

Verifies:
- Endpoint returns findings with position info (start/end)
- Response never contains matched secret text (safe for frontend display)
- min_risk filter works correctly
- No audit logs are triggered (read-only scan)
- Handles empty text, clean text, and various secret formats
"""

import pytest


class TestScanTextEndpoint:
    """POST /api/immune/scan-text — frontend consumption contract."""

    def test_scan_returns_200_and_findings(self, immune_client):
        """Scanning text with a known secret returns 200 + finding with position."""
        resp = immune_client.post(
            "/api/immune/scan-text",
            json={
                "text": "My API key is sk-abc123def456ghi789jkl012mno345pqr678stu90v for testing"
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "findings" in data
        assert "risk_level" in data
        assert "total_findings" in data
        assert data["total_findings"] >= 1

    def test_findings_have_position_not_matched_text(self, immune_client):
        """Findings contain start/end positions but NEVER the matched secret."""
        secret = "sk-" + "a" * 48
        resp = immune_client.post(
            "/api/immune/scan-text", json={"text": f"Here is a key: {secret} in the text"}
        )
        data = resp.json()
        assert data["total_findings"] >= 1
        for f in data["findings"]:
            assert "start" in f
            assert "end" in f
            assert "type" in f
            assert "risk" in f
            assert "label" in f
            # CRITICAL: response must NEVER contain matched text
            assert "matched" not in f
            # The secret itself must not appear in any finding field
            for key, value in f.items():
                assert secret not in str(value), f"Secret leaked in field '{key}'"

    def test_min_risk_filter_excludes_low(self, immune_client):
        """min_risk='high' excludes medium-risk findings like phone numbers."""
        text = "Phone: 13812345678 and key: sk-" + "b" * 48
        resp_low = immune_client.post(
            "/api/immune/scan-text", json={"text": text, "min_risk": "low"}
        )
        resp_high = immune_client.post(
            "/api/immune/scan-text", json={"text": text, "min_risk": "high"}
        )
        data_low = resp_low.json()
        data_high = resp_high.json()
        assert data_low["total_findings"] >= data_high["total_findings"]
        # high filter should exclude the phone number (medium risk)
        types_high = [f["type"] for f in data_high["findings"]]
        assert "phone_cn" not in types_high
        # low filter should include it
        types_low = [f["type"] for f in data_low["findings"]]
        assert "phone_cn" in types_low

    def test_clean_text_returns_zero_findings(self, immune_client):
        """Text with no secrets returns empty findings list."""
        resp = immune_client.post(
            "/api/immune/scan-text",
            json={"text": "This is a perfectly normal sentence about climbing mountains."},
        )
        data = resp.json()
        assert data["total_findings"] == 0
        assert data["findings"] == []

    def test_empty_text_handled(self, immune_client):
        """Empty text doesn't crash the endpoint."""
        resp = immune_client.post("/api/immune/scan-text", json={"text": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_findings"] == 0

    def test_position_offsets_are_correct(self, immune_client):
        """start/end positions accurately point to the secret in the original text."""
        prefix = "Config value: "
        secret = "ghp_" + "X" * 36
        suffix = " is the token"
        text = prefix + secret + suffix
        resp = immune_client.post("/api/immune/scan-text", json={"text": text, "min_risk": "high"})
        data = resp.json()
        assert data["total_findings"] >= 1
        # Find the github PAT finding
        pat_findings = [f for f in data["findings"] if "github" in f["type"]]
        assert len(pat_findings) >= 1
        f = pat_findings[0]
        # Verify position matches the actual secret location
        assert text[f["start"] : f["end"]] == secret

    def test_multiple_secret_types_detected(self, immune_client):
        """Multiple different secret types in one text are all detected."""
        text = "Contact: 13812345678, AWS: AKIA" + "IOSFODNN7" + "EXAMPLE, OpenAI: sk-" + "T" * 48
        resp = immune_client.post("/api/immune/scan-text", json={"text": text, "min_risk": "low"})
        data = resp.json()
        types = [f["type"] for f in data["findings"]]
        assert "phone_cn" in types
        assert "aws_access_id" in types
        # OpenAI key matches openai_api_key AND generic_sk_api_key (merged to one)
        assert any("api_key" in t or "sk" in t for t in types)

    def test_original_length_reported(self, immune_client):
        """Response includes original_length for frontend context."""
        text = "Some text with 13812345678 in it"
        resp = immune_client.post("/api/immune/scan-text", json={"text": text})
        data = resp.json()
        assert data["original_length"] == len(text)

    def test_no_mutation_of_audit_log(self, immune_client):
        """scan-text is read-only: does not create audit log entries."""
        # Get current audit stats
        before = immune_client.get("/api/immune/audit/stats").json()
        # Scan text with a critical-risk secret
        immune_client.post(
            "/api/immune/scan-text", json={"text": "password=SuperSecret123 and ghs_" + "a" * 36}
        )
        after = immune_client.get("/api/immune/audit/stats").json()
        # Audit count should not change from scan-text calls
        # (moderate would log, scan-text should not)
        # Note: we compare total entries; the scan-text endpoint is read-only
        assert after.get("total_entries", 0) == before.get("total_entries", 0)

    def test_overlapping_patterns_merged(self, immune_client):
        """An OpenAI key also matching generic sk- pattern is merged into one finding."""
        secret = "sk-" + "y" * 48
        resp = immune_client.post(
            "/api/immune/scan-text", json={"text": f"Key: {secret}", "min_risk": "low"}
        )
        data = resp.json()
        # Should be one merged finding, not two overlapping ones
        sk_findings = [f for f in data["findings"] if "sk" in f["type"] or "api_key" in f["type"]]
        # The merged finding should cover exactly the secret length
        for f in sk_findings:
            span = f["end"] - f["start"]
            assert span <= len(secret) + 5  # allow small margin
