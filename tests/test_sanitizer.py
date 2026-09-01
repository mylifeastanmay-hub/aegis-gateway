import time
import pytest
from app.services.sanitizer import PIISanitizer, luhn_checksum_valid


def test_luhn_checksum_detection():
    # Valid credit card numbers passing Luhn algorithm (13-19 digits)
    assert luhn_checksum_valid("4532015112830366") is True  # Visa (16 digits)
    assert luhn_checksum_valid("378282246310005") is True   # Amex (15 digits)

    # Invalid Luhn checksums
    assert luhn_checksum_valid("4532015112830367") is False
    assert luhn_checksum_valid("1234567890123456") is False


def test_multiline_pii_and_secrets_scrubbing():
    sanitizer = PIISanitizer()

    multiline_raw_text = """
Line 1: Contact support at support@aegis-gateway.io or admin@security.org.
Line 2: Phone: +1 (555) 019-2834 or international +44-20-7946-0912.
Line 3: Server IP: 10.0.4.155, Gateway IP: 192.168.1.1.
Line 4: Secret Leak! AWS: AKIAIOSFODNN7EXAMPLE, OpenAI: sk-proj-1234567890abcdef1234567890abcdef.
Line 5: JWT Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c.
Line 6: Payment Card: 4532 0151 1283 0366.
    """

    scrubbed, mapping = sanitizer.sanitize(multiline_raw_text)

    # Assert all sensitive entities scrubbed
    assert "support@aegis-gateway.io" not in scrubbed
    assert "admin@security.org" not in scrubbed
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed
    assert "sk-proj-1234567890abcdef1234567890abcdef" not in scrubbed
    assert "10.0.4.155" not in scrubbed
    assert "4532 0151 1283 0366" not in scrubbed

    # Assert surrogate tokens present
    assert "[REDACTED_EMAIL_1]" in scrubbed
    assert "[REDACTED_EMAIL_2]" in scrubbed
    assert "[REDACTED_SECRET_" in scrubbed
    assert "[REDACTED_CREDIT_CARD_1]" in scrubbed
    assert "[REDACTED_IPV4_1]" in scrubbed

    # Assert bidirectional integrity
    restored = sanitizer.restore(scrubbed, mapping)
    assert restored == multiline_raw_text


def test_concurrent_redactions_latency_benchmark():
    sanitizer = PIISanitizer()
    sample_text = (
        "User email: alice@company.com, phone: +1-555-839-2001, "
        "AWS Key: AKIA9876543210123456, Card: 4532-0151-1283-0366."
    )

    start_time = time.perf_counter()

    # Perform 50 sequential/concurrent redactions
    for _ in range(50):
        scrubbed, mapping = sanitizer.sanitize(sample_text)
        _ = sanitizer.restore(scrubbed, mapping)

    total_duration_ms = (time.perf_counter() - start_time) * 1000.0

    # Assert total latency for 50 redactions completes in < 15ms
    assert total_duration_ms < 15.0
